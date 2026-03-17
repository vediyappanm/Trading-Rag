"""
Continuous Journal Log Poller
------------------------------
Watches the Noren OMS journal file for new lines and bulk-indexes them
into Elasticsearch. Designed to run as a long-lived background service.

Usage:
    python -m trading_rag.ingest.poller --file /path/to/Journal.log.txt
    python -m trading_rag.ingest.poller --file /path/to/Journal.log.txt --interval 5

The poller tracks its last byte-offset in Redis so it survives restarts
without re-indexing already-processed records.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from elasticsearch.helpers import bulk
from apscheduler.schedulers.blocking import BlockingScheduler

from trading_rag.clients import es_client, redis_client
from trading_rag.config import settings

logger = logging.getLogger(__name__)

# Redis key prefix for tracking file offsets
_OFFSET_KEY_PREFIX = "ingest:journal:offset:"

# OrdStatus int codes → human-readable
_STATUS_MAP = {48: "FILLED", 65: "OPEN", 110: "NEW", 67: "CANCELLED",
               56: "REJECTED", 98: "AMO", 50: "PARTIAL", 52: "REPLACED"}


def _get_last_offset(file_path: str) -> int:
    try:
        val = redis_client.client.get(f"{_OFFSET_KEY_PREFIX}{file_path}")
        return int(val) if val else 0
    except Exception:
        return 0


def _set_last_offset(file_path: str, offset: int) -> None:
    try:
        redis_client.client.set(f"{_OFFSET_KEY_PREFIX}{file_path}", str(offset))
    except Exception as e:
        logger.warning(f"Could not save offset to Redis: {e}")


def _build_doc(raw: dict) -> dict:
    """Transform a raw Noren journal record into an ES-ready document."""
    doc = dict(raw)

    # Convert NorenTimeStamp (Unix epoch) to ISO @timestamp
    ts = doc.get("NorenTimeStamp")
    if ts:
        doc["@timestamp"] = datetime.utcfromtimestamp(ts).isoformat()

    # Add base ticker (RELIANCE-EQ -> RELIANCE, NIFTY27JAN26F -> NIFTY)
    trading_symbol = doc.get("TradingSymbol", "")
    doc["ticker"] = trading_symbol.split("-")[0] if "-" in trading_symbol else (
        # For derivatives like NIFTY27JAN26F strip trailing date/expiry
        "".join(c for c in trading_symbol if c.isalpha()) or trading_symbol
    )

    # Add human-readable status label
    doc["ord_status_label"] = _STATUS_MAP.get(doc.get("OrdStatus"), "UNKNOWN")

    # Convert PriceToFill from paise to rupees as a float field
    price_paise = doc.get("PriceToFill")
    if price_paise is not None:
        doc["price_rs"] = round(price_paise / 100, 2)

    # Strip PII — PAN card number must never be indexed
    doc.pop("PanNum", None)

    return doc


def _generate_actions(lines: list[str]):
    index = settings.elasticsearch.execution_logs_index
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        try:
            raw = json.loads(clean)
            doc = _build_doc(raw)
            yield {"_index": index, "_source": doc}
        except (json.JSONDecodeError, Exception):
            pass


def poll_once(file_path: str) -> int:
    """
    Read new lines from file_path since last offset, index them.
    Returns number of new records indexed.
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"Journal file not found: {file_path}")
        return 0

    current_size = path.stat().st_size
    last_offset = _get_last_offset(file_path)

    if current_size < last_offset:
        # File was rotated / truncated — reset to beginning
        logger.info(f"File size decreased (rotation detected). Resetting offset.")
        last_offset = 0

    if current_size <= last_offset:
        logger.debug(f"No new data in {file_path} (size={current_size}, offset={last_offset})")
        return 0

    new_bytes = current_size - last_offset
    logger.info(f"New data: {new_bytes:,} bytes | file={path.name}")

    new_lines: list[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(last_offset)
        new_lines = f.readlines()

    if not new_lines:
        return 0

    try:
        success, failed = bulk(
            es_client.client,
            _generate_actions(new_lines),
            chunk_size=200,
            raise_on_error=False,
            request_timeout=60,
        )
        failed_count = len(failed) if failed else 0
        logger.info(f"Indexed {success:,} records | failed: {failed_count}")
        _set_last_offset(file_path, current_size)
        return success
    except Exception as e:
        logger.error(f"Bulk indexing error: {e}")
        return 0


def start_poller(file_path: str, interval_minutes: int = 5) -> None:
    """Start the blocking scheduler that polls on a fixed interval."""
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        poll_once,
        trigger="interval",
        minutes=interval_minutes,
        args=[file_path],
        id="journal_poller",
        max_instances=1,
        replace_existing=True,
    )

    logger.info(f"Poller starting | file={file_path} | interval={interval_minutes}m")

    # Run immediately on startup so we don't wait for first interval
    count = poll_once(file_path)
    logger.info(f"Initial poll complete: {count:,} records indexed")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Poller stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="Noren Journal Log Poller")
    parser.add_argument("--file", required=True, help="Path to journal log file")
    parser.add_argument("--interval", type=int, default=5,
                        help="Poll interval in minutes (default: 5)")
    args = parser.parse_args()
    start_poller(args.file, args.interval)
