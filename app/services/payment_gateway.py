# Payment gateway wrapper.
# If STRIPE_SECRET_KEY (sk_test_...) is in .env we use real Stripe test mode.
# If not, a small local "sandbox" is used so everything works offline:
# payments get fake ids and webhooks are signed with SANDBOX_WEBHOOK_SECRET (HMAC-SHA256).
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal

from fastapi import HTTPException

from app.core.config import settings


def using_stripe():
    return bool(settings.STRIPE_SECRET_KEY)


def _to_paise(amount):
    # stripe wants the smallest unit, 499.50 rupees -> 49950 paise
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))

def create_payment_intent(amount, order_number, idempotency_key):
    if using_stripe():
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            intent = stripe.PaymentIntent.create(
                amount=_to_paise(amount),
                currency=settings.CURRENCY,
                metadata={"order_number": order_number},
                automatic_payment_methods={"enabled": True},
                idempotency_key=idempotency_key,
            )
        except stripe.StripeError as e:
            raise HTTPException(502, f"Stripe error: {e.user_message or str(e)}")
        return "stripe", intent.id, intent.client_secret

    # sandbox: fake ids that look like stripe ones
    ref = "pi_sandbox_" + uuid.uuid4().hex[:20]
    return "sandbox", ref, ref + "_secret_" + uuid.uuid4().hex[:10]

def refund_payment(gateway_reference, amount):
    real_stripe_payment = (gateway_reference and gateway_reference.startswith("pi_")
                           and not gateway_reference.startswith("pi_sandbox_"))
    if using_stripe() and real_stripe_payment:
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            r = stripe.Refund.create(payment_intent=gateway_reference, amount=_to_paise(amount))
        except stripe.StripeError as e:
            raise HTTPException(502, f"Stripe refund failed: {e.user_message or str(e)}")
        return r.id, r.status
    # sandbox refunds always succeed
    return "re_sandbox_" + uuid.uuid4().hex[:20], "succeeded"

def sandbox_signature(body: bytes, timestamp: str = None):
    # same format as stripe:  t=<timestamp>,v1=<hmac sha256>
    timestamp = timestamp or str(int(time.time()))
    signed = f"{timestamp}.".encode() + body
    sig = hmac.new(settings.SANDBOX_WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


def verify_webhook(body: bytes, signature_header: str):
    """returns a dict {id, type, payment_reference, failure_reason}"""
    if not signature_header:
        raise HTTPException(400, "Missing signature header")

    if using_stripe() and settings.STRIPE_WEBHOOK_SECRET:
        import stripe
        try:
            event = stripe.Webhook.construct_event(body, signature_header, settings.STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.SignatureVerificationError):
            raise HTTPException(400, "Invalid webhook signature")
        obj = event["data"]["object"]
        failure = obj["last_payment_error"].get("message") if obj.get("last_payment_error") else None
        return {"id": event["id"], "type": event["type"], "payment_reference": obj.get("id"),
                "failure_reason": failure}

    # sandbox check, same idea as stripe
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts, given = parts["t"], parts["v1"]
    except (ValueError, KeyError):
        raise HTTPException(400, "Bad signature format")
    if abs(time.time() - int(ts)) > 300:
        raise HTTPException(400, "Webhook timestamp too old")   # stops replaying old calls
    expected = sandbox_signature(body, ts).split("v1=")[1]
    if not hmac.compare_digest(expected, given):
        raise HTTPException(400, "Invalid webhook signature")

    data = json.loads(body)
    return {"id": data.get("id"), "type": data.get("type"),
            "payment_reference": data.get("payment_reference"),
            "failure_reason": data.get("failure_reason")}