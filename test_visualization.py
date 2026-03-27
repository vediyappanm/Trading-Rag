import asyncio
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from infra_rag.visualization.agent import create_adhoc_dashboard
from infra_rag.models import QueryType, QueryDomain

async def test_viz():
    print("=== Testing Instant Visualization ===")
    question = "Visualize CPU and Memory for host fs-le-isv"
    target = "fs-le-isv"
    
    try:
        result = create_adhoc_dashboard(
            question=question,
            query_type=QueryType.CPU_SPIKE,
            domain=QueryDomain.INFRA_METRICS,
            target=target
        )
        
        if result.get("created"):
            print(f"SUCCESS: Dashboard created instantly!")
            print(f"Title: {result['title']}")
            print(f"URL: {result['url']}")
            print(f"Panels: {result['panels']}")
        else:
            print(f"FAILED: Could not create dashboard.")
            print(f"Error: {result.get('error')}")
            if "json" in result:
                print("Generated JSON was:")
                import json
                print(json.dumps(result['json'], indent=2)[:500] + "...")
                
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_viz())
