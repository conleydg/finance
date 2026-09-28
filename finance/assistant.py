"""The Ask chat: the local model answers questions from the user's own numbers.

It reads data through a few read-only tools. It can't change anything itself: saving a
goal or changing budgets becomes a proposal that the user accepts or dismisses in the app.
"""
import calendar
import json
import re
import sqlite3
from datetime import date

from . import db, goals, llm

MAX_ROUNDS = 6
CLAIMS_PROPOSAL = re.compile(r"suggestion below|added (a |the )?(suggestion|proposal)|below for you to (review|confirm)", re.I)
HISTORY = 20

SYSTEM = """You are the assistant inside a private household finance app that runs entirely on the user's Mac.
Today is {today}. Amounts are US dollars. In the data, negative amounts are money out.

How to work:
- Answer from the user's real data. Call the tools for any number you need; never guess or invent figures.
- Keep answers short and concrete: a few sentences or a short list, with the numbers that matter.
- When the user states a savings goal (an amount, and ideally a date), call propose_goal. When they report
  progress or change a goal, call propose_goal_update. When a goal or their question calls for spending
  changes, call propose_budget_changes with specific categories and monthly amounts, and explain the trade-off
  (for example how much sooner the goal is reached). Proposals appear right below your reply as cards the user
  can accept or dismiss; nothing changes until they accept. Say so briefly ("I've added a suggestion below").
- For goal math, use the figures the tools return (needed_per_month, months_left) rather than your own arithmetic.
- If a goal is missing its amount, ask for it instead of proposing.
- You explain the user's own numbers and general budgeting ideas. You are not a licensed financial advisor:
  don't recommend specific investments, securities or tax strategies; for those, say so briefly and suggest a
  licensed professional.

Snapshot of their finances:
{overview}"""


# ---------- data helpers ----------

def _months_with_data(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT substr(date, 1, 7) m FROM transactions "
                                      f"WHERE account_id IN {db.BUDGET_ACCOUNTS} ORDER BY m")]


def overview(con: sqlite3.Connection) -> str:
    months = _months_with_data(con)
    if not months:
        return "No transactions imported yet."
    last3 = [m for m in goals.full_months_back(3) if m in months] or months[-3:]
    marks = ",".join("?" * len(last3))
    kinds = {r["id"]: r for r in db.categories(con)}
    budgets = {r["category_id"]: r["monthly_amount"] for r in con.execute("SELECT * FROM budgets")}
    avg = {r[0]: (r[1] or 0) / len(last3) for r in con.execute(
        f"SELECT category_id, sum(amount) FROM transactions WHERE substr(date, 1, 7) IN ({marks}) "
        f"AND account_id IN {db.BUDGET_ACCOUNTS} GROUP BY 1", last3)}
    this_month = date.today().strftime("%Y-%m")
    mtd = {r[0]: r[1] or 0 for r in con.execute(
        f"SELECT category_id, sum(amount) FROM transactions WHERE date LIKE ? AND account_id IN {db.BUDGET_ACCOUNTS} "
        "GROUP BY 1", (f"{this_month}-%",))}
    income = sum(v for k, v in avg.items() if k in kinds and kinds[k]["kind"] == "income")
    spend = -sum(v for k, v in avg.items() if k in kinds and kinds[k]["kind"] == "expense")
    lines = [f"Data covers {months[0]} to {months[-1]}. Averages below are over {', '.join(last3)}.",
             f"Average monthly income ${income:,.0f}, spending ${spend:,.0f}, kept ${income - spend:,.0f}.",
             "Expense categories (avg per month | this month so far | monthly budget):"]
    for cid, c in sorted(kinds.items(), key=lambda kv: avg.get(kv[0], 0)):
        if c["kind"] != "expense" or (not avg.get(cid) and cid not in budgets and not mtd.get(cid)):
            continue
        b = budgets.get(cid)
        lines.append(f"- {c['name']}: ${-avg.get(cid, 0):,.0f} | ${-mtd.get(cid, 0):,.0f} | "
                     + (f"${b:,.0f}" if b is not None else "no budget"))
    unc = con.execute("SELECT count(*) FROM transactions WHERE category_id IS NULL "
                      f"AND account_id IN {db.BUDGET_ACCOUNTS}").fetchone()[0]
    if unc:
        lines.append(f"{unc} transactions are uncategorized.")
    lines.append("Accounts (retirement and investment accounts are not part of the budget figures above):")
    for a in con.execute("SELECT id, name, kind FROM accounts ORDER BY kind, name"):
        b = con.execute("SELECT date, balance FROM balances WHERE account_id = ? ORDER BY date DESC LIMIT 1",
                        (a["id"],)).fetchone()
        lines.append(f"- {a['name']} ({a['kind']})" + (f": balance ${b['balance']:,.0f} as of {b['date']}" if b else ""))
    gl = goals.list_goals(con)
    if gl:
        lines.append("Goals:")
        for g in gl:
            lines.append(f"- [id {g['id']}] {g['name']}: ${g['saved']:,.0f} of ${g['target_amount']:,.0f}"
                         + (f", by {g['target_date']}" if g["target_date"] else "")
                         + (f", needs ${g['needed_per_month']:,.0f}/month" if g["needed_per_month"] else "")
                         + (f", {g['account']} is getting ${g['pace']:,.0f}/month" if g["pace"] is not None else "")
                         + f" ({g['state'].replace('_', ' ')})")
    else:
        lines.append("Goals: none yet.")
    return "\n".join(lines)


