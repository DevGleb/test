# LedgerLite

Two services from the take-home assignment:

- **wallet-service** keeps USD and credit balances, takes deposits through an external
  payment provider, and handles transfers, credit purchases and transaction history.
  It uses FastAPI and Postgres.
- **provider-mock** is a minimal in-memory imitation of that payment provider. It sends
  HMAC-signed webhooks.

## Run it

```bash
docker compose up --build
```

This starts Postgres, provider-mock (`:8001`) and wallet-service (`:8000`). No `.env`
file is needed because every variable has a local-dev default. To override them, copy
`.env.example` to `.env`. Migrations run automatically when the wallet starts.

API docs: http://localhost:8000/docs and http://localhost:8001/docs.

### Try the main flow

```bash
# 1. Create two accounts. The API key is printed once and only its SHA-256 is stored.
docker compose exec wallet python -m wallet.cli create-account alice
docker compose exec wallet python -m wallet.cli create-account bob
export ALICE=llk_...   BOB_ID=<bob account_id>

# 2. Deposit 10.00. This creates an invoice at the provider and returns pay_url and invoice_id.
curl -s -X POST localhost:8000/v1/deposits -H "X-API-Key: $ALICE" \
     -H 'Content-Type: application/json' -d '{"amount": "10.00"}'

# 3. "Pay" the invoice. The provider sends the signed webhook twice (duplicate: true).
curl -s -X POST localhost:8001/invoices/<invoice_id>/simulate \
     -H 'Authorization: Bearer dev-provider-api-key' -H 'Content-Type: application/json' \
     -d '{"status": "paid", "deliver_webhook": true, "duplicate": true}'

curl -s localhost:8000/v1/accounts/me -H "X-API-Key: $ALICE"    # balance_usd: "9.85"

# 4. Transfer with an idempotency key. Run it twice: the debit happens only once.
curl -s -X POST localhost:8000/v1/transfers -H "X-API-Key: $ALICE" \
     -H 'Idempotency-Key: demo-1' -H 'Content-Type: application/json' \
     -d "{\"to_account_id\": \"$BOB_ID\", \"amount\": \"2.00\"}"

# 5. Buy credits and look at the history.
curl -s -X POST localhost:8000/v1/purchases -H "X-API-Key: $ALICE" \
     -H 'Content-Type: application/json' -d '{"credits": 50}'
curl -s "localhost:8000/v1/transactions?limit=10" -H "X-API-Key: $ALICE"

# 6. Settle deposits whose webhook was lost (simulate with "deliver_webhook": false).
docker compose exec wallet python -m wallet.cli reconcile --older-than-minutes 0
```

`python3 scripts/smoke_test.py` (or `make smoke`) runs this whole flow automatically
against the running stack and checks every number. CI runs it too.

## Tests

The tests use a real Postgres, not SQLite: row locks, `ON CONFLICT` and the concurrency
tests depend on Postgres behaviour. The compose database works fine for this; the tests
create and use a separate `ledgerlite_test` database there.

```bash
python3.11 -m venv .venv    # 3.11+
.venv/bin/pip install -r wallet-service/requirements-dev.txt -r provider-mock/requirements-dev.txt
docker compose up -d db
make test     # pytest for both services
make lint     # ruff check + ruff format --check
```

To use another database, set
`TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:port/<name>_test`. The test
suite refuses to run against a database whose name doesn't end in `_test`, because it
truncates tables.

CI (`.github/workflows/ci.yml`) has three jobs:
- ruff;
- pytest for both services against a Postgres service container;
- `docker compose up --build --wait` plus the smoke test.

## Who calls whom

```
                 X-API-Key                      Bearer PROVIDER_API_KEY
  client ───────────────────▶ wallet-service ───────────────────────────▶ provider-mock
                              │      ▲   POST /invoices, GET /invoices/{id}      │
                              │      │   (timeouts, 1 retry)                      │
                              │      └────────────────────────────────────────────┘
                              │        POST /v1/webhooks/provider
                              │        X-Signature: sha256=HMAC(secret, "{ts}.{raw body}")
                              ▼
                          Postgres       accounts, deposits, webhook_events, transfers,
                                         purchases, ledger_entries, idempotency_keys,
                                         rate_limit_hits

  python -m wallet.cli reconcile ──▶ GET /invoices/{id} for stale pending deposits
```

Code layout: `wallet/api/*` contains thin routers. The logic is in `wallet/services/*`.
`wallet/money.py` holds every money rule.

## Key decisions and trade-offs

1. **FastAPI + async SQLAlchemy 2 + asyncpg + Alembic.** The service mostly waits on
   I/O (Postgres and the provider), so async fits. Pydantic gives strict request
   validation, and FastAPI provides OpenAPI docs for free. Alembic keeps the schema
   versioned; the tests apply the real migrations.

2. **Money is `Decimal` from the JSON string to `NUMERIC(18,2)`.**
   - Amounts must match `^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$`. A bare `Decimal(...)`
     would accept `1e3`, `NaN`, `1_000`, `" 10"` and non-ASCII digits. A JSON number
     gives 422, as do more than two decimal places.
   - The fee is rounded first (`ROUND_HALF_UP`) and then subtracted. Rounding
     `paid × 0.985` instead gives 0.99 for a 1.00 payment, while the correct answer
     is 0.98.
   - Credits get the whole-order tier discount, with one rounding at the end. Python's
     default banker's rounding would make 507 credits cost 48.16 instead of 48.17.

