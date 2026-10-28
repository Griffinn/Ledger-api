# Ledger API

A small accounts-and-transactions REST API: immutable ledger, atomic balance updates,
pagination, validation, and account-to-account transfers (bonus).

**Stack:** Python 3.10+, Flask, SQLite (stdlib `sqlite3`). No ORM, so every SQL statement and
transaction boundary is explicit.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py                      # http://127.0.0.1:5000  (DB file: ./ledger.db)
```
Optional env vars: `PORT`, `DATABASE_PATH`.

## Run tests

```bash
python -m unittest discover -s tests -t .
```
24 tests cover validation, pagination, ordering, immutability (API + DB level), duplicate handling,
balance consistency, transfer rollback, and concurrent debits.

## Project structure

```
app/
  __init__.py    app factory
  routes.py      HTTP layer (thin)
  validators.py  input parsing / validation, money -> cents
  services.py    business logic + atomic transactions
  errors.py      error classes + consistent JSON handlers
  db.py          connection handling
  schema.sql     tables, constraints, immutability triggers
tests/test_api.py
run.py
```

## How data integrity is guaranteed

| Requirement | Mechanism |
|---|---|
| Atomicity | Each transaction/transfer runs inside one `BEGIN IMMEDIATE ... COMMIT`; any exception triggers `ROLLBACK`, so the ledger row(s) and balance update(s) persist together or not at all. |
| No race conditions / overdraft | `BEGIN IMMEDIATE` takes the write lock before the balance is read, so concurrent writers are serialised. Tested with 25 concurrent debits against a balance that covers only 10. |
| Immutability | No PUT/PATCH/DELETE routes (returns 405) **and** SQLite `BEFORE UPDATE` / `BEFORE DELETE` triggers on `transactions` that abort. |
| Balance consistency | Balance is only changed in the same DB transaction as the ledger insert. `CHECK (balance >= 0)` is a last-line guard. A test asserts `balance == SUM(credits) - SUM(debits)`. |
| Duplicate `reference_id` | `UNIQUE (account_id, reference_id)` plus an explicit pre-check for a clean 409. |
| Money precision | Stored as integer cents; amounts parsed with `Decimal` (no float drift: ten 0.10 credits = 1.00). |
| Other constraints | `CHECK` on type, amount > 0, non-blank name/reference; foreign keys enabled. |

## API

Base URL `http://127.0.0.1:5000`. All bodies are JSON. Amounts are numbers (or numeric strings)
with at most 2 decimal places.

### Error format (all errors)

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [{"field": "amount", "message": "amount must be greater than 0.", "code": "INVALID_AMOUNT"}]
  }
}
```

| HTTP | code | When |
|---|---|---|
| 400 | `INVALID_REQUEST_BODY` | Body missing / not a JSON object |
| 400 | `INVALID_PAGINATION` | `page`/`limit` not a positive integer, or `limit` > 100 |
| 400 | `INVALID_ACCOUNT_ID` / `INVALID_TRANSACTION_ID` | Path ID is not a positive integer |
| 404 | `ACCOUNT_NOT_FOUND` / `TRANSACTION_NOT_FOUND` / `NOT_FOUND` | Resource does not exist |
| 405 | `METHOD_NOT_ALLOWED` | e.g. PUT/PATCH/DELETE on a transaction |
| 409 | `DUPLICATE_TRANSACTION` | `reference_id` already used for that account |
| 422 | `VALIDATION_ERROR` | Field-level problems (see `details`; per-field codes `INVALID_AMOUNT`, `INVALID_TRANSACTION_TYPE`) |
| 422 | `INSUFFICIENT_BALANCE` | Debit/transfer exceeds available balance |
| 500 | `INTERNAL_ERROR` | Unexpected error (details are logged, not leaked) |

### Accounts

**POST /accounts** — body `{"name": "Alice"}` → `201`
```json
{"id": 1, "name": "Alice", "balance": 0.0, "created_at": "2026-10-27T10:00:00.123456+00:00"}
```

**GET /accounts/{id}** → `200` account object (balance is always derived from valid transactions).

**GET /accounts?page=1&limit=10** → paginated list of accounts.

### Transactions

**POST /transactions** → `201`
```json
// request
{"account_id": 1, "type": "credit", "amount": 100.50, "reference_id": "INV-1001", "description": "Deposit"}

// response
{
  "transaction": {"id": 1, "account_id": 1, "type": "credit", "amount": 100.5,
                  "description": "Deposit", "reference_id": "INV-1001", "transfer_id": null,
                  "created_at": "..."},
  "account": {"id": 1, "name": "Alice", "balance": 100.5, "created_at": "..."}
}
```
`description` is optional. `type` is `credit` or `debit`.

**GET /transactions/{id}** → `200` transaction object.

**GET /accounts/{id}/transactions?page=1&limit=10** → `200`, newest first (`created_at DESC`, ties broken by insertion order)
```json
{
  "data": [ { "id": 25, "...": "..." } ],
  "pagination": {"current_page": 1, "page_size": 10, "total_records": 25, "total_pages": 3}
}
```
Defaults: `page=1`, `limit=10`. Max `limit=100`. A page past the end returns `data: []` with correct metadata.

### Transfers (bonus)

**POST /transfers** → `201`
```json
// request
{"from_account_id": 1, "to_account_id": 2, "amount": 30, "reference_id": "TRF-1", "description": "Rent"}
```
Response contains the transfer, its `debit_transaction` and `credit_transaction`, and the updated
`from_account` / `to_account`. Debit + credit + transfer record are one atomic unit; any failure
(missing account, insufficient funds, duplicate reference) rolls everything back.

### Quick example

```bash
curl -s -X POST localhost:5000/accounts -H 'Content-Type: application/json' -d '{"name":"Alice"}'
curl -s -X POST localhost:5000/transactions -H 'Content-Type: application/json' \
  -d '{"account_id":1,"type":"credit","amount":100,"reference_id":"r1"}'
curl -s 'localhost:5000/accounts/1/transactions?page=1&limit=5'
```

## Assumptions

1. **Accounts start at balance 0.** There is no way to set a balance directly; it only changes through transactions.
2. **Single currency**, 2 decimal places, max amount 1,000,000,000.
3. **`reference_id` is unique per account**, not globally (as specified). A duplicate is rejected with **409** and
   not applied; it does not return the original transaction (no idempotent-replay behaviour).
4. **Transfers** reuse the client-supplied `reference_id` on both legs, so it must be unused on *both* accounts.
   Transfers between the same account are rejected.
5. **No account update/delete endpoints.** Accounts and transactions are never deleted, preserving the ledger.
6. **Latest-first** means `created_at DESC`, with insertion order as a tie-breaker so ordering is deterministic.
7. **Validation vs. bad request:** malformed JSON / path / query problems are `400`; well-formed JSON with invalid
   field values is `422`; insufficient funds is `422`; duplicates `409`.
8. **`type` is case-insensitive** on input and normalised to lowercase.
9. **No authentication/authorisation**, as it was not in scope.
10. **SQLite** is used for zero-setup local running. Its single-writer lock gives simple, correct serialisation;
    for a multi-node deployment, port to PostgreSQL and use `SELECT ... FOR UPDATE` on the account row (the service
    layer is the only place that would change).
