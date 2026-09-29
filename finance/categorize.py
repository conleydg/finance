"""Categorization: card payments and transfers by pattern, then the user's merchant rules, then the
local model for the rest. Money out is never counted as income."""
import re
import sqlite3

from . import db, llm

BATCH = 40

PROMPT = """Assign each bank transaction to exactly one budget category.
Categories: {cats}
Guidance: "Transfer" is money moving between the holder's own accounts, including credit card payments
(on both the card and the bank side) and transfers to savings or brokerage. "Income" is only money received:
payroll, interest earned, benefits, side income; never money going out, and never a refund (refunds go in the
category of the original purchase). Use "Other" only when nothing fits.
{examples}
Transactions (amount negative = money out):
{rows}"""

# Paying a card and moving money between your own accounts: both sides are Transfer, never income or spending.
CARD_ISSUERS = (r"CAPITAL ONE|CAP ONE|AMEX|AMERICAN EXPRESS|DISCOVER|CITI(?:BANK)? ?CARD|CITI\b|CHASE CARD|CHASE CREDIT|"
                r"BARCLAY|SYNCHRONY|APPLECARD|APPLE CARD|GS BANK|BK OF AMER.*CARD|BANK OF AMERICA CARD|WELLS FARGO CARD|"
                r"USAA (?:CREDIT )?CARD|USAA CC|NAVY FEDERAL CARD|US BANK CARD|CREDIT ?CARD|CRCARDPMT|CARDMEMBER")
TRANSFER_RE = re.compile(
    r"PAYMENT\s*[-,]?\s*THANK ?YOU|THANK ?YOU\b.*PAYMENT|PAYMENT RECEIVED|AUTOPAY PAYMENT|AUTOMATIC PAYMENT - THANK|"
    rf"(?:{CARD_ISSUERS}).*\b(?:PMT|PYMT|PAYMENT|EPAY|E-PAYMENT|AUTOPAY|AUTO PAY|ONLINE PMT)\b|"
    rf"\b(?:PMT|PYMT|PAYMENT|EPAY|E-PAYMENT|AUTOPAY)\b.*(?:{CARD_ISSUERS})|"
    r"\bTRANSFER (?:TO|FROM)\b|ONLINE (?:BANKING )?TRANSFER|INTERNAL TRANSFER|FUNDS TRANSFER|\bXFER\b|"
    r"TRANSFER (?:TO|FROM) (?:SAV|CHK|CHECKING|SAVINGS)", re.I)


def _schema(names: list[str]) -> dict:
    return {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "category": {"type": "string", "enum": names}},
        "required": ["i", "category"]}}}, "required": ["items"]}


def is_transfer(description: str) -> bool:
    return bool(TRANSFER_RE.search(description or ""))


def apply_patterns(con: sqlite3.Connection) -> int:
    """Mark card payments and account transfers as Transfer, unless the user set a category by hand.
    Also undo any money-out row the model filed under Income."""
    transfer = con.execute("SELECT id FROM categories WHERE kind = 'transfer' ORDER BY id LIMIT 1").fetchone()
    if not transfer:
        return 0
    rows = con.execute(
        "SELECT t.id, t.description, t.amount, c.kind FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
        f"WHERE (t.category_source IS NULL OR t.category_source IN ('model', 'pattern', 'rule')) "
        f"AND t.account_id IN {db.BUDGET_ACCOUNTS}").fetchall()
    changed = 0
    for r in rows:
        if r["kind"] == "income" and r["amount"] < 0:
            con.execute("UPDATE transactions SET category_id = NULL, category_source = NULL WHERE id = ?", (r["id"],))
            changed += 1
            continue
        if r["kind"] is not None and r["kind"] != "transfer" and con.execute(
                "SELECT 1 FROM transactions WHERE id = ? AND category_source = 'rule'", (r["id"],)).fetchone():
            continue       # the user's own merchant rule wins over the pattern
        if is_transfer(r["description"]):
            if r["kind"] != "transfer":
                con.execute("UPDATE transactions SET category_id = ?, category_source = 'pattern' WHERE id = ?",
                            (transfer["id"], r["id"]))
                changed += 1
    return changed