def _category_id(con, name: str | None) -> int | None:
    if not name:
        return None
    r = con.execute("SELECT id FROM categories WHERE lower(name) = lower(?)", (name.strip(),)).fetchone()
    if not r:
        r = con.execute("SELECT id FROM categories WHERE lower(name) LIKE lower(?)", (f"%{name.strip()}%",)).fetchone()
    return r["id"] if r else None


def t_monthly_spending(con, category: str | None = None, months: int = 6) -> dict:
    months = max(1, min(int(months or 6), 24))
    recent = _months_with_data(con)[-months:]
    if not recent:
        return {"months": []}
    marks = ",".join("?" * len(recent))
    sql = (f"SELECT substr(t.date, 1, 7) m, c.name, sum(t.amount) FROM transactions t JOIN categories c "
           f"ON c.id = t.category_id WHERE substr(t.date, 1, 7) IN ({marks}) AND c.kind = 'expense' "
           f"AND t.account_id IN {db.BUDGET_ACCOUNTS}")
    args: list = list(recent)
    if category:
        cid = _category_id(con, category)
        if cid is None:
            return {"error": f"No category called {category}"}
        sql += " AND c.id = ?"
        args.append(cid)
    out: dict = {}
    for m, name, total in con.execute(sql + " GROUP BY 1, 2", args):
        out.setdefault(m, {})[name] = round(-total, 2)
    return {"spending_by_month": out}


def t_search_transactions(con, text: str | None = None, category: str | None = None, month: str | None = None,
                          limit: int = 25) -> dict:
    sql = ("SELECT t.date, t.description, t.amount, c.name AS category, a.name AS account FROM transactions t "
           "JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id WHERE 1=1")
    args: list = []
    if text:
        sql += " AND t.description LIKE ?"
        args.append(f"%{text}%")
    if category:
        sql += " AND t.category_id = ?"
        args.append(_category_id(con, category))
    if month:
        sql += " AND t.date LIKE ?"
        args.append(f"{month[:7]}-%")
    rows = [dict(r) for r in con.execute(sql + " ORDER BY t.date DESC", args)]
    total = round(sum(r["amount"] for r in rows), 2)
    return {"count": len(rows), "net_total": total, "transactions": rows[:max(1, min(int(limit or 25), 50))]}


