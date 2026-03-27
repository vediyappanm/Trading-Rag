import asyncio
from datetime import datetime
from infra_rag.config import settings


async def compute_hourly_baselines():
    from infra_rag.baselines.service import compute_baseline
    from infra_rag.clients import es_client

    index = settings.elasticsearch.metrics_index
    targets = [None]

    try:
        result = es_client.execute_esql(
            f'FROM "{index}" | STATS hosts = COUNT_DISTINCT(host.name)',
            {},
        )
        # Try to get distinct hostnames
        host_result = es_client.execute_esql(
            f'FROM "{index}" | STATS c = COUNT() BY host.name | LIMIT 50',
            {},
        )
        if host_result.get("values"):
            cols = [c["name"] for c in host_result.get("columns", [])]
            host_idx = cols.index("host.name") if "host.name" in cols else None
            if host_idx is not None:
                for row in host_result["values"]:
                    if row and row[host_idx]:
                        targets.append(row[host_idx])
    except Exception:
        pass

    hours = list(range(24))

    for target in targets:
        for hour in hours:
            print(f"Computing baseline for target={target}, hour={hour}...")
            baseline = compute_baseline(target, hour)
            if baseline:
                print(f"  Success: cpu={baseline.avg_cpu_pct}, memory={baseline.avg_memory_pct}")
            else:
                print(f"  No data found")


def main():
    print(f"Starting baseline computation at {datetime.utcnow().isoformat()}")
    asyncio.run(compute_hourly_baselines())
    print("Baseline computation complete")


if __name__ == "__main__":
    main()
