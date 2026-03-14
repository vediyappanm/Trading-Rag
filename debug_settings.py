from trading_rag.config import settings
import json

print("--- Settings Debug ---")
print(f"ES Host: {settings.elasticsearch.host}")
print(f"ES Username: {settings.elasticsearch.username}")
print(f"ES Password: {'*' * len(settings.elasticsearch.password)}")
print(f"ES Verify Certs: {settings.elasticsearch.verify_certs}")
print("----------------------")