def t_top_merchants(con, months: int = 3, category: str | None = None) -> dict:
    recent = _months_with_data(con)[-max(1, min(int(months or 3), 24)):]
    if not recent:
        return {"merchants": []}
    marks = ",".join("?" * len(recent))
    sql = (f"SELECT t.merchant, count(*) n, sum(t.amount) total FROM transactions t JOIN categories c "
           f"ON c.id = t.category_id WHERE substr(t.date, 1, 7) IN ({marks}) AND c.kind = 'expense' "
           f"AND t.account_id IN {db.BUDGET_ACCOUNTS}")
    args: list = list(recent)
    if category:
        sql += " AND c.id = ?"
        args.append(_category_id(con, category))
    rows = con.execute(sql + " GROUP BY 1 ORDER BY total LIMIT 15", args)
    return {"months": recent, "merchants": [{"merchant": m, "count": n, "spent": round(-t, 2)} for m, n, t in rows]}


def t_account_balances(con, account: str | None = None) -> dict:
    sql = "SELECT a.name, a.kind, b.date, b.balance FROM balances b JOIN accounts a ON a.id = b.account_id"
    args: list = []
    if account:
        sql += " WHERE lower(a.name) LIKE lower(?)"
        args.append(f"%{account}%")
    out: dict = {}
    for r in con.execute(sql + " ORDER BY a.name, b.date", args):
        out.setdefault(r["name"], {"kind": r["kind"], "balances": []})["balances"].append([r["date"], r["balance"]])
    for v in out.values():
        pts = v["balances"]
        v["latest"] = pts[-1]
        if len(pts) > 24:          # keep the reply small: first, every few, last
            step = len(pts) // 20 + 1
            v["balances"] = pts[:1] + pts[1:-1:step] + pts[-1:]
    return {"accounts": out} if out else {"note": "No balances recorded yet."}


# ---------- proposals (never applied here) ----------

