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
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'checking'   -- checking | savings | credit | retirement | investment
);
CREATE TABLE IF NOT EXISTS balances (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date TEXT NOT NULL,              -- YYYY-MM-DD the balance was as of
    balance REAL NOT NULL,
    import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    source TEXT,                     -- statement | csv | manual
    PRIMARY KEY (account_id, date)
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
    rows_skipped INTEGER,            -- overlapping rows already imported from another file
    period_start TEXT,
    period_end TEXT,
    ending_balance REAL,
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
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    target_amount REAL NOT NULL,
    target_date TEXT,                -- YYYY-MM-DD, optional
    saved_start REAL NOT NULL DEFAULT 0,   -- already saved when the goal was set
    start_date TEXT NOT NULL,        -- YYYY-MM-DD
    account_id INTEGER REFERENCES accounts(id),  -- money into this account counts toward it
    status TEXT NOT NULL DEFAULT 'active',        -- active | done | archived
    note TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,              -- user | assistant
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    message_id INTEGER REFERENCES chat_messages(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,              -- goal | goal_update | budgets
    payload TEXT NOT NULL,           -- JSON
    status TEXT NOT NULL DEFAULT 'pending',       -- pending | accepted | dismissed
    created_at TEXT DEFAULT (datetime('now'))
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


# Accounts whose activity is investing, not spending: kept out of the budget and categorization.
INVEST_KINDS = ("retirement", "investment")
ACCOUNT_KINDS = ("checking", "savings", "credit", "retirement", "investment")
BUDGET_ACCOUNTS = "(SELECT id FROM accounts WHERE kind NOT IN ('retirement', 'investment'))"

MIGRATIONS = [
    ("accounts", "kind", "ALTER TABLE accounts ADD COLUMN kind TEXT NOT NULL DEFAULT 'checking'"),
    ("imports", "period_start", "ALTER TABLE imports ADD COLUMN period_start TEXT"),
    ("imports", "period_end", "ALTER TABLE imports ADD COLUMN period_end TEXT"),
    ("imports", "ending_balance", "ALTER TABLE imports ADD COLUMN ending_balance REAL"),
    ("imports", "rows_skipped", "ALTER TABLE imports ADD COLUMN rows_skipped INTEGER"),
]


def init() -> None:
    with connect() as con:
        for table, col, sql in MIGRATIONS:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            if cols and col not in cols:
                con.execute(sql)
        con.executescript(SCHEMA)
        con.executemany("INSERT OR IGNORE INTO categories(name, kind) VALUES (?, ?)", DEFAULT_CATEGORIES)


def account_id(con: sqlite3.Connection, name: str, kind: str | None = None) -> int:
    con.execute("INSERT OR IGNORE INTO accounts(name, kind) VALUES (?, ?)", (name, kind or "checking"))
    if kind in ACCOUNT_KINDS:
        con.execute("UPDATE accounts SET kind = ? WHERE name = ?", (kind, name))
    return con.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()["id"]


def categories(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, name, kind FROM categories ORDER BY kind, name")]
