"""Local personal finance app. Binds to 127.0.0.1; data and inference stay on this Mac."""
import threading
import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import categorize, db, importers, llm

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Finance", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.init()

JOBS: dict[str, dict] = {}


@app.middleware("http")
async def no_stale_assets(request, call_next):
    # The Dock app's Chrome window would otherwise keep showing an old page after updates.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _run_import(job: dict, filename: str, data: bytes, account: str, sign: str) -> None:
    def progress(msg: str) -> None:
        job["message"] = msg
    try:
        dropped = 0
        if filename.lower().endswith(".pdf"):
            txns, parser, dropped = importers.parse_pdf(data, progress)
        else:
            txns, parser = importers.parse_csv(data)
        txns, flipped = importers.apply_sign(txns, sign)
        with db.connect() as con:
            acct = db.account_id(con, account)
            imp = con.execute("INSERT INTO imports(filename, account_id, kind, parser, rows_found) "
                              "VALUES (?, ?, ?, ?, ?)",
                              (filename, acct, Path(filename).suffix.lower().lstrip("."), parser, len(txns))).lastrowid
            added = 0
            for t, fp in zip(txns, importers.fingerprints(acct, txns)):
                cur = con.execute(
                    "INSERT OR IGNORE INTO transactions(account_id, import_id, date, description, merchant, amount, fingerprint) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (acct, imp, t.date, t.description, importers.merchant_key(t.description), round(t.amount, 2), fp))
                added += cur.rowcount
            con.execute("UPDATE imports SET rows_added = ? WHERE id = ?", (added, imp))
        job.update(found=len(txns), added=added, parser=parser, flipped=flipped, dropped=dropped,
                   months=sorted({t.date[:7] for t in txns}))
        job["categorize"] = categorize.categorize_pending(progress)
        job["status"] = "done"
    except importers.ImportError_ as e:
        job.update(status="error", message=str(e))
    except Exception as e:  # surface anything else in the UI too
        job.update(status="error", message=f"{type(e).__name__}: {e}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    with db.connect() as con:
        n = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
        accounts = [dict(r) for r in con.execute("SELECT id, name FROM accounts ORDER BY name")]
    return {"model": llm.MODEL, "model_ok": llm.available(), "transactions": n, "accounts": accounts}


@app.post("/api/import")
async def import_file(file: UploadFile = File(...), account: str = Form(...), sign: str = Form("auto")):
    if sign not in ("auto", "asis", "flip"):
        raise HTTPException(400, "sign must be auto, asis or flip")
    if not account.strip():
        raise HTTPException(400, "Pick or name an account")
    data = await file.read()
    job_id = uuid.uuid4().hex[:12]
    job = JOBS[job_id] = {"id": job_id, "status": "running", "message": "Reading file", "filename": file.filename}
    threading.Thread(target=_run_import, args=(job, file.filename or "upload.csv", data, account.strip(), sign),
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
        return [r[0] for r in con.execute("SELECT DISTINCT substr(date, 1, 7) m FROM transactions ORDER BY m DESC")]


@app.get("/api/transactions")
def transactions(month: str | None = None, category_id: int | None = None, uncategorized: bool = False,
                 q: str | None = None, limit: int = 1000):
    sql = """SELECT t.id, t.date, t.description, t.merchant, t.amount, t.category_id, t.category_source,
                    c.name AS category, a.name AS account
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
                "GROUP BY category_id", months)}
        cur, hist = sums([month]), sums(prev)
        hist_months = con.execute(
            f"SELECT count(DISTINCT substr(date, 1, 7)) FROM transactions WHERE substr(date, 1, 7) IN "
            f"({','.join('?' * len(prev))})", prev).fetchone()[0] or 1
        uncategorized = con.execute("SELECT count(*), coalesce(sum(amount), 0) FROM transactions "
                                    "WHERE category_id IS NULL AND date LIKE ?", (f"{month}-%",)).fetchone()
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
    return {"month": month, "income": round(income, 2), "spent": round(spent, 2), "net": round(income - spent, 2),
            "budgeted": round(sum(v for k, v in budgets.items() if cats.get(k, {}).get("kind") == "expense"), 2),
            "uncategorized": {"count": uncategorized[0], "amount": round(uncategorized[1], 2)},
            "categories": rows}


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
