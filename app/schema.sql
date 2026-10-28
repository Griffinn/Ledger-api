PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    balance     INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),  -- minor units (cents)
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS transfers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account_id  INTEGER NOT NULL REFERENCES accounts(id),
    to_account_id    INTEGER NOT NULL REFERENCES accounts(id),
    amount           INTEGER NOT NULL CHECK (amount > 0),
    reference_id     TEXT    NOT NULL,
    description      TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL,
    CHECK (from_account_id <> to_account_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    type          TEXT    NOT NULL CHECK (type IN ('credit', 'debit')),
    amount        INTEGER NOT NULL CHECK (amount > 0),            -- minor units (cents)
    description   TEXT    NOT NULL DEFAULT '',
    reference_id  TEXT    NOT NULL CHECK (length(trim(reference_id)) > 0),
    transfer_id   INTEGER REFERENCES transfers(id),
    created_at    TEXT    NOT NULL,
    UNIQUE (account_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_account_created
    ON transactions (account_id, created_at DESC);

-- Immutability enforced at the database level, not just the API level.
CREATE TRIGGER IF NOT EXISTS transactions_no_update
BEFORE UPDATE ON transactions
BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS transactions_no_delete
BEFORE DELETE ON transactions
BEGIN SELECT RAISE(ABORT, 'transactions are immutable'); END;
