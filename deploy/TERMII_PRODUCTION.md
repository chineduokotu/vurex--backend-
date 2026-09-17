# Termii production integration

## Scope and ownership

This release uses real Termii Token and SMS APIs. There is no Termii sandbox switch,
mock backend, fallback code, or successful response when provider verification fails.
No automated tests were run, at the owner's request. No real SMS calls were made during implementation.
Static syntax/schema checks, Django checks, focused frontend lint and a production build
are separate from delivery verification. Actual delivery and webhook compatibility have
not been demonstrated by those checks.

- `identity`: registration, password login, scoped onboarding sessions, Nigerian phone
  challenges, limits, verification and audit events.
- `integrations/termii.py`: HTTP requests, provider response validation and webhook signatures.
- `notifications`: durable PostgreSQL dispatch jobs, fixed message templates, delivery
  attempts and sanitized webhook receipts. The worker runs the same application code.
- Existing transaction/business modules retain their responsibilities. This release
  does not send payment, payout or refund alerts from the existing transaction states.

PostgreSQL holds the queue and shared rate counters. Redis/Celery are not required.
Network calls run outside database transactions. Only an explicitly safe unsent failure
(such as connection establishment timeout) gets bounded automatic retries. A read timeout,
unexpected send response or interrupted worker produces an unknown outcome, never an
automatic second send. OTP resends are explicit user actions with a new local challenge.

## Required backend configuration

Place secrets in the existing backend `.env`, or inject them through the deployment
secret manager. Do not put them in React variables, Git, command-line arguments or logs.

```dotenv
TERMII_API_KEY=<your Termii API key>
TERMII_BASE_URL=<exact HTTPS base URL from your dashboard>
TERMII_SENDER_ID=<exact approved sender ID>
TERMII_WEBHOOKS_ENABLED=False
SMS_ENABLED=True
SMS_DAILY_SEND_LIMIT=500

DJANGO_SECRET_KEY=<strong random secret of at least 50 characters>
DEBUG=False
ALLOW_ALL_HOSTS_AND_ORIGINS=False
DATABASE_URL=<your existing production PostgreSQL connection URL>
ALLOWED_HOSTS=<your API hostname>
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://<your frontend hostname>
CSRF_TRUSTED_ORIGINS=https://<your frontend hostname>
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SESSION_COOKIE_SAMESITE=Lax
SECURE_SSL_REDIRECT=True
PUBLIC_API_BASE_URL=https://<your API hostname>
```

Termii's DND/transactional route must be activated for the sender and destination
country. The integration fixes `TERMII_CHANNEL` to `dnd`; there is no promotional fallback.
The API key and base URL alone are insufficient for production activation.

`TERMII_WEBHOOKS_ENABLED` defaults to `False`. No webhook secret is needed for OTP sending,
OTP verification, resending or ordinary SMS. Phone verification uses Termii's Verify Token
API directly. The worker and production check do not require delivery-report configuration
when webhooks are disabled. Accepted messages remain `sent` (provider acceptance); the
application cannot independently confirm handset delivery without delivery reports.

### Optional delivery webhooks

Leave delivery webhooks disabled for now. The callback endpoint returns HTTP 404 without
reading or storing its body. Do not register a callback with Termii while this feature is
disabled. Use Termii's dashboard for delivery-report investigation.

Termii documents HMAC-SHA512 signatures in `X-Termii-Signature`, but its public page does
not specify their encoding or unambiguously identify a separate webhook-secret setting.
`TERMII_WEBHOOK_SECRET` is our application's name for the signing key, not a separate
credential that Termii is known to issue. It is not required for this release's core flow.

If delivery reports are enabled later, first establish the provider's actual signing
mechanism, then configure `TERMII_WEBHOOKS_ENABLED=True`, the confirmed signing key in
`TERMII_WEBHOOK_SECRET`, and `TERMII_WEBHOOK_SIGNATURE_ENCODING` (`hex` or `base64`).
Do not invent a new secret or assume the API key is the signing key. Enabled webhooks
require valid configuration and signatures; they never establish phone ownership.

### Browser/API hosting

The current local backend `.env` enables the owner's requested temporary access mode:

```dotenv
ALLOW_ALL_HOSTS_AND_ORIGINS=True
```

This overrides `ALLOWED_HOSTS` to `*`, enables all CORS origins with credentials, and
sets session/CSRF cookies to Secure `SameSite=None`. The identity API accepts any
request Origin while still checking the CSRF cookie/header token pair. Django admin
keeps its standard CSRF checks. Requests without an Origin retain Django's normal
Referer checks. HTTPS and authentication requirements still apply.

Any website can read credentialed API responses in this mode, including the identity
CSRF bootstrap response, so token validation alone does not prevent hostile websites
from using a browser's session. This mode is temporary. The deployment check reports
`identity.W001` rather than blocking startup for missing origin allowlists.

Restart the API and worker after changing environment configuration. To restore domain
restrictions, set `ALLOW_ALL_HOSTS_AND_ORIGINS=False`, `CORS_ALLOW_ALL_ORIGINS=False`,
and configure the explicit `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, and
`CSRF_TRUSTED_ORIGINS` lists above. Existing list values are preserved by the override.
This changes Django configuration only; proxy routing, DNS and TLS still need to serve
the hostname used by the browser. Browser third-party-cookie policies may still block
sessions between unrelated sites.

Use HTTPS for the frontend and API. Prefer subdomains of the same site, for example
`app.example.com` and `api.example.com`, so the secure onboarding cookie works with
`SameSite=Lax`. Set frontend `VITE_API_URL=https://<API hostname>/api` when the API is
separately hosted; the client otherwise uses same-origin `/api`.

Separate unrelated sites may require `SESSION_COOKIE_SAMESITE=None`, explicit credentialed
CORS and CSRF origins, and remain subject to browser third-party-cookie blocking. A same-site
custom domain or same-origin reverse proxy avoids that dependency.

When TLS terminates at a trusted reverse proxy, set `TRUST_PROXY_SSL_HEADER=True` only if
that proxy overwrites `X-Forwarded-Proto`. It must append or overwrite the client IP correctly.
Configure `IDENTITY_TRUSTED_PROXY_IPS` to the actual proxy addresses; do not trust arbitrary
forwarded headers. The default trusts loopback addresses only. Authentication endpoints
reject plain HTTP. Ensure no infrastructure request-body logging captures passwords or OTPs.

## Database and rollout precautions

`DATABASE_URL` is now read from `.env` as well as the process environment. Previously,
the database helper could fall back to SQLite when only python-decouple had read `.env`.
Before migration, confirm the PostgreSQL target is the intended existing database. If
the active deployment has been using SQLite, its data needs a separately reviewed migration;
switching the URL does not copy accounts or transactions.

Migrations preserve the existing user IDs and transaction relationships and add:

- `transactions/0006_phone_verification`: verified-phone timestamp, active flag, session
  version and uniqueness of verified phone numbers.
- `identity/0001_initial`: challenges, audit events and shared rate windows.
- `notifications/0001_initial`: messages, attempts, jobs, receipts and daily send budget.

Existing phone numbers are unverified. Users must sign in with their existing password
and verify a number. Old JWTs are rejected because they lack the new session version.
Legacy email-OTP and direct phone-update endpoints return HTTP 410. Deploy the frontend
and backend together and ensure browsers refresh the HTML entry point. Email ownership
and KYC are not established by phone verification.

Automatically created buyer placeholders no longer receive a shared usable password.
Existing placeholder credentials cannot log in through the new login endpoint. A genuine
guest-to-account claim/recovery process remains separate work. Logout and verified phone
changes invalidate the user's previously issued JWTs and onboarding sessions on other devices.

## Activation steps

These commands are deployment instructions; they were not executed against the configured
database or production services during implementation.

1. Confirm sender approval, DND activation, HTTPS domains and database target. Delivery
   webhooks can remain disabled; no webhook secret is needed for sending or verification.
2. Back up the existing database and configure the values above in the API and worker environments.
3. From the backend virtual environment run the configuration check:

   ```sh
   python manage.py check --deploy --fail-level ERROR
   ```

   Process environment variables take precedence over `.env`. In the inspected Windows
   shell, `DEBUG` was inherited as the non-boolean value `release`; static checks explicitly
   used `DEBUG=False`. Set `DEBUG=False` in the actual service environment as well.