def apply_rules(con: sqlite3.Connection) -> int:
    cur = con.execute("""
        UPDATE transactions SET category_id = r.category_id, category_source = 'rule'
        FROM rules r WHERE r.merchant = transactions.merchant
          AND (r.direction IS NULL OR (r.direction = 'in' AND transactions.amount > 0)
               OR (r.direction = 'out' AND transactions.amount < 0))
          AND (transactions.category_source IS NULL OR transactions.category_source IN ('model', 'pattern'))
          AND transactions.account_id IN """ + db.BUDGET_ACCOUNTS)
    return cur.rowcount


def categorize_pending(progress=None) -> dict:
    with db.connect() as con:
        by_pattern = apply_patterns(con)
        by_rule = apply_rules(con)
        cats = db.categories(con)
        names = [c["name"] for c in cats]
        ids = {c["name"]: c["id"] for c in cats}
        kinds = {c["name"]: c["kind"] for c in cats}
        pending = [dict(r) for r in con.execute(
            "SELECT id, description, amount FROM transactions WHERE category_id IS NULL "
            f"AND account_id IN {db.BUDGET_ACCOUNTS} ORDER BY date")]
        examples = [dict(r) for r in con.execute("""
            SELECT t.description, c.name AS category FROM transactions t
            JOIN categories c ON c.id = t.category_id WHERE t.category_source = 'user'
            GROUP BY t.merchant ORDER BY max(t.id) DESC LIMIT 30""")]
    out = {"by_pattern": by_pattern, "by_rule": by_rule, "by_model": 0, "model": None}
    if not pending:
        return out
    if not llm.available():
        return dict(out, error=f"{len(pending)} transactions weren't categorized: the local model ({llm.MODEL}) "
                               "isn't running. Start Ollama, then use Categorize uncategorized on the Transactions page.")
    ex = ""
    if examples:
        ex = "The account holder has labelled these before; follow their preferences:\n" + "\n".join(
            f"- {e['description']} => {e['category']}" for e in examples) + "\n"
    done, failed = 0, 0
    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        if progress:
            progress(f"Categorizing {start + 1}-{start + len(batch)} of {len(pending)} with the local model")
        rows = "\n".join(f"{i}. {t['description']} | {t['amount']:.2f}" for i, t in enumerate(batch))
        try:
            res = llm.chat_json(PROMPT.format(cats=", ".join(names), examples=ex, rows=rows), _schema(names))
        except Exception:
            failed += len(batch)       # keep going; these stay uncategorized and are reported
            continue
        with db.connect() as con:
            for item in res.get("items", []):
                i, cat = item.get("i"), item.get("category")
                if not (isinstance(i, int) and 0 <= i < len(batch) and cat in ids):
                    continue
                if kinds[cat] == "income" and batch[i]["amount"] < 0:
                    continue           # money out is never income
                con.execute("UPDATE transactions SET category_id = ?, category_source = 'model' "
                            "WHERE id = ? AND category_id IS NULL", (ids[cat], batch[i]["id"]))
                done += 1
    out.update(by_model=done, model=llm.MODEL)
    left = len(pending) - done
    if left:
        out["error"] = (f"{left} transactions are still uncategorized"
                        + (" because the local model didn't answer for some batches" if failed else "")
                        + ". They show under Uncategorized; use Categorize uncategorized to try again.")
    return out


def set_category(tx_id: int, category_id: int, remember: bool = True) -> int:
    """User correction. Also teaches a rule for the merchant and applies it to past
    transactions the user hasn't set by hand. Returns how many others changed."""
    with db.connect() as con:
        row = con.execute("SELECT merchant, amount FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        con.execute("UPDATE transactions SET category_id = ?, category_source = 'user' WHERE id = ?",
                    (category_id, tx_id))
        if not remember or not row:
            return 0
        # A rule only covers money moving the same way: "count this deposit as income" must not turn the
        # matching payments out into income too.
        direction = "in" if row["amount"] > 0 else "out"
        con.execute("INSERT INTO rules(merchant, category_id, direction) VALUES (?, ?, ?) "
                    "ON CONFLICT(merchant) DO UPDATE SET category_id = excluded.category_id, direction = excluded.direction",
                    (row["merchant"], category_id, direction))
        cur = con.execute("""UPDATE transactions SET category_id = ?, category_source = 'rule'
                             WHERE merchant = ? AND id != ? AND (category_source IS NULL OR category_source != 'user')
                               AND (amount > 0) = ?""",
                          (category_id, row["merchant"], tx_id, direction == "in"))
        return cur.rowcount
