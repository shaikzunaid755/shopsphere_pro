# sends a signed sandbox webhook to the running server, for testing Module 29
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.payment_gateway import sandbox_signature

URL = "http://127.0.0.1:8000/payments/webhook"


def send(body, signature):
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Stripe-Signature": signature})
    try:
        with urllib.request.urlopen(req) as r:
            print(r.status, r.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())


ref = sys.argv[1]
event_type = sys.argv[2] if len(sys.argv) > 2 else "payment_intent.succeeded"
body = json.dumps({"id": f"evt_test_{int(time.time())}", "type": event_type,
                   "payment_reference": ref}).encode()

print("1) wrong signature  ->", end=" "); send(body, f"t={int(time.time())},v1=wrongsignature")
print("2) signed event     ->", end=" "); send(body, sandbox_signature(body))
print("3) same event again ->", end=" "); send(body, sandbox_signature(body))