4. Apply the reviewed schema changes:

   ```sh
   python manage.py migrate
   ```

5. Deploy the matching frontend build and restart the Django web service.
6. Skip webhook registration while `TERMII_WEBHOOKS_ENABLED=False`. If optional authenticated
   delivery reports are configured later, register this HTTPS callback in Termii:

   ```text
   https://<your API hostname>/api/webhooks/termii/
   ```

   Restrict this route to a 64 KiB request body at the reverse proxy. Do not redirect it to
   login or add browser authentication; its provider signature is its authentication.

7. Install `deploy/vurex-notifications.service`, adapting the existing virtual-environment
   path/user if necessary. Start the worker only when real sending is intended. Its
   `ExecStartPre` runs the production configuration check. The worker itself requires
   PostgreSQL and uses row locks so multiple instances can share the queue safely.
8. Observe queue age, errors and provider balance during the controlled production rollout.
   View delivery reports in Termii's dashboard while webhooks are disabled. Every actual
   onboarding request can incur an SMS charge.

## Operational behavior

- Codes are six digits, expire after five minutes, and allow three verification attempts.
- Resends have a 60-second cooldown. Limits apply separately to user, destination and IP;
  creating new challenges does not reset the hourly verification budget.
- An `Idempotency-Key` identifies a send/resend request. Retrying that request returns the
  existing challenge; different input with the same key is rejected.
- Sending is queued. Termii acceptance is distinct from handset delivery and phone verification.
- A code is not stored locally. Provider PIN/message references and challenge outcomes are stored.
- Verification reserves an attempt before contacting Termii and consumes the challenge atomically.
  Resend races and superseded challenges cannot verify a different phone.
- When enabled, provider callbacks are signature-checked before parsing; only allowlisted metadata and a
  payload fingerprint are stored. The message text, which may include an OTP, is discarded.
- Duplicate reports have one receipt/job; delayed sent reports cannot erase a delivered status.
  Reports arriving before the send response are retried locally for matching; unresolved receipts
  remain visible for operations. A report never sets `phone_verified_at`.
- The daily cap counts send attempts, including ambiguous outcomes. It is a count cap, not a
  guaranteed monetary spend limit. Monitor the actual Termii wallet balance separately.

Inspect aggregate status without phone numbers:

```sh
python manage.py notification_status
```

Use the Django admin's read-only challenge, message, attempt, job and receipt views for
investigation. Investigate `unknown`, `failed` and `unmatched` records against the provider
dashboard; do not reset unknown dispatch jobs to pending. Phone users can explicitly request
a replacement code after the cooldown. A notification failure never undoes phone verification.

Pause new sends with `SMS_ENABLED=False` in both API and worker environments and restart
those processes. Keep processing delivery reports if configured. Do not roll back to the unsafe legacy
OTP endpoints. Schema rollback is not a safe messaging rollback.

After approving retention periods, schedule bounded history cleanup and Django session cleanup:

```sh
python manage.py purge_notification_history --days 30 --audit-days 90 --batch-size 1000
python manage.py clearsessions
```

The history command also removes retired email OTP records. It retains unknown sends,
unmatched receipts and in-flight work for investigation. Schedule repeated bounded batches
when volume requires it. Restrict database/admin access and configure encrypted backups.
Remove any historical `last_otp.txt` copies from production storage and old log archives
under the application's retention process; the new implementation does not create them.

## Remaining application concerns

This integration does not make the whole financial application production-ready. The
previous architecture audit identified payment-verification shortcuts, missing transaction
ownership checks and incomplete settlement accounting. Those remain launch blockers for
real financial operations. Financial SMS templates are deliberately not connected to those
unverified state changes. SMS-only password recovery, login MFA, marketing and additional
countries are separate future changes.

Provider references: [Send Token](https://developers.termii.com/send-token),
[Verify Token](https://developers.termii.com/verify-token),
[Messaging](https://developers.termii.com/messaging-api),
[Webhooks](https://developers.termii.com/events-and-reports).
