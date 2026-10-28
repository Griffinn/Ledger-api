"""Business logic. Every money-moving operation runs in one BEGIN IMMEDIATE transaction,
so the ledger row(s) and balance update(s) commit together or not at all."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .errors import DuplicateTransaction, InsufficientBalance, NotFound
from .validators import from_cents


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def atomic(db):
    """BEGIN IMMEDIATE takes the write lock up-front, serialising concurrent writers."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


# ---------- serialisers ----------
def account_dict(r):
    return {"id": r["id"], "name": r["name"], "balance": from_cents(r["balance"]),
            "created_at": r["created_at"]}


def transaction_dict(r):
    return {"id": r["id"], "account_id": r["account_id"], "type": r["type"],
            "amount": from_cents(r["amount"]), "description": r["description"],
            "reference_id": r["reference_id"], "transfer_id": r["transfer_id"],
            "created_at": r["created_at"]}


# ---------- accounts ----------
def create_account(db, name):
    cur = db.execute("INSERT INTO accounts (name, balance, created_at) VALUES (?, 0, ?)",
                     (name, now()))
    return get_account(db, cur.lastrowid)


def get_account(db, account_id):
    r = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if r is None:
        raise NotFound(f"Account {account_id} not found.", code="ACCOUNT_NOT_FOUND")
    return account_dict(r)


def list_accounts(db, page, limit):
    total = db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    rows = db.execute("SELECT * FROM accounts ORDER BY id LIMIT ? OFFSET ?",
                      (limit, (page - 1) * limit)).fetchall()
    return [account_dict(r) for r in rows], total


# ---------- transactions ----------
def _apply(db, account_id, type_, amount, description, reference_id, transfer_id=None):
    """Insert ledger row + adjust balance. MUST be called inside atomic()."""
    acct = db.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if acct is None:
        raise NotFound(f"Account {account_id} not found.", code="ACCOUNT_NOT_FOUND")

    if db.execute("SELECT 1 FROM transactions WHERE account_id = ? AND reference_id = ?",
                  (account_id, reference_id)).fetchone():
        raise DuplicateTransaction(
            f"A transaction with reference_id '{reference_id}' already exists for account {account_id}.")

    if type_ == "debit" and acct["balance"] < amount:
        raise InsufficientBalance(
            f"Insufficient balance on account {account_id}: "
            f"available {from_cents(acct['balance'])}, requested {from_cents(amount)}.")

    delta = amount if type_ == "credit" else -amount
    try:
        cur = db.execute(
            "INSERT INTO transactions (account_id, type, amount, description, reference_id, "
            "transfer_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, type_, amount, description, reference_id, transfer_id, now()))
    except sqlite3.IntegrityError:  # UNIQUE backstop
        raise DuplicateTransaction(
            f"A transaction with reference_id '{reference_id}' already exists for account {account_id}.")
    db.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (delta, account_id))
    return cur.lastrowid


def create_transaction(db, account_id, type_, amount, description, reference_id):
    with atomic(db):
        tx_id = _apply(db, account_id, type_, amount, description, reference_id)
    return get_transaction(db, tx_id), get_account(db, account_id)


def get_transaction(db, tx_id):
    r = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if r is None:
        raise NotFound(f"Transaction {tx_id} not found.", code="TRANSACTION_NOT_FOUND")
    return transaction_dict(r)


def list_transactions(db, account_id, page, limit):
    get_account(db, account_id)  # 404 if missing
    total = db.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?",
                       (account_id,)).fetchone()[0]
    rows = db.execute(
        "SELECT * FROM transactions WHERE account_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",   # latest first; id breaks ties
        (account_id, limit, (page - 1) * limit)).fetchall()
    return [transaction_dict(r) for r in rows], total


# ---------- transfers ----------
def create_transfer(db, src, dst, amount, reference_id, description):
    with atomic(db):
        for aid in (src, dst):
            if db.execute("SELECT 1 FROM accounts WHERE id = ?", (aid,)).fetchone() is None:
                raise NotFound(f"Account {aid} not found.", code="ACCOUNT_NOT_FOUND")
        cur = db.execute(
            "INSERT INTO transfers (from_account_id, to_account_id, amount, reference_id, "
            "description, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (src, dst, amount, reference_id, description, now()))
        tid = cur.lastrowid
        debit_id = _apply(db, src, "debit", amount, description or f"Transfer to account {dst}",
                          reference_id, tid)
        credit_id = _apply(db, dst, "credit", amount, description or f"Transfer from account {src}",
                           reference_id, tid)
    return {
        "id": tid, "reference_id": reference_id, "amount": from_cents(amount),
        "description": description,
        "debit_transaction": get_transaction(db, debit_id),
        "credit_transaction": get_transaction(db, credit_id),
        "from_account": get_account(db, src),
        "to_account": get_account(db, dst),
    }
