"""Health check that prints counts only: no descriptions, no amounts, no account names.

Run it yourself and paste the output when something looks off:
    .venv/bin/python scripts/diagnose.py
It reads data/finance.db (or FINANCE_DATA) read-only and changes nothing.
"""
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("FINANCE_DATA", ROOT / "data")) / "finance.db"
TRANSFERISH = ("upper(t.description) GLOB '*PAYMENT*' OR upper(t.description) GLOB '*TRANSFER*' "
               "OR upper(t.description) GLOB '*AUTOPAY*' OR upper(t.description) GLOB '*XFER*'")


def table(con, title, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = [[("" if v is None else str(v)) for v in r] for r in cur.fetchall()]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print(f"\n{title}")
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows or [["(none)"] + [""] * (len(cols) - 1)]:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(r, widths)))


def main() -> None:
    if not DB.exists():
        sys.exit(f"No database at {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print(f"Finance diagnose (counts only) for {DB.parent.name}/finance.db")
    table(con, "Accounts (numbered, not named)", """
        SELECT a.id AS acct, a.kind, (a.last4 IS NOT NULL) AS has_number, count(t.id) AS rows,
               round(avg(t.amount > 0), 2) AS share_money_in, count(DISTINCT substr(t.date, 1, 7)) AS months,
               (SELECT count(*) FROM balances b WHERE b.account_id = a.id) AS balance_points
        FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id GROUP BY a.id""")
    table(con, "Categories by account type and direction", """
        SELECT a.kind AS acct_kind, coalesce(c.kind, 'uncategorized') AS category_kind,
               sum(t.amount > 0) AS money_in, sum(t.amount < 0) AS money_out
        FROM transactions t JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id
        GROUP BY 1, 2""")
    table(con, "Where categories came from", """
        SELECT coalesce(t.category_source, 'none') AS source, coalesce(c.kind, 'uncategorized') AS category_kind, count(*) AS rows
        FROM transactions t LEFT JOIN categories c ON c.id = t.category_id GROUP BY 1, 2""")
    table(con, "Payment- or transfer-worded rows", f"""
        SELECT coalesce(c.kind, 'uncategorized') AS category_kind, sum(t.amount > 0) AS money_in, sum(t.amount < 0) AS money_out
        FROM transactions t LEFT JOIN categories c ON c.id = t.category_id WHERE {TRANSFERISH} GROUP BY 1""")
    table(con, "Possible duplicates (same account, date and amount from different files)", """
        SELECT count(*) AS groups, coalesce(sum(n - 1), 0) AS extra_rows FROM (
          SELECT count(*) AS n, count(DISTINCT import_id) AS files FROM transactions
          GROUP BY account_id, date, amount HAVING n > 1 AND files > 1)""")
    table(con, "Rules", """
        SELECT coalesce(r.direction, 'both') AS direction, c.kind AS category_kind, count(*) AS rules
        FROM rules r JOIN categories c ON c.id = r.category_id GROUP BY 1, 2""")
    table(con, "Imports", "SELECT parser, count(*) AS files, sum(rows_found) AS found, sum(rows_added) AS added, "
                          "sum(coalesce(rows_skipped, 0)) AS skipped FROM imports GROUP BY parser")


if __name__ == "__main__":
    main()
