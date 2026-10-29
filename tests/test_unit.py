"""unit tests - small pieces of business logic, no http calls"""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.core.security import hash_password
from app.core.utils import make_number, money
from app.routers.questions import check_content
from app.services import export_service as ex


def test_money_rounds_half_up_to_two_places():
    assert money("10.005") == Decimal("10.01")
    assert money(12766.944) == Decimal("12766.94")
    assert money(None) == Decimal("0.00")


def test_make_number_has_prefix_and_is_unique():
    a, b = make_number("ORD"), make_number("ORD")
    assert a.startswith("ORD-") and b.startswith("ORD-")
    assert a != b


def test_password_is_hashed_with_salt():
    h1, h2 = hash_password("Password@123"), hash_password("Password@123")
    assert "Password@123" not in h1
    assert h1 != h2


def test_qa_filter_blocks_abuse_phone_and_links():
    assert check_content("Does this  phone support 25W charging?") == "Does this phone support 25W charging?"
    for text in ("Is this stupid phone waterproof?", "call me on 9876543210", "cheaper on www.other.com"):
        with pytest.raises(HTTPException) as e:
            check_content(text)
        assert e.value.status_code == 400


REPORT = {"title": "Sales report", "columns": ["date", "orders", "sales", "low_stock"],
          "rows": [{"date": "2026-10-28", "orders": 2, "sales": Decimal("2500.50"), "low_stock": True}],
          "totals": {"sales": Decimal("2500.50")}, "count": 1}


def test_csv_export_has_header_rows_and_totals():
    text = ex.to_csv(REPORT).decode("utf-8-sig").splitlines()
    assert text[0] == "Date,Orders,Sales,Low Stock"
    assert text[1] == "2026-10-28,2,2500.5,yes"
    assert text[-1] == "Sales,2500.5"


def test_excel_and_pdf_exports_are_real_files():
    assert ex.to_excel(REPORT)[:2] == b"PK"        # xlsx is a zip file
    assert ex.to_pdf(REPORT).startswith(b"%PDF")
