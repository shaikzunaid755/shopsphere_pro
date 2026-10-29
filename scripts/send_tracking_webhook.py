# pretends to be the courier and sends signed tracking updates, for testing Module 34
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.database import SessionLocal
from app.models import ShippingProvider
from app.routers.tracking import sign_tracking_payload

code, tracking = sys.argv[1].upper(), sys.argv[2]
db = SessionLocal()
provider = db.query(ShippingProvider).filter(ShippingProvider.code == code).first()
db.close()
URL = f"http://127.0.0.1:8000/tracking/webhooks/{code}"
run = int(time.time())


def send(label, payload, good_signature=True):
    body = json.dumps(payload).encode()
    sig = sign_tracking_payload(provider, body) if good_signature else f"t={int(time.time())},v1=fake"
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Signature": sig})
    try:
        with urllib.request.urlopen(req) as r:
            print(f"{label:28} -> {r.status} {r.read().decode()}")
    except urllib.error.HTTPError as e:
        print(f"{label:28} -> {e.code} {e.read().decode()}")


def event(n, status, location=None, description=None):
    return {"event_id": f"{run}-{n}", "tracking_number": tracking, "status": status,
            "location": location, "description": description}


send("1) fake signature", event(1, "IN TRANSIT"), good_signature=False)
send("2) in transit", event(1, "IN TRANSIT", "Hyderabad hub", "Bag scanned at hub"))
send("3) same event again", event(1, "IN TRANSIT", "Hyderabad hub"))
send("4) out for delivery", event(2, "OFD", "Kukatpally", "Out with delivery agent"))
send("5) undelivered (exception)", event(3, "UNDELIVERED", "Kukatpally", "Customer not available"))
