"""Accounts with their type, balance history and activity summary."""
import sqlite3

from . import db


def balance_history(con: sqlite3.Connection, account_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT date, balance, source FROM balances WHERE account_id = ? ORDER BY date", (account_id,))]


def balance_on(con: sqlite3.Connection, account_id: int, day: str) -> float | None:
    """Latest known balance on or before a day (or the earliest one after it, if none before)."""
    r = con.execute("SELECT balance FROM balances WHERE account_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                    (account_id, day)).fetchone()
    if not r:
        r = con.execute("SELECT balance FROM balances WHERE account_id = ? AND date > ? ORDER BY date LIMIT 1",
                        (account_id, day)).fetchone()
    return r["balance"] if r else None


def list_accounts(con: sqlite3.Connection) -> list[dict]:
    out = []
    for a in con.execute("SELECT * FROM accounts ORDER BY name"):
        a = dict(a)
        hist = balance_history(con, a["id"])
        stats = con.execute("SELECT count(*) n, min(date) first, max(date) last FROM transactions WHERE account_id = ?",
                            (a["id"],)).fetchone()
        flows = {r["k"]: r["total"] for r in con.execute(
            "SELECT CASE WHEN amount > 0 THEN 'in' ELSE 'out' END k, sum(amount) total FROM transactions "
            "WHERE account_id = ? AND date >= date('now', '-12 months') GROUP BY k", (a["id"],))}
        a.update(history=hist, latest=hist[-1] if hist else None, transactions=stats["n"],
                 first_date=stats["first"], last_date=stats["last"],
                 in_12m=round(flows.get("in") or 0, 2), out_12m=round(-(flows.get("out") or 0), 2),
                 in_budget=a["kind"] not in db.INVEST_KINDS)
        out.append(a)
    return out