def _norm_date(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    d = int(m.group(3)) if m.group(3) else calendar.monthrange(y, mo)[1]
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def p_goal(con, name: str, target_amount: float, target_date: str | None = None, saved_so_far: float = 0,
           account: str | None = None, note: str | None = None) -> dict:
    if not name or not target_amount or float(target_amount) <= 0:
        return {"error": "A goal needs a name and a positive target amount"}
    acct = goals.account_by_name(con, account)
    preview = goals.progress(con, {"target_amount": float(target_amount), "target_date": _norm_date(target_date),
                                   "saved_start": float(saved_so_far or 0), "start_date": date.today().isoformat(),
                                   "account_id": acct})
    return {"math": {k: preview[k] for k in ("remaining", "months_left", "needed_per_month", "pace", "projected_date")},
            "proposal": {"kind": "goal", "payload": {
        "name": name.strip(), "target_amount": round(float(target_amount), 2), "target_date": _norm_date(target_date),
        "saved_so_far": round(float(saved_so_far or 0), 2), "account_id": acct,
        "account": account if acct else None, "note": note}}}


def p_goal_update(con, goal_id: int, target_amount: float | None = None, target_date: str | None = None,
                  saved_so_far: float | None = None, name: str | None = None, status: str | None = None) -> dict:
    g = con.execute("SELECT * FROM goals WHERE id = ?", (int(goal_id),)).fetchone()
    if not g:
        return {"error": f"No goal with id {goal_id}"}
    changes = {k: v for k, v in {"name": name, "target_amount": target_amount, "target_date": _norm_date(target_date),
                                 "saved_so_far": saved_so_far,
                                 "status": status if status in ("active", "done", "archived") else None}.items()
               if v is not None}
    if not changes:
        return {"error": "Nothing to change"}
    return {"proposal": {"kind": "goal_update", "payload": {"goal_id": g["id"], "goal": g["name"], "changes": changes}}}


def p_budgets(con, changes: list, reason: str | None = None) -> dict:
    current = {r["category_id"]: r["monthly_amount"] for r in con.execute("SELECT * FROM budgets")}
    last3 = goals.full_months_back(3)
    avg = {r[0]: -(r[1] or 0) / 3 for r in con.execute(
        f"SELECT category_id, sum(amount) FROM transactions WHERE substr(date, 1, 7) IN ({','.join('?' * 3)}) "
        "GROUP BY 1", last3)}
    clean = []
    for ch in changes or []:
        cid = _category_id(con, ch.get("category"))
        amt = ch.get("monthly_amount")
        if cid is None or amt is None or float(amt) < 0:
            return {"error": f"Bad change: {ch}. Use an existing expense category and a monthly amount >= 0."}
        name = con.execute("SELECT name FROM categories WHERE id = ?", (cid,)).fetchone()["name"]
        clean.append({"category_id": cid, "category": name, "from": current.get(cid), "to": round(float(amt), 2),
                      "avg_spent": round(avg.get(cid, 0.0), 2)})
    if not clean:
        return {"error": "No changes given"}
    freed = round(sum(max(0.0, c["avg_spent"] - c["to"]) for c in clean), 2)
    return {"math": {"per_category": [{k: c[k] for k in ("category", "from", "to", "avg_spent")} for c in clean],
                     "monthly_saving_vs_recent_spending": freed,
                     "note": "'from' is the current budget; avg_spent is actual spending per month lately. "
                             "Quote these figures, not your own arithmetic."},
            "proposal": {"kind": "budgets", "payload": {"changes": clean, "reason": reason, "freed": freed}}}


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


S, N, I = {"type": "string"}, {"type": "number"}, {"type": "integer"}
TOOLS = [
    _fn("monthly_spending", "Spending per expense category per month for recent months.",
        {"category": dict(S, description="Optional category name"), "months": dict(I, description="How many months, default 6")}),
    _fn("search_transactions", "Find transactions by description text, category and/or month (YYYY-MM). Returns count, net total and rows.",
        {"text": S, "category": S, "month": S, "limit": I}),
    _fn("top_merchants", "Merchants with the most spending over recent months, optionally within one category.",
        {"months": I, "category": S}),
    _fn("account_balances", "Balance history for accounts (from statements), e.g. a 401(k) or IRA over time.",
        {"account": dict(S, description="Optional part of an account name")}),
    _fn("propose_goal", "Suggest saving a new savings goal. The user must accept it in the app.",
        {"name": S, "target_amount": N, "target_date": dict(S, description="YYYY-MM or YYYY-MM-DD, optional"),
         "saved_so_far": dict(N, description="Already saved toward it, default 0"),
         "account": dict(S, description="Optional account whose deposits count toward the goal, e.g. a savings account"),
         "note": S}, ["name", "target_amount"]),
    _fn("propose_goal_update", "Suggest changing an existing goal (use its id from the snapshot). The user must accept it.",
        {"goal_id": I, "target_amount": N, "target_date": S, "saved_so_far": N, "name": S,
         "status": dict(S, description="active, done or archived")}, ["goal_id"]),
    _fn("propose_budget_changes", "Suggest new monthly budgets for one or more expense categories. The user must accept them.",
        {"changes": {"type": "array", "items": {"type": "object", "properties": {"category": S, "monthly_amount": N},
                                                "required": ["category", "monthly_amount"]}},
         "reason": S}, ["changes"]),
]
HANDLERS = {"monthly_spending": t_monthly_spending, "search_transactions": t_search_transactions,
            "top_merchants": t_top_merchants, "account_balances": t_account_balances, "propose_goal": p_goal, "propose_goal_update": p_goal_update,
            "propose_budget_changes": p_budgets}


def _call(con, name: str, args: dict) -> dict:
    fn = HANDLERS.get(name)
    if not fn:
        return {"error": f"Unknown tool {name}"}
    try:
        return fn(con, **(args or {}))
    except TypeError as e:
        return {"error": f"Bad arguments: {e}"}
    except Exception as e:  # keep the chat alive on a bad call
        return {"error": f"{type(e).__name__}: {e}"}


# ---------- the conversation ----------

def history(con: sqlite3.Connection) -> list[dict]:
    msgs = [dict(r) for r in con.execute("SELECT * FROM chat_messages ORDER BY id")]
    props: dict[int, list] = {}
    for r in con.execute("SELECT * FROM proposals ORDER BY id"):
        p = dict(r)
        p["payload"] = json.loads(p["payload"])
        props.setdefault(p["message_id"], []).append(p)
    for m in msgs:
        m["proposals"] = props.get(m["id"], [])
    return msgs


def ask(text: str) -> dict:
    with db.connect() as con:
        con.execute("INSERT INTO chat_messages(role, content) VALUES ('user', ?)", (text,))
        past = [dict(r) for r in con.execute(
            "SELECT role, content FROM chat_messages ORDER BY id DESC LIMIT ?", (HISTORY,))][::-1]
        messages = [{"role": "system", "content": SYSTEM.format(today=date.today().isoformat(), overview=overview(con))}]
        messages += [{"role": m["role"], "content": m["content"]} for m in past]
        proposals: list[dict] = []
        reply = ""
        nudged = False
        for _ in range(MAX_ROUNDS):
            msg = llm.chat(messages, TOOLS)
            calls = msg.get("tool_calls") or []
            if not calls:
                reply = (msg.get("content") or "").strip()
                # The model sometimes says it added a suggestion without calling the tool; make it follow through once.
                if not proposals and not nudged and CLAIMS_PROPOSAL.search(reply):
                    nudged = True
                    messages += [{"role": "assistant", "content": reply},
                                 {"role": "user", "content": "(App note: no suggestion card was created. Call the "
                                  "matching propose_ tool now, then restate your answer.)"}]
                    continue
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                fn = c.get("function", {})
                result = _call(con, fn.get("name", ""), fn.get("arguments") or {})
                if "proposal" in result:
                    proposals.append(result.pop("proposal"))
                    result["ok"] = "Shown to the user below your reply as a suggestion to accept or dismiss."
                messages.append({"role": "tool", "tool_name": fn.get("name", ""),
                                 "content": json.dumps(result, default=str)[:12000]})
        if not reply:
            reply = "Here's a suggestion for you to review." if proposals else \
                "Sorry, I couldn't work that out. Could you rephrase it?"
        mid = con.execute("INSERT INTO chat_messages(role, content) VALUES ('assistant', ?)", (reply,)).lastrowid
        for p in proposals:
            con.execute("INSERT INTO proposals(message_id, kind, payload) VALUES (?, ?, ?)",
                        (mid, p["kind"], json.dumps(p["payload"])))
        return history(con)[-1]


def resolve(proposal_id: int, accept: bool) -> dict:
    with db.connect() as con:
        p = con.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not p:
            raise KeyError(proposal_id)
        if p["status"] != "pending":
            return {"status": p["status"]}
        payload = json.loads(p["payload"])
        if accept:
            if p["kind"] == "goal":
                goals.create(con, payload["name"], payload["target_amount"], payload.get("target_date"),
                             payload.get("saved_so_far", 0), payload.get("account_id"), payload.get("note"))
            elif p["kind"] == "goal_update":
                goals.update(con, payload["goal_id"], payload["changes"])
            elif p["kind"] == "budgets":
                for ch in payload["changes"]:
                    con.execute("INSERT INTO budgets VALUES (?, ?) ON CONFLICT(category_id) DO UPDATE "
                                "SET monthly_amount = excluded.monthly_amount", (ch["category_id"], ch["to"]))
        status = "accepted" if accept else "dismissed"
        con.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))
        return {"status": status}
