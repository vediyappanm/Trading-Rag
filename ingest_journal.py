import json
import logging
import argparse
from elasticsearch import Elasticsearch, helpers
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def ingest_journal_logs(file_path, es_url, api_key=None, user=None, password=None):
    # Connect to Elasticsearch
    if api_key:
        es = Elasticsearch(es_url, api_key=api_key, verify_certs=False)
    elif user and password:
        es = Elasticsearch(es_url, basic_auth=(user, password), verify_certs=False)
    else:
        es = Elasticsearch(es_url, verify_certs=False)

    index_name = "trading-execution-logs"
    
    def generate_actions():
        with open(file_path, 'r') as f:
            for i, line in enumerate(f):
                clean_line = line.strip()
                if not clean_line: continue
                try:
                    doc = json.loads(clean_line)
                    # Convert NorenTimeStamp to ISO format for Elasticsearch
                    if 'NorenTimeStamp' in doc:
                        doc['@timestamp'] = datetime.fromtimestamp(doc['NorenTimeStamp']).isoformat()
                    
                    # Add some search-friendly fields
                    doc['ticker'] = doc.get('TradingSymbol', '').split('-')[0]
                    
                    yield {
                        "_index": index_name,
                        "_source": doc
                    }
                except Exception as e:
                    logger.error(f"Error parsing line {i}: {e}")
                
                if i % 1000 == 0:
                    logger.info(f"Buffered {i} lines...")

    try:
        success, failed = helpers.bulk(es, generate_actions(), chunk_size=500, raise_on_error=False)
        logger.info(f"Successfully indexed {success} documents. Failed: {len(failed)}")
    except Exception as e:
        logger.error(f"Bulk indexing failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--url", default="https://173.249.2.23:9200")
    parser.add_argument("--user", default="elastic")
    parser.add_argument("--password", default="kNWuqeumJPWRDiWVF-Ak")
    args = parser.parse_args()
    
    ingest_journal_logs(args.file, args.url, user=args.user, password=args.password)
