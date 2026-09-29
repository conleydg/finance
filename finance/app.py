"""Local personal finance app. Binds to 127.0.0.1; data and inference stay on this Mac."""
import threading
import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import accounts, assistant, categorize, db, goals, identify, importers, llm

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Finance", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.init()
with db.connect() as _con:          # re-apply transfer patterns and rules so fixes reach existing data
    categorize.apply_patterns(_con)
    categorize.apply_rules(_con)

JOBS: dict[str, dict] = {}


@app.middleware("http")
async def no_stale_assets(request, call_next):
    # The Dock app's Chrome window would otherwise keep showing an old page after updates.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _save(con, acct: int, filename: str, parsed: importers.Parsed) -> dict:
    """Insert a parsed file. Rows already present from another file of the same account (same date and
    amount, even if the description is worded differently) are skipped, so overlapping statements are safe."""
    imp = con.execute(
        "INSERT INTO imports(filename, account_id, kind, parser, rows_found, period_start, period_end, ending_balance) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (filename, acct, Path(filename).suffix.lower().lstrip("."), parsed.parser, len(parsed.txns),
         parsed.period_start, parsed.period_end, parsed.ending_balance)).lastrowid
    added, skipped, examples, matched = 0, 0, [], set()
    for t, fp in zip(parsed.txns, importers.fingerprints(acct, parsed.txns)):
        amount = round(t.amount, 2)
        if con.execute("SELECT 1 FROM transactions WHERE fingerprint = ?", (fp,)).fetchone():
            skipped += 1
            continue
        placeholders = ",".join("?" * len(matched)) or "-1"   # NOT IN (NULL) would match nothing
        twin = con.execute(
            f"SELECT id, description FROM transactions WHERE account_id = ? AND date = ? AND amount = ? "
            f"AND import_id != ? AND id NOT IN ({placeholders}) LIMIT 1",
            (acct, t.date, amount, imp, *matched)).fetchone()
        if twin:
            matched.add(twin["id"])
            skipped += 1
            if len(examples) < 5:
                examples.append(f"{t.date} {t.description} {amount:,.2f}")
            continue
        con.execute(
            "INSERT INTO transactions(account_id, import_id, date, description, merchant, amount, fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (acct, imp, t.date, t.description, importers.merchant_key(t.description), amount, fp))
        added += 1
    new_pts, same_pts, conflicts = 0, 0, []
    for day, bal in parsed.balance_points():
        row = con.execute("SELECT balance FROM balances WHERE account_id = ? AND date = ?", (acct, day)).fetchone()
        if row is None:
            con.execute("INSERT INTO balances(account_id, date, balance, import_id, source) VALUES (?, ?, ?, ?, ?)",
                        (acct, day, round(bal, 2), imp, "csv" if parsed.parser == "csv" else "statement"))
            new_pts += 1
        elif abs(row["balance"] - bal) < 0.01:
            same_pts += 1
        else:
            conflicts.append(f"{day}: kept {row['balance']:,.2f}, this file says {bal:,.2f}")
    con.execute("UPDATE imports SET rows_added = ?, rows_skipped = ? WHERE id = ?", (added, skipped, imp))
    return {"added": added, "skipped": skipped, "skipped_examples": examples, "balances_added": new_pts,
            "balances_confirmed": same_pts, "balance_conflicts": conflicts}


def _run_import(job: dict, filename: str, data: bytes, account: str, sign: str, kind: str | None) -> None:
    def progress(msg: str) -> None:
        job["message"] = msg
    try:
        if filename.lower().endswith(".pdf"):
            parsed = importers.parse_pdf(data, progress)
        else:
            parsed = importers.parse_csv(data)
        with db.connect() as con:
            acct = db.account_id(con, account, kind)
            acct_kind = con.execute("SELECT kind FROM accounts WHERE id = ?", (acct,)).fetchone()["kind"]
            # Card-style sign detection only makes sense for spending accounts; contributions are mostly positive.
            if acct_kind in db.INVEST_KINDS and sign == "auto":
                sign = "asis"
            parsed.txns, flipped = importers.apply_sign(parsed.txns, sign)
            result = _save(con, acct, filename, parsed)
        job.update(found=len(parsed.txns), parser=parsed.parser, flipped=flipped, dropped=parsed.dropped,
                   account_kind=acct_kind, period_start=parsed.period_start, period_end=parsed.period_end,
                   ending_balance=parsed.ending_balance, months=sorted({t.date[:7] for t in parsed.txns}), **result)
        if acct_kind not in db.INVEST_KINDS:
            job["categorize"] = categorize.categorize_pending(progress)
        job["status"] = "done"
    except importers.ImportError_ as e:
        job.update(status="error", message=str(e))
    except Exception as e:  # surface anything else in the UI too
        job.update(status="error", message=f"{type(e).__name__}: {e}")


# ---------- staged import: read and identify first, save after review ----------

STAGED: dict[str, dict] = {}


def _item_view(it: dict) -> dict:
    return {k: v for k, v in it.items() if k not in ("parsed", "data")}


def _stage_one(it: dict) -> None:
    try:
        it["message"] = "Reading"
        name = it["filename"]
        parsed = importers.parse_pdf(it.pop("data"), lambda m: it.update(message=m)) if name.lower().endswith(".pdf") \
            else importers.parse_csv(it.pop("data"))
        it["message"] = "Working out which account this is"
        ident = identify.identify(parsed, name)
        contrib = identify.contributions(parsed)
        with db.connect() as con:
            m = identify.match(con, ident)
            known = con.execute("SELECT contributing FROM accounts WHERE id = ?", (m["account_id"],)).fetchone() \
                if m["account_id"] else None
        qs = identify.questions(ident, parsed, contrib, m)
        if known and known["contributing"] is not None:
            qs = [q for q in qs if q["id"] != "contributing"]      # already answered for this account
        it.update(parsed=parsed, ident=ident, contributions=contrib, match=m, questions=qs,
                  summary=identify.summary(ident, parsed, contrib), suggested_name=identify.suggested_name(ident),
                  period_start=parsed.period_start, period_end=parsed.period_end,
                  ending_balance=parsed.ending_balance, transactions=len(parsed.txns), status="ready", message="")
    except importers.ImportError_ as e:
        it.update(status="error", message=str(e))
    except Exception as e:
        it.update(status="error", message=f"{type(e).__name__}: {e}")


@app.post("/api/stage")
async def stage(files: list[UploadFile] = File(...)):
    batch_id = uuid.uuid4().hex[:12]
    items = []
    for i, f in enumerate(files):
        items.append({"id": i, "filename": f.filename or f"file{i}", "status": "waiting", "message": "Waiting",
                      "data": await f.read()})
    batch = STAGED[batch_id] = {"id": batch_id, "items": items, "status": "reading"}

    def run():
        for it in items:
            it["status"] = "reading"
            _stage_one(it)
        batch["status"] = "ready"
    threading.Thread(target=run, daemon=True).start()
    return {"id": batch_id, "status": batch["status"], "items": [_item_view(i) for i in items]}


@app.get("/api/stage/{batch_id}")
def stage_status(batch_id: str):
    b = STAGED.get(batch_id)
    if not b:
        raise HTTPException(404, "That import batch is gone (the app was restarted). Drop the files again.")
    return {"id": b["id"], "status": b["status"], "items": [_item_view(i) for i in b["items"]]}


class NewAccount(BaseModel):
    name: str
    kind: str = "checking"
    institution: str | None = None
    last4: str | None = None
    subtype: str | None = None


class Decision(BaseModel):
    id: int
    skip: bool = False
    account_id: int | None = None
    new_account: NewAccount | None = None
    contributing: int | None = None      # 1, 0 or None
    balance_date: str | None = None
    sign: str = "auto"


class Commit(BaseModel):
    items: list[Decision]


@app.post("/api/stage/{batch_id}/commit")
def stage_commit(batch_id: str, body: Commit):
    b = STAGED.get(batch_id)
    if not b:
        raise HTTPException(404, "That import batch is gone (the app was restarted). Drop the files again.")
    by_id = {it["id"]: it for it in b["items"]}
    job_id = uuid.uuid4().hex[:12]
    job = JOBS[job_id] = {"id": job_id, "status": "running", "message": "Saving", "results": []}

    def run():
        created: dict[str, int] = {}     # the same new account named on several files is created once
        any_budget = False
        try:
            for d in body.items:
                it = by_id.get(d.id)
                res = {"id": d.id, "filename": it["filename"] if it else "?"}
                job["results"].append(res)
                if not it or it.get("status") != "ready" or d.skip:
                    res["not_imported"] = True
                    continue
                job["message"] = f"Saving {it['filename']}"
                parsed = it["parsed"]
                if d.balance_date and not parsed.period_end:
                    parsed.period_end = importers.parse_date(d.balance_date)
                with db.connect() as con:
                    if d.account_id:
                        acct = d.account_id
                    elif d.new_account:
                        na = d.new_account
                        key = (na.name.strip().lower(), na.institution or "", na.last4 or "")
                        acct = created.get(str(key)) or db.create_account(con, na.name, na.kind, na.institution,
                                                                           na.last4, na.subtype)
                        created[str(key)] = acct
                    else:
                        res["error"] = "No account chosen"
                        continue
                    a = con.execute("SELECT * FROM accounts WHERE id = ?", (acct,)).fetchone()
                    # Fill in identity details the account doesn't have yet.
                    ident = it.get("ident") or {}
                    con.execute("UPDATE accounts SET institution = coalesce(institution, ?), last4 = coalesce(last4, ?), "
                                "subtype = coalesce(subtype, ?) WHERE id = ?",
                                (ident.get("institution"), ident.get("last4"), ident.get("subtype"), acct))
                    contributing = 1 if it["contributions"]["count"] else d.contributing
                    if contributing is not None and a["kind"] in db.INVEST_KINDS:
                        con.execute("UPDATE accounts SET contributing = ? WHERE id = ?", (contributing, acct))
                    sign = d.sign
                    if a["kind"] in db.INVEST_KINDS and sign == "auto":
                        sign = "asis"
                    parsed.txns, flipped = importers.apply_sign(parsed.txns, sign)
                    res.update(_save(con, acct, it["filename"], parsed), flipped=flipped, account=a["name"],
                               account_kind=a["kind"], period_start=parsed.period_start,
                               period_end=parsed.period_end, ending_balance=parsed.ending_balance,
                               found=len(parsed.txns))
                    any_budget |= a["kind"] not in db.INVEST_KINDS
                it["status"] = "saved"
            if any_budget:
                job["categorize"] = categorize.categorize_pending(lambda m: job.update(message=m))
            job["status"] = "done"
        except Exception as e:
            job.update(status="error", message=f"{type(e).__name__}: {e}")
    threading.Thread(target=run, daemon=True).start()
    return job


@app.get("/")
def index():
    # Version the asset URLs so a browser can never pair a new page with an old script.
    html = (STATIC / "index.html").read_text()
    for name in ("style.css", "app.js"):
        v = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
    return HTMLResponse(html)


@app.get("/api/status")
def status():
    with db.connect() as con:
        n = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
        accounts = [dict(r) for r in con.execute(f"SELECT {db.ACCOUNT_COLS} FROM accounts ORDER BY name")]
    return {"model": llm.MODEL, "model_ok": llm.available(), "transactions": n, "accounts": accounts,
            "demo": db.DATA_DIR.name == "data-demo"}


@app.post("/api/import")
async def import_file(file: UploadFile = File(...), account: str = Form(...), sign: str = Form("auto"),
                      kind: str = Form("")):
    if sign not in ("auto", "asis", "flip"):
        raise HTTPException(400, "sign must be auto, asis or flip")
    if kind and kind not in db.ACCOUNT_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(db.ACCOUNT_KINDS)}")
    if not account.strip():
        raise HTTPException(400, "Pick or name an account")
    data = await file.read()
    job_id = uuid.uuid4().hex[:12]
    job = JOBS[job_id] = {"id": job_id, "status": "running", "message": "Reading file", "filename": file.filename}
    threading.Thread(target=_run_import, args=(job, file.filename or "upload.csv", data, account.strip(), sign, kind or None),
                     daemon=True).start()
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404)
    return JOBS[job_id]


