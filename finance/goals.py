"""Savings goals and their progress.

Progress is what was already saved when the goal was set, plus the net money that has
flowed into the goal's linked account since then (e.g. transfers into savings). A goal
without an account only moves when its saved amount is updated by hand or through chat.
"""
import math
import sqlite3
from datetime import date


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.0


def _add_months(d: date, months: float) -> date:
    whole = int(math.ceil(months))
    y, m = divmod(d.month - 1 + whole, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def full_months_back(n: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    out, y, m = [], today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
    return out


def account_pace(con: sqlite3.Connection, account_id: int) -> float:
    """Average net inflow per month into an account over the last 3 full months."""
    months = full_months_back(3)
    total = con.execute(
        f"SELECT coalesce(sum(amount), 0) FROM transactions WHERE account_id = ? AND substr(date, 1, 7) IN "
        f"({','.join('?' * len(months))})", (account_id, *months)).fetchone()[0]
    return total / 3


def progress(con: sqlite3.Connection, g: dict) -> dict:
    today = date.today()
    contributed = 0.0
    pace = None
    if g.get("account_id"):
        contributed = con.execute(
            "SELECT coalesce(sum(amount), 0) FROM transactions WHERE account_id = ? AND date >= ?",
            (g["account_id"], g["start_date"])).fetchone()[0]
        pace = account_pace(con, g["account_id"])
    saved = round(g["saved_start"] + contributed, 2)
    remaining = max(0.0, g["target_amount"] - saved)
    out = dict(g, saved=saved, remaining=round(remaining, 2), pct=min(100.0, saved / g["target_amount"] * 100)
               if g["target_amount"] else 100.0, pace=round(pace, 2) if pace is not None else None,
               needed_per_month=None, months_left=None, projected_date=None, state="no_pace")
    if g.get("target_date"):
        left = max(_months_between(today, date.fromisoformat(g["target_date"])), 0.0)
        out["months_left"] = round(left, 1)
        out["needed_per_month"] = round(remaining / max(left, 1.0), 2)
    if remaining <= 0:
        out["state"] = "done"
    elif pace is not None and pace > 0:
        out["projected_date"] = _add_months(today, remaining / pace).isoformat()
        if out["needed_per_month"] is not None:
            out["state"] = "on_track" if pace >= out["needed_per_month"] * 0.95 else "behind"
        else:
            out["state"] = "on_track"
    elif pace is not None:
        out["state"] = "behind"
    return out


def list_goals(con: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    sql = """SELECT g.*, a.name AS account FROM goals g LEFT JOIN accounts a ON a.id = g.account_id"""
    if not include_archived:
        sql += " WHERE g.status != 'archived'"
    sql += " ORDER BY coalesce(g.target_date, '9999'), g.id"
    return [progress(con, dict(r)) for r in con.execute(sql)]


def account_by_name(con: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    r = con.execute("SELECT id FROM accounts WHERE lower(name) = lower(?)", (name.strip(),)).fetchone()
    if not r:
        r = con.execute("SELECT id FROM accounts WHERE lower(name) LIKE lower(?)", (f"%{name.strip()}%",)).fetchone()
    return r["id"] if r else None


def create(con: sqlite3.Connection, name: str, target_amount: float, target_date: str | None = None,
           saved_so_far: float = 0.0, account_id: int | None = None, note: str | None = None) -> int:
    return con.execute(
        "INSERT INTO goals(name, target_amount, target_date, saved_start, start_date, account_id, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name.strip(), float(target_amount), target_date or None, float(saved_so_far or 0),
         date.today().isoformat(), account_id, note)).lastrowid


FIELDS = {"name", "target_amount", "target_date", "account_id", "status", "note"}


def update(con: sqlite3.Connection, goal_id: int, changes: dict) -> None:
    if changes.get("saved_so_far") is not None:
        # Re-base: the new figure is what's saved today, and account inflows count from today on.
        con.execute("UPDATE goals SET saved_start = ?, start_date = ? WHERE id = ?",
                    (float(changes["saved_so_far"]), date.today().isoformat(), goal_id))
    sets = {k: v for k, v in changes.items() if k in FIELDS and v is not None}
    if sets:
        con.execute(f"UPDATE goals SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), goal_id))
