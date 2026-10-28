import sqlite3
from pathlib import Path
from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")


def _connect(path):
    # isolation_level=None -> we control transactions explicitly (BEGIN IMMEDIATE / COMMIT).
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    conn = _connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.close()