@app.post("/api/categorize")
def recategorize():
    job_id = uuid.uuid4().hex[:12]
    job = JOBS[job_id] = {"id": job_id, "status": "running", "message": "Categorizing"}

    def run():
        try:
            job["categorize"] = categorize.categorize_pending(lambda m: job.update(message=m))
            job["status"] = "done"
        except Exception as e:
            job.update(status="error", message=f"{type(e).__name__}: {e}")
    threading.Thread(target=run, daemon=True).start()
    return job


@app.get("/api/imports")
def imports():
    with db.connect() as con:
        return [dict(r) for r in con.execute("""
            SELECT i.*, a.name AS account FROM imports i LEFT JOIN accounts a ON a.id = i.account_id
            ORDER BY i.id DESC""")]


@app.delete("/api/imports/{import_id}")
def delete_import(import_id: int):
    with db.connect() as con:
        n = con.execute("DELETE FROM transactions WHERE import_id = ?", (import_id,)).rowcount
        con.execute("DELETE FROM balances WHERE import_id = ?", (import_id,))
        con.execute("DELETE FROM imports WHERE id = ?", (import_id,))
    return {"deleted": n}


@app.get("/api/categories")
def categories():
    with db.connect() as con:
        return db.categories(con)


class NewCategory(BaseModel):
    name: str
    kind: str = "expense"


