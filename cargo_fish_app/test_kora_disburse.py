import os
import requests
import uuid
import json
from dotenv import load_dotenv

load_dotenv(r"c:\Users\DELL\Desktop\ANTI\cargo_fish_app\.env")
SECRET_KEY = os.environ.get('KORA_SECRET_KEY')
url = "https://api.korapay.com/merchant/api/v1/transactions/disburse"

payload = {
    "reference": "TEST-WD-" + uuid.uuid4().hex[:8],
    "destination": {
        "type": "bank_account",
        "amount": "1000",
        "currency": "NGN",
        "narration": "Payout",
        "bank_account": {
            "bank": "033",
            "account": "0000000000"
        },
        "customer": {
            "name": "Admin User",
            "email": "admin@example.com"
        }
    }
}

headers = {
    "Authorization": f"Bearer {SECRET_KEY}",
    "Content-Type": "application/json"
}

resp = requests.post(url, json=payload, headers=headers)
print(resp.status_code)
print(json.dumps(resp.json(), indent=2))
