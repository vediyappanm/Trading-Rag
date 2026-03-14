import asyncio
from datetime import datetime, timedelta
import sys


async def compute_hourly_baselines():
    from trading_rag.baselines.service import compute_baseline
    from trading_rag.clients import es_client
    
    symbols = [None]
    
    try:
        result = es_client.execute_esql(
            f"FROM {es_client.get_execution_logs_index()} | STATS symbols = SINGLETON(symbol)",
            {}
        )
        if result.get("values"):
            for row in result["values"]:
                if row and row[0]:
                    symbols.append(row[0])
    except Exception:
        pass
    
    hours = list(range(24))
    
    for symbol in symbols:
        for hour in hours:
            print(f"Computing baseline for symbol={symbol}, hour={hour}...")
            baseline = compute_baseline(symbol, hour)
            if baseline:
                print(f"  Success: avg_latency={baseline.avg_latency_ms}, avg_volume={baseline.avg_volume}")
            else:
                print(f"  No data found")


def main():
    print(f"Starting baseline computation at {datetime.utcnow().isoformat()}")
    asyncio.run(compute_hourly_baselines())
    print("Baseline computation complete")


if __name__ == "__main__":
    main()