@app.post("/api/categories")
def add_category(c: NewCategory):
    if c.kind not in ("expense", "income", "transfer"):
        raise HTTPException(400, "kind must be expense, income or transfer")
    with db.connect() as con:
        con.execute("INSERT OR IGNORE INTO categories(name, kind) VALUES (?, ?)", (c.name.strip(), c.kind))
        return db.categories(con)


@app.get("/api/months")
def months():
    with db.connect() as con:
        return [r[0] for r in con.execute("SELECT DISTINCT substr(date, 1, 7) m FROM transactions "
                                          f"WHERE account_id IN {db.BUDGET_ACCOUNTS} ORDER BY m DESC")]


@app.get("/api/transactions")
def transactions(month: str | None = None, category_id: int | None = None, uncategorized: bool = False,
                 q: str | None = None, limit: int = 1000):
    sql = """SELECT t.id, t.date, t.description, t.merchant, t.amount, t.category_id, t.category_source,
                    c.name AS category, a.name AS account, a.kind AS account_kind
             FROM transactions t JOIN accounts a ON a.id = t.account_id
             LEFT JOIN categories c ON c.id = t.category_id WHERE 1=1"""
    args: list = []
    if month:
        sql += " AND t.date LIKE ?"
        args.append(f"{month}-%")
    if category_id:
        sql += " AND t.category_id = ?"
        args.append(category_id)
    if uncategorized:
        sql += " AND t.category_id IS NULL"
    if q:
        sql += " AND t.description LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY t.date DESC, t.id DESC LIMIT ?"
    args.append(limit)
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql, args)]


