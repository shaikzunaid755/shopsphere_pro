import json
import random
import string
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP


def utcnow():
    # naive utc time, mysql DATETIME doesn't store tz anyway
    return datetime.now(timezone.utc).replace(tzinfo=None)


def money(value) -> Decimal:
    if value is None:
        value = 0
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def make_number(prefix: str) -> str:
    # e.g. ORD-20261023-K7P2QX
    stamp = utcnow().strftime("%Y%m%d")
    tail = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{stamp}-{tail}"


def slugify(text: str) -> str:
    text = text.lower().strip()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def to_json(data) -> str:
    return json.dumps(data, default=str)


def from_json(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None