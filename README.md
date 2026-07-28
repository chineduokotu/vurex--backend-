# VUREX Django Backend

Django + DRF backend for the VUREX escrow Sprint 2 prototype.

## Setup

```powershell
cd c:\Users\dell\Documents\bank\vurex
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with your Paystack sandbox key and PostgreSQL `DATABASE_URL`.

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

## API Endpoints

- `POST /api/vendors/register-subaccount/`
- `POST /api/transactions/initialize/`
- `POST /api/webhooks/paystack/`
- `POST /api/transactions/confirm-delivery/`
- `POST /api/transactions/dispute/`
- `POST /api/transactions/resolve/`

Paystack webhook requests must include a valid `x-paystack-signature` HMAC-SHA512 signature.

For step-by-step Postman requests, see [POSTMAN_TESTING.md](POSTMAN_TESTING.md).

For the local HTML demo in `..\vurex_prototype_wired.html`, set:

- `TRANSACTION_ID`
- `VENDOR_SUBACCOUNT_CODE`
- `PAYSTACK_TEST_SECRET_KEY`

`PAYSTACK_TEST_SECRET_KEY` must match the backend `.env` value so the simulated webhook can be signed. Keep this sandbox-only.