class CategoryChange(BaseModel):
    category_id: int
    remember: bool = True


@app.patch("/api/transactions/{tx_id}")
def change_category(tx_id: int, body: CategoryChange):
    return {"also_updated": categorize.set_category(tx_id, body.category_id, body.remember)}


def _prev_months(month: str, n: int) -> list[str]:
    y, m = map(int, month.split("-"))
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
    return out


@app.get("/api/budget")
def budget(month: str | None = None):
    month = month or date.today().strftime("%Y-%m")
    prev = _prev_months(month, 3)
    with db.connect() as con:
        cats = {c["id"]: c for c in db.categories(con)}
        budgets = {r["category_id"]: r["monthly_amount"] for r in con.execute("SELECT * FROM budgets")}

        def sums(months: list[str]) -> dict:
            marks = ",".join("?" * len(months))
            return {r[0]: r[1] for r in con.execute(
                f"SELECT category_id, sum(amount) FROM transactions WHERE substr(date, 1, 7) IN ({marks}) "
                f"AND account_id IN {db.BUDGET_ACCOUNTS} GROUP BY category_id", months)}
        cur, hist = sums([month]), sums(prev)
        hist_months = con.execute(
            f"SELECT count(DISTINCT substr(date, 1, 7)) FROM transactions WHERE substr(date, 1, 7) IN "
            f"({','.join('?' * len(prev))}) AND account_id IN {db.BUDGET_ACCOUNTS}", prev).fetchone()[0] or 1
        uncategorized = con.execute(
            "SELECT count(*), coalesce(sum(amount), 0), coalesce(-sum(CASE WHEN amount < 0 THEN amount END), 0) "
            f"FROM transactions WHERE category_id IS NULL AND date LIKE ? AND account_id IN {db.BUDGET_ACCOUNTS}",
            (f"{month}-%",)).fetchone()
    rows, income, spent = [], 0.0, 0.0
    for cid, c in cats.items():
        net = cur.get(cid, 0.0)
        if c["kind"] == "income":
            income += net
        if c["kind"] != "expense":
            continue
        spend = -net                     # refunds reduce spend
        spent += spend
        avg = -hist.get(cid, 0.0) / hist_months + 0.0
        rows.append({"category_id": cid, "category": c["name"], "spent": round(spend, 2) + 0.0,
                     "budget": budgets.get(cid), "avg3": round(avg, 2)})
    rows.sort(key=lambda r: -r["spent"])
    # Uncategorized money out still counts as spending, so the total is never quietly low.
    spent += uncategorized[2]
    return {"month": month, "income": round(income, 2), "spent": round(spent, 2), "net": round(income - spent, 2),
            "budgeted": round(sum(v for k, v in budgets.items() if cats.get(k, {}).get("kind") == "expense"), 2),
            "uncategorized": {"count": uncategorized[0], "amount": round(uncategorized[1], 2),
                              "spent": round(uncategorized[2], 2)},
            "categories": rows}


