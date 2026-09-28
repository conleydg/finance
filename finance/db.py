"""SQLite storage. One file under data/, never leaves this Mac."""
import os
import shutil
import sqlite3
from datetime import datetime
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
    name TEXT NOT NULL,              -- not unique: two "401(k)"s at different employers are both fine
    kind TEXT NOT NULL DEFAULT 'checking',  -- checking | savings | credit | retirement | investment
    institution TEXT,                -- e.g. Fidelity
    last4 TEXT,                      -- last digits of the account number; with institution, identifies it
    subtype TEXT,                    -- e.g. 401(k), Roth IRA, HSA, 529, brokerage
    contributing INTEGER             -- 1 yes, 0 no, NULL unknown (retirement and investment accounts)
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
    ("accounts", "institution", "ALTER TABLE accounts ADD COLUMN institution TEXT"),
    ("accounts", "last4", "ALTER TABLE accounts ADD COLUMN last4 TEXT"),
    ("accounts", "subtype", "ALTER TABLE accounts ADD COLUMN subtype TEXT"),
    ("accounts", "contributing", "ALTER TABLE accounts ADD COLUMN contributing INTEGER"),
]


ACCOUNT_COLS = "id, name, kind, institution, last4, subtype, contributing"


def _rebuild_accounts(con: sqlite3.Connection) -> None:
    """Older databases made account names unique. SQLite can't drop a constraint, so copy the table."""
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'accounts'").fetchone()
    if not sql or "UNIQUE" not in sql[0].upper():
        return
    backup = DATA_DIR / "backups" / f"before-accounts-migration-{datetime.now():%Y%m%d-%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    con.commit()
    shutil.copy2(DB_PATH, backup)
    cols = [r[1] for r in con.execute("PRAGMA table_info(accounts)")]
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("BEGIN")
        con.execute("""CREATE TABLE accounts_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                       kind TEXT NOT NULL DEFAULT 'checking', institution TEXT, last4 TEXT, subtype TEXT,
                       contributing INTEGER)""")
        keep = [c for c in ("id", "name", "kind") if c in cols]
        con.execute(f"INSERT INTO accounts_new({', '.join(keep)}) SELECT {', '.join(keep)} FROM accounts")
        con.execute("DROP TABLE accounts")
        con.execute("ALTER TABLE accounts_new RENAME TO accounts")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON")


def init() -> None:
    with connect() as con:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'accounts'").fetchone():
            _rebuild_accounts(con)
        for table, col, sql in MIGRATIONS:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            if cols and col not in cols:
                con.execute(sql)
        con.executescript(SCHEMA)
        con.executemany("INSERT OR IGNORE INTO categories(name, kind) VALUES (?, ?)", DEFAULT_CATEGORIES)


def account_id(con: sqlite3.Connection, name: str, kind: str | None = None) -> int:
    """Find an account by name, creating it if there's none (names aren't unique; first match wins)."""
    r = con.execute("SELECT id FROM accounts WHERE name = ? ORDER BY id LIMIT 1", (name,)).fetchone()
    if r:
        if kind in ACCOUNT_KINDS:
            con.execute("UPDATE accounts SET kind = ? WHERE id = ?", (kind, r["id"]))
        return r["id"]
    return create_account(con, name, kind or "checking")


def create_account(con: sqlite3.Connection, name: str, kind: str = "checking", institution: str | None = None,
                   last4: str | None = None, subtype: str | None = None, contributing: int | None = None) -> int:
    return con.execute("INSERT INTO accounts(name, kind, institution, last4, subtype, contributing) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (name.strip(), kind if kind in ACCOUNT_KINDS else "checking",
                                                     institution, last4, subtype, contributing)).lastrowid


def categories(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT id, name, kind FROM categories ORDER BY kind, name")]
