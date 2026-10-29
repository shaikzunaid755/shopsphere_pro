# ShopSphere Pro - E-commerce Backend

Backend for an online store built with **FastAPI + MySQL + SQLAlchemy + Alembic**.
JWT login, role based access, products, inventory across warehouses, cart and checkout,
online (Stripe test mode / sandbox) and COD payments, shipping, returns, exchanges,
reviews, support tickets, notifications, suppliers and purchase orders, reports and exports.

Backend only - no frontend. All 50 modules are in Swagger, grouped by module number.

## Tech stack

| Part | Used |
|---|---|
| Framework | FastAPI, Pydantic v2 |
| Database | MySQL 8 + SQLAlchemy 2, migrations with Alembic |
| Auth | JWT access + refresh tokens, bcrypt passwords, role based permissions |
| Payments | Stripe test mode, or the built-in sandbox gateway when no Stripe key is set |
| Reports | openpyxl (Excel), reportlab (PDF), csv |
| Background work | FastAPI BackgroundTasks + APScheduler |
| Tests | pytest + FastAPI TestClient (separate SQLite test database) |

## Project structure

```
app/
  core/        config, database, security (JWT), roles, deps, audit, utils
  models/      SQLAlchemy models
  schemas/     Pydantic request / response models
  services/    business logic (inventory, pricing, orders, payments, notifications, reports, exports)
  routers/     API endpoints, one file per module (or group of modules)
  main.py      app + router registration
alembic/       migrations
scripts/       seed.py (sample data), export_postman.py, send_tracking_webhook.py
tests/         unit, integration, role and workflow tests
postman/       Postman collection
```

## Setup (Windows / PowerShell)

Needs Python 3.11+ and MySQL 8.

```powershell
git clone https://github.com/shaikzunaid755/shopsphere_pro.git
cd shopsphere_pro
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create the database in MySQL:

```sql
CREATE DATABASE shopsphere_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Copy the env file and put your own MySQL password in it:

```powershell
Copy-Item .env.example .env
notepad .env
```

Create the tables and load sample data:

```powershell
alembic upgrade head
python -m scripts.seed
```

Run the server:

```powershell
uvicorn app.main:app --reload
```

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

## Sample logins (from the seed script)

All seeded users have the password `Password@123`.
Customers log in with `POST /auth/login`, staff with `POST /auth/staff/login`.

| Role | Email |
|---|---|
| Super admin | superadmin@shopsphere.com |
| Admin | admin@shopsphere.com |
| Product manager | product@shopsphere.com |
| Inventory manager | inventory@shopsphere.com |
| Order manager | orders@shopsphere.com |
| Support agent | support@shopsphere.com |
| Delivery manager | delivery@shopsphere.com |
| Finance manager | finance@shopsphere.com |
| Customer | rahul@example.com, anjali@example.com |

In Swagger click **Authorize** and paste the `access_token`.

## Payments (test mode only)

- No `STRIPE_SECRET_KEY` in `.env` -> the **sandbox gateway** is used. Confirm a payment with
  `POST /payments/{id}/sandbox-confirm` (`{"result": "success"}` or `"fail"`).
- With Stripe **test** keys (`sk_test_...`) payments go to Stripe test mode and the webhook
  `POST /payments/webhook` updates them. Webhook signatures are always checked.
- Payment and order creation accept an `Idempotency-Key` header, so a retried request never
  charges or orders twice.
- Never put live keys or real card numbers in this project.

## Tests

The tests use a separate SQLite file (`test_shopsphere.db`) created fresh on every run with the
seed data, so the MySQL database is not touched.

```powershell
pytest -v
```

| File | What it checks |
|---|---|
| tests/test_unit.py | money rounding, order numbers, password hashing, Q&A content filter, CSV / Excel / PDF export |
| tests/test_auth_roles.py | login, invalid / missing token, staff vs customer access, customer data isolation |
| tests/test_workflow.py | cart -> order -> payment -> cancellation -> refund, idempotency, failed payment, webhook signature |
| tests/test_integration_db.py | stock movements, no overselling, read-only audit logs, supplier -> PO -> stock -> invoice |
| tests/test_docs.py | Swagger loads, every endpoint documented, Postman collection covers every endpoint |

## Postman

`postman/ShopSphere_Pro.postman_collection.json` has every endpoint, one folder per module.
Import it, set the `token` collection variable to your `access_token`, and send requests.
To rebuild it after API changes:

```powershell
python -m scripts.export_postman
```

## Background jobs and exports

- `GET /exports/{report}?format=csv|xlsx|pdf` downloads a report directly.
- `POST /jobs/exports` runs a big export in the background, `GET /jobs/{id}/download` gets the file.
- `POST /jobs/schedules` schedules inventory / order summaries or the email-SMS outbox every N minutes.
- Failed jobs keep their error and can be retried (max 3 attempts) with `POST /jobs/{id}/retry`.
- Exported files are saved in the `exports/` folder (not committed to git).

## Modules
- 01 Authentication & Authorization
- 02 User Management
- 03 Customer Registration
- 04 Customer Profile
- 05 Address Management
- 06 Category Management
- 07 Brand Management
- 08 Product Management
- 09 Product Variants
- 10 Product Images & Documents
- 11 Product Attributes
- 12 Product Pricing
- 13 Inventory Management
- 14 Warehouse Management
- 15 Inventory Transfers
- 16 Low-Stock Management
- 17 Shopping Cart
- 18 Cart Price Calculation
- 19 Wishlist
- 20 Coupon Management
- 21 Promotion Management
- 22 Tax Management
- 23 Shipping Configuration
- 24 Checkout Management
- 25 Order Management
- 26 Order Item Management
- 27 Order Status Management
- 28 Order Cancellation
- 29 Payment Management
- 30 Payment Transaction History
- 31 Refund Management
- 32 Cash on Delivery Management
- 33 Shipping Order Management
- 34 Shipment Tracking
- 35 Delivery Management
- 36 Return Management
- 37 Exchange Management
- 38 Product Reviews and Ratings
- 39 Product Questions and Answers
- 40 Customer Support Tickets
- 41 Notification Management
- 42 Email and SMS Notifications
- 43 Seller and Supplier Management
- 44 Purchase Order Management
- 45 Supplier Invoice Management
- 46 Audit Log Management
- 47 Dashboard and Summary APIs
- 48 Reports and Analytics APIs
- 49 Report Export and Background Tasks