@app.get("/api/income")
def income(month: str):
    """What counts as income this month, and money that came in but isn't counted (so it can be fixed)."""
    base = ("SELECT t.id, t.date, t.description, t.amount, t.category_id, t.category_source, c.name AS category, "
            "c.kind AS category_kind, a.name AS account FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "LEFT JOIN categories c ON c.id = t.category_id "
            f"WHERE t.date LIKE ? AND t.account_id IN {db.BUDGET_ACCOUNTS} ")
    with db.connect() as con:
        counted = [dict(r) for r in con.execute(base + "AND c.kind = 'income' ORDER BY t.amount DESC", (f"{month}-%",))]
        other = [dict(r) for r in con.execute(
            base + "AND t.amount > 0 AND (c.kind IS NULL OR c.kind != 'income') ORDER BY t.amount DESC", (f"{month}-%",))]
    for r in counted + other:
        r["looks_transfer"] = categorize.is_transfer(r["description"])
    return {"month": month, "counted": counted, "not_counted": other,
            "counted_total": round(sum(r["amount"] for r in counted), 2),
            "not_counted_total": round(sum(r["amount"] for r in other), 2)}


class BudgetSet(BaseModel):
    monthly_amount: float | None


@app.put("/api/budget/{category_id}")
def set_budget(category_id: int, body: BudgetSet):
    with db.connect() as con:
        if body.monthly_amount is None:
            con.execute("DELETE FROM budgets WHERE category_id = ?", (category_id,))
        else:
            con.execute("INSERT INTO budgets VALUES (?, ?) ON CONFLICT(category_id) DO UPDATE "
                        "SET monthly_amount = excluded.monthly_amount", (category_id, body.monthly_amount))
    return {"ok": True}


# ---------- Ask (chat) ----------

