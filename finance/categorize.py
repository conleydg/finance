"""Categorization: the user's merchant rules first, then the local model for the rest."""
import sqlite3

from . import db, llm

BATCH = 40

PROMPT = """Assign each bank transaction to exactly one budget category.
Categories: {cats}
Guidance: "Transfer" is money moving between the holder's own accounts, including credit card payments
and transfers to savings or brokerage. "Income" is payroll, interest earned, and other money received that
is not a refund (refunds go in the category of the original purchase). Use "Other" only when nothing fits.
{examples}
Transactions (amount negative = money out):
{rows}"""


def _schema(names: list[str]) -> dict:
    return {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "category": {"type": "string", "enum": names}},
        "required": ["i", "category"]}}}, "required": ["items"]}


def apply_rules(con: sqlite3.Connection) -> int:
    cur = con.execute("""
        UPDATE transactions SET category_id = r.category_id, category_source = 'rule'
        FROM rules r WHERE r.merchant = transactions.merchant
          AND (transactions.category_source IS NULL OR transactions.category_source = 'model')
          AND transactions.account_id IN """ + db.BUDGET_ACCOUNTS)
    return cur.rowcount


def categorize_pending(progress=None) -> dict:
    with db.connect() as con:
        by_rule = apply_rules(con)
        cats = db.categories(con)
        names = [c["name"] for c in cats]
        ids = {c["name"]: c["id"] for c in cats}
        pending = [dict(r) for r in con.execute(
            "SELECT id, description, amount FROM transactions WHERE category_id IS NULL "
            f"AND account_id IN {db.BUDGET_ACCOUNTS} ORDER BY date")]
        examples = [dict(r) for r in con.execute("""
            SELECT t.description, c.name AS category FROM transactions t
            JOIN categories c ON c.id = t.category_id WHERE t.category_source = 'user'
            GROUP BY t.merchant ORDER BY max(t.id) DESC LIMIT 30""")]
    if not pending:
        return {"by_rule": by_rule, "by_model": 0, "model": None}
    if not llm.available():
        return {"by_rule": by_rule, "by_model": 0, "model": None, "error": f"Ollama model {llm.MODEL} isn't reachable"}
    ex = ""
    if examples:
        ex = "The account holder has labelled these before; follow their preferences:\n" + "\n".join(
            f"- {e['description']} => {e['category']}" for e in examples) + "\n"
    done = 0
    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        if progress:
            progress(f"Categorizing {start + 1}-{start + len(batch)} of {len(pending)} with the local model")
        rows = "\n".join(f"{i}. {t['description']} | {t['amount']:.2f}" for i, t in enumerate(batch))
        res = llm.chat_json(PROMPT.format(cats=", ".join(names), examples=ex, rows=rows), _schema(names))
        with db.connect() as con:
            for item in res.get("items", []):
                i, cat = item.get("i"), item.get("category")
                if isinstance(i, int) and 0 <= i < len(batch) and cat in ids:
                    con.execute("UPDATE transactions SET category_id = ?, category_source = 'model' "
                                "WHERE id = ? AND category_id IS NULL", (ids[cat], batch[i]["id"]))
                    done += 1
    return {"by_rule": by_rule, "by_model": done, "model": llm.MODEL}


def set_category(tx_id: int, category_id: int, remember: bool = True) -> int:
    """User correction. Also teaches a rule for the merchant and applies it to past
    transactions the user hasn't set by hand. Returns how many others changed."""
    with db.connect() as con:
        row = con.execute("SELECT merchant FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        con.execute("UPDATE transactions SET category_id = ?, category_source = 'user' WHERE id = ?",
                    (category_id, tx_id))
        if not remember or not row:
            return 0
        con.execute("INSERT INTO rules(merchant, category_id) VALUES (?, ?) "
                    "ON CONFLICT(merchant) DO UPDATE SET category_id = excluded.category_id",
                    (row["merchant"], category_id))
        cur = con.execute("""UPDATE transactions SET category_id = ?, category_source = 'rule'
                             WHERE merchant = ? AND id != ? AND (category_source IS NULL OR category_source != 'user')""",
                          (category_id, row["merchant"], tx_id))
        return cur.rowcount
