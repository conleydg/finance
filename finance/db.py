"""SQLite storage. One file under data/, never leaves this Mac."""
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("FINANCE_DATA", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "finance.db"

DEFAULT_CATEGORIES = [
    # (name, kind) kind: expense | income | transfer
    ("Groceries", "expense"),
    ("Dining", "expense"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Transportation", "expense"),
    ("Fuel", "expense"),
    ("Insurance", "expense"),
    ("Healthcare", "expense"),
    ("Shopping", "expense"),
    ("Subscriptions", "expense"),
    ("Entertainment", "expense"),
    ("Travel", "expense"),
    ("Kids & Education", "expense"),
    ("Pets", "expense"),
    ("Personal Care", "expense"),
    ("Gifts & Donations", "expense"),
    ("Fees & Interest", "expense"),
    ("Taxes", "expense"),
    ("Other", "expense"),
    ("Income", "income"),
    ("Transfer", "transfer"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'expense'
);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    kind TEXT NOT NULL,
    parser TEXT,
    rows_found INTEGER,
    rows_added INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    import_id INTEGER REFERENCES imports(id),
    date TEXT NOT NULL,              -- YYYY-MM-DD
    description TEXT NOT NULL,
    merchant TEXT NOT NULL,          -- normalized key used for rules
    amount REAL NOT NULL,            -- negative = money out, positive = money in
    category_id INTEGER REFERENCES categories(id),
    category_source TEXT,            -- rule | model | user
    fingerprint TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS rules (
    merchant TEXT PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id)
);
CREATE TABLE IF NOT EXISTS budgets (
    category_id INTEGER PRIMARY KEY REFERENCES categories(id),
    monthly_amount REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        if not con.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
            con.executemany("INSERT INTO categories(name, kind) VALUES (?, ?)", DEFAULT_CATEGORIES)


def account_id(con: sqlite3.Connection, name: str) -> int:
    con.execute("INSERT OR IGNORE INTO accounts(name) VALUES (?)", (name,))
    return con.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()["id"]


def categories(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, name, kind FROM categories ORDER BY kind, name")]