class ChatIn(BaseModel):
    message: str


@app.get("/api/chat")
def chat_history():
    with db.connect() as con:
        return assistant.history(con)


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.message.strip():
        raise HTTPException(400, "Type a question")
    if not llm.available():
        raise HTTPException(503, f"The local model ({llm.MODEL}) isn't running")
    return assistant.ask(body.message.strip())


@app.delete("/api/chat")
def clear_chat():
    with db.connect() as con:
        con.execute("DELETE FROM proposals WHERE status = 'pending'")
        con.execute("UPDATE proposals SET message_id = NULL")
        con.execute("DELETE FROM chat_messages")
    return {"ok": True}


@app.post("/api/proposals/{proposal_id}/{action}")
def resolve_proposal(proposal_id: int, action: str):
    if action not in ("accept", "dismiss"):
        raise HTTPException(400, "accept or dismiss")
    try:
        return assistant.resolve(proposal_id, action == "accept")
    except KeyError:
        raise HTTPException(404)


# ---------- Goals ----------

@app.get("/api/goals")
def list_goals():
    with db.connect() as con:
        return goals.list_goals(con)


class GoalIn(BaseModel):
    name: str | None = None
    target_amount: float | None = None
    target_date: str | None = None
    saved_so_far: float | None = None
    account_id: int | None = None
    status: str | None = None


@app.post("/api/goals")
def add_goal(g: GoalIn):
    if not g.name or not g.target_amount or g.target_amount <= 0:
        raise HTTPException(400, "A goal needs a name and a positive amount")
    with db.connect() as con:
        gid = goals.create(con, g.name, g.target_amount, g.target_date or None, g.saved_so_far or 0, g.account_id)
    return {"id": gid}


@app.patch("/api/goals/{goal_id}")
def edit_goal(goal_id: int, g: GoalIn):
    with db.connect() as con:
        goals.update(con, goal_id, g.model_dump(exclude_unset=True))
    return {"ok": True}


@app.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int):
    with db.connect() as con:
        con.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    return {"ok": True}


# ---------- Accounts and balances ----------

@app.get("/api/accounts")
def list_accounts():
    with db.connect() as con:
        return accounts.list_accounts(con)


class AccountIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    institution: str | None = None
    last4: str | None = None
    subtype: str | None = None
    contributing: int | None = None


@app.patch("/api/accounts/{account_id}")
def edit_account(account_id: int, a: AccountIn):
    with db.connect() as con:
        if a.kind:
            if a.kind not in db.ACCOUNT_KINDS:
                raise HTTPException(400, f"kind must be one of {', '.join(db.ACCOUNT_KINDS)}")
            con.execute("UPDATE accounts SET kind = ? WHERE id = ?", (a.kind, account_id))
        if a.name and a.name.strip():
            con.execute("UPDATE accounts SET name = ? WHERE id = ?", (a.name.strip(), account_id))
        for col in ("institution", "last4", "subtype", "contributing"):
            if col in a.model_fields_set:
                con.execute(f"UPDATE accounts SET {col} = ? WHERE id = ?", (getattr(a, col), account_id))
    return {"ok": True}


class BalanceIn(BaseModel):
    date: str
    balance: float


@app.post("/api/accounts/{account_id}/balances")
def add_balance(account_id: int, b: BalanceIn):
    day = importers.parse_date(b.date)
    if not day:
        raise HTTPException(400, "Use a date like 2026-09-30")
    with db.connect() as con:
        con.execute("INSERT INTO balances(account_id, date, balance, source) VALUES (?, ?, ?, 'manual') "
                    "ON CONFLICT(account_id, date) DO UPDATE SET balance = excluded.balance, source = 'manual'",
                    (account_id, day, round(b.balance, 2)))
    return {"ok": True}


@app.delete("/api/accounts/{account_id}/balances/{day}")
def delete_balance(account_id: int, day: str):
    with db.connect() as con:
        con.execute("DELETE FROM balances WHERE account_id = ? AND date = ?", (account_id, day))
    return {"ok": True}
