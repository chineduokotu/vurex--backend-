# VUREX API Testing With Postman

This guide shows how to test the VUREX Sprint 2 backend endpoints in Postman.

## 1. Start The Backend

From the Django project folder:

```powershell
cd c:\Users\dell\Documents\bank\vurex
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Your API base URL is:

```text
http://127.0.0.1:8000
```

## 2. Create A Postman Environment

Create a Postman environment called `VUREX Local` with these variables:

| Variable | Example |
|---|---|
| `base_url` | `http://127.0.0.1:8000` |
| `paystack_secret_key` | `sk_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `vendor_id` | UUID from DB |
| `buyer_id` | UUID from DB |
| `transaction_id` | UUID from DB |
| `vendor_subaccount_code` | `ACCT_xxxxxxxxxxxxxxxxx` |
| `payment_reference` | UUID/reference returned by initialize |
| `dispute_id` | UUID returned by dispute endpoint |

Make sure `paystack_secret_key` matches `PAYSTACK_SECRET_KEY` in your Django `.env`.

## 3. Create Test Users And Transaction

Use Django admin or Django shell to create:

- One vendor user with `role = vendor`
- One buyer user with `role = buyer`
- One transaction with:
  - `status = created`
  - `amount = 5000`
  - `vendor_id = your vendor UUID`
  - `buyer_id = your buyer UUID`

Copy the transaction UUID into Postman as `transaction_id`.

## 4. Required Request Headers

For normal API calls:

```text
Content-Type: application/json
```

For the Paystack webhook endpoint, also send:

```text
x-paystack-signature: generated-hmac-signature
```

The webhook signature steps are shown in section 7.

## 5. Test The Endpoints In This Order

### 0. Register Vendor Subaccount

**POST**

```text
{{base_url}}/api/vendors/register-subaccount/
```

**Body**

```json
{
  "vendor_id": "{{vendor_id}}",
  "business_name": "VUREX Test Vendor",
  "bank_code": "058",
  "account_number": "0123456789"
}
```

**Expected response**

```json
{
  "subaccount_code": "ACCT_xxxxxxxxxxxxxxxxx",
  "vendor_id": "vendor-uuid",
  "status": "active"
}
```

Save `subaccount_code` into the Postman environment variable `vendor_subaccount_code`.

Common sandbox bank codes:

| Bank | Code |
|---|---|
| GTBank | `058` |
| First Bank | `011` |
| Access Bank | `044` |
| Zenith Bank | `057` |
| UBA | `033` |

### 1. Initialize Transaction

**POST**

```text
{{base_url}}/api/transactions/initialize/
```

**Body**

```json
{
  "transaction_id": "{{transaction_id}}"
}
```

**Expected response**

```json
{
  "authorization_url": "https://checkout.paystack.com/xxxxx",
  "reference": "transaction-uuid",
  "status": "initialized"
}
```

Save `reference` into the Postman environment variable `payment_reference`.

### 2. Simulate Paystack Webhook

**POST**

```text
{{base_url}}/api/webhooks/paystack/
```

**Body**

```json
{
  "event": "charge.success",
  "data": {
    "reference": "{{payment_reference}}"
  }
}
```

**Expected response**

```json
{
  "status": "ok"
}
```

After this, the transaction status should become `funded`.

Important: this request requires a valid `x-paystack-signature`. See section 7.

### 3A. Confirm Delivery

Use this if the buyer confirms the goods were received.

**POST**

```text
{{base_url}}/api/transactions/confirm-delivery/
```

**Body**

```json
{
  "transaction_id": "{{transaction_id}}",
  "vendor_subaccount_code": "{{vendor_subaccount_code}}"
}
```

**Expected response**

```json
{
  "transfer_code": "TRF_xxxxxxxxxx",
  "status": "delivered"
}
```

After this, the transaction status should become `delivered`.

### 4A. Raise Dispute

Use this instead of confirm delivery if the buyer has a problem.

**POST**

```text
{{base_url}}/api/transactions/dispute/
```

**Body**

```json
{
  "transaction_id": "{{transaction_id}}",
  "reason": "Goods not received as described"
}
```

**Expected response**

```json
{
  "dispute_id": "dispute-uuid",
  "status": "disputed"
}
```

Save `dispute_id` into the Postman environment if you want to track it.

After this, the transaction status should become `disputed`.

### 5. Resolve Dispute

Only call this after raising a dispute.

**POST**

```text
{{base_url}}/api/transactions/resolve/
```

Buyer wins:

```json
{
  "transaction_id": "{{transaction_id}}",
  "outcome": "refund_buyer"
}
```

Vendor wins:

```json
{
  "transaction_id": "{{transaction_id}}",
  "outcome": "release_vendor"
}
```

**Expected response**

```json
{
  "outcome": "refund_buyer",
  "status": "resolved"
}
```

After this, the transaction status should become `resolved`.

## 6. Recommended Testing Flows

### Happy Path

1. Register vendor subaccount
2. Initialize transaction
3. Simulate Paystack webhook
4. Confirm delivery

Expected final status:

```text
delivered
```

### Dispute Path

Use a fresh transaction, then:

1. Register vendor subaccount
2. Initialize transaction
3. Simulate Paystack webhook
4. Raise dispute
5. Resolve dispute

Expected final status:

```text
resolved
```

## 7. How To Sign The Paystack Webhook In Postman

The backend rejects unsigned webhook calls. To test the webhook manually, add this script to the webhook request only.

Open the webhook request in Postman, go to **Pre-request Script**, and paste:

```javascript
const secret = pm.environment.get("paystack_secret_key");
const rawBody = pm.request.body.raw;
const signature = CryptoJS.HmacSHA512(rawBody, secret).toString(CryptoJS.enc.Hex);

pm.request.headers.upsert({
  key: "x-paystack-signature",
  value: signature
});
```

Then send the webhook request normally.

## 8. Common Errors

### `Invalid Paystack signature`

Your webhook signature is missing or wrong.

Check that:

- The webhook request has the pre-request script from section 7.
- `paystack_secret_key` in Postman matches `.env`.
- The body is raw JSON, not form-data.

### `Transaction must be in 'funded' state`

You called confirm delivery before the webhook changed the transaction from `created` to `funded`.

Run:

```text
POST /api/webhooks/paystack/
```

### `Transaction must be in 'in_transit' state`

You called confirm delivery or dispute before the transaction was in `in_transit` state.

### `Vendor subaccount_code is required for release`

You did not register the vendor subaccount first, or the vendor record does not have `subaccount_code`.

Run:

```text
POST /api/vendors/register-subaccount/
```

### `Paystack error: PAYSTACK_SECRET_KEY is not configured`

Your `.env` file is missing `PAYSTACK_SECRET_KEY`.

## 9. Notes

- Use Paystack sandbox keys only.
- Amounts are stored in Naira and sent to Paystack in kobo.
- Terminal Africa integration has been removed.
- The HTML prototype must use the same transaction ID and vendor subaccount code you tested here.