3. **Balances can't go negative under concurrency.**
   - Each debit runs in one transaction: row-lock, check, update.
   - Accounts are locked in primary-key order, so A→B and B→A can't deadlock.
   - `CHECK (balance_usd >= 0)` is the last line of defence.
   - The lock is `FOR NO KEY UPDATE`, not `FOR UPDATE`. Inserts into tables with a
     foreign key to `accounts` take `FOR KEY SHARE`, and `FOR UPDATE` deadlocked
     against it. The parallel-transfer test caught this.

4. **The idempotency key lives in the same transaction as the operation.**
   - The `(account_id, key)` row is inserted first. A parallel request with the same key
     waits on the primary key, then replays the stored response.
   - If the operation crashes, nothing is committed, the key stays free and a retry
     works.
   - The fingerprint hashes the *validated* body, so `"5"`, `"5.00"` and reordered
     keys count as the same request.
   - Business errors such as `insufficient_funds` are stored and replayed too (Stripe
     semantics). A new attempt after a top-up needs a new key.
   - Keys are scoped per account.

5. **Webhooks.**
   - The HMAC is computed over the raw request bytes and compared in constant time.
   - The timestamp window is ±5 minutes; future timestamps are rejected as well.
   - Dedup uses a unique `event_id` insert plus a row lock on the deposit. That makes a
     duplicate, a late event and a race with reconciliation all no-ops.
   - Duplicates and events for an already-final deposit get **200** (`duplicate` /
     `ignored`) so the provider stops retrying.
   - An unknown invoice gets **404** and is *not* recorded, so a real provider's retry
     can succeed later.

6. **Provider calls run outside DB transactions.**
   - The deposit row is committed first, then the provider is called with a 2 s connect
     and 5 s read timeout.
   - Transport errors and 5xx are retried once. This is safe because provider-mock is
     idempotent on `external_id`.
   - If the provider is still down: 503 `provider_unavailable`. If it rejects the
     request: 502 `provider_error`. In both cases the deposit is marked `failed`. The
     client never received a `pay_url`, so it cannot pay that deposit.

7. **Rate limit: a sliding-window log in Postgres**, not a fixed per-minute counter.
   - A fixed counter allows 2× the limit across a minute boundary.
   - The log keeps the limit strict in any rolling 60 s window.
   - The state is shared across workers and replicas.
   - A per-account advisory lock makes count-plus-insert atomic.
   - Rejected requests don't consume the window.

### API conventions

- **Errors** always have the shape
  `{"error": {"code", "message", "details"}}`, including 404/405 on unknown routes and
  validation errors.

  | HTTP | Codes |
  |---|---|
  | 400 | `malformed_json`, `idempotency_key_required` |
  | 401 | `unauthorized`, `invalid_signature`, `stale_timestamp` |
  | 404 | `account_not_found`, `deposit_not_found`, `not_found` |
  | 409 | `insufficient_funds`, `idempotency_key_reused`, `idempotency_key_in_use` |
  | 422 | `validation_error`, `self_transfer`, `invalid_cursor`, `invalid_idempotency_key` |
  | 429 | `rate_limited` |
  | 502 / 503 | `provider_error` / `provider_unavailable` |

- **`insufficient_funds` is 409**: the request is valid but conflicts with the current
  balance. The spec's list of codes has no 402.
- **Missing `Idempotency-Key` is 400.**
- **Idempotency on purchases:** `Idempotency-Key` is optional there and honoured when
  sent.
- **Extra fields** in request bodies are rejected (422). Webhook payloads may carry extra
  fields; only the validated ones are stored in `webhook_events`.
- **Order of checks:** FastAPI parses the JSON body before it runs dependencies, so a
  request with a broken body and no API key gets 400 `malformed_json`, not 401. Fixing
  this would mean moving auth into middleware; I left it.
- **History** is newest first, with keyset pagination: pass `next_cursor` back as
  `?cursor=`. Deposits appear when credited; pending ones can be read via
  `GET /v1/deposits/{id}`.

## Simplified or not done

- **provider-mock state:** it is in memory and runs one worker. Webhooks are sent once,
  with no retries; a lost webhook is what reconciliation is for. After a provider-mock
  restart, old invoices are gone and reconciliation counts them as `provider_errors`.
- **Reconciliation** is a CLI command (`--loop-seconds` for a simple loop). It is not
  wired into compose as a scheduled job; in production it would run from cron or a
  worker.
- **Unknown invoice after timeouts:** if the provider created the invoice but both
  attempts timed out, the deposit is `failed`. That is safe because nobody got the
  `pay_url`. With a real provider I would also look the invoice up by `external_id`.
- **Accounts and API keys:** accounts are created only through the CLI. There is one key
  per account and no key rotation or revocation endpoint.
- **Idempotency keys never expire.** In production they'd get a TTL (e.g. 24 h) and a
  cleanup job.
- **Ledger:** `ledger_entries` is a per-account history of balance changes, not full
  double-entry bookkeeping with a provider/fees account.
- **Overpayment:** the spec says to credit the amount actually paid, so a `paid` webhook
  whose `paid_amount` exceeds the invoice amount is credited as is.
- **Pagination edge case:** keyset pagination is by `id`. A transaction that commits out
  of id order while a client is paging could be skipped. Acceptable for this scope.
- **Observability:** no metrics or tracing, only logs.
