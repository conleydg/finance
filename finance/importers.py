"""Statement parsers. Each returns a list of Txn (date, description, amount) with
amount negative for money out and positive for money in."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import llm


@dataclass
class Txn:
    date: str
    description: str
    amount: float


class ImportError_(Exception):
    pass


# ---------- shared helpers ----------

DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y/%m/%d", "%m-%d-%Y", "%m-%d-%y",
                "%d %b %Y", "%b %d, %Y", "%b %d %Y", "%d-%b-%Y", "%d-%b-%y", "%B %d, %Y", "%Y%m%d"]


def parse_date(s: str) -> str | None:
    s = s.strip().split("T")[0].split(" 00:")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


AMOUNT_RE = re.compile(r"^\(?-?\+?\$?\s*-?[\d,]*\.?\d+\)?(\s*(CR|DR|-))?$", re.I)


def parse_amount(s: str) -> float | None:
    s = (s or "").strip().replace("−", "-")
    if not s or not AMOUNT_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")") or "-" in s
    credit = s.upper().endswith("CR")
    debit = s.upper().endswith("DR")
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits == ".":
        return None
    val = float(digits)
    if neg or debit:
        val = -val
    if credit:
        val = abs(val)
    return val


PREFIXES = re.compile(
    r"^(SQ ?\*|TST ?\*|SP ?\*?|PAYPAL ?\*|PP ?\*|POS |DEBIT CARD PURCHASE |CHECKCARD |PURCHASE AUTHORIZED ON \S+ |"
    r"RECURRING PAYMENT |ACH (DEBIT|CREDIT) |ONLINE |PREAUTHORIZED DEBIT |DBT CRD \S+ |CARD \d+ )+", re.I)


def merchant_key(desc: str) -> str:
    s = PREFIXES.sub("", desc.upper().strip())
    s = re.sub(r"\d{2}/\d{2}(/\d{2,4})?", " ", s)
    s = re.sub(r"[#*]?\d[\d\-]*", " ", s)          # store numbers, refs, phone numbers
    s = re.sub(r"\b(WWW\.|\.COM|\.NET|HTTPS?)\b", " ", s)
    s = re.sub(r"[^A-Z& ]", " ", s)
    words = [w for w in s.split() if len(w) > 1 or w == "&"]
    return " ".join(words[:3]) or desc.upper().strip()[:30]


def fingerprints(account_id: int, txns: list[Txn]) -> list[str]:
    """Stable per-file keys: re-importing a file adds nothing, but two identical
    purchases on the same day in one file are both kept."""
    seen: dict[tuple, int] = {}
    out = []
    for t in txns:
        key = (account_id, t.date, round(t.amount, 2), t.description.strip().upper())
        n = seen.get(key, 0)
        seen[key] = n + 1
        out.append(hashlib.sha1(repr(key + (n,)).encode()).hexdigest())
    return out


def apply_sign(txns: list[Txn], sign: str) -> tuple[list[Txn], bool]:
    """sign: auto | asis | flip. Some card exports (Amex, Discover) list purchases as
    positive numbers; auto flips when most rows are positive and payments are negative."""
    flip = sign == "flip"
    if sign == "auto" and txns:
        pos = sum(1 for t in txns if t.amount > 0)
        payment_neg = any(t.amount < 0 and re.search(r"PAYMENT|THANK YOU|AUTOPAY", t.description, re.I)
                          for t in txns)
        flip = pos / len(txns) > 0.7 and payment_neg
    if flip:
        txns = [Txn(t.date, t.description, -t.amount) for t in txns]
    return txns, flip


# ---------- CSV ----------

DATE_COLS = ["transaction date", "trans. date", "trans date", "date", "posted date", "posting date", "post date"]
DESC_COLS = ["description", "payee", "merchant", "name", "transaction description", "details", "memo",
             "original description"]
AMOUNT_COLS = ["amount", "transaction amount", "amount (usd)", "net amount"]
DEBIT_COLS = ["debit", "debits", "withdrawal", "withdrawals", "withdrawal amount", "debit amount", "money out"]
CREDIT_COLS = ["credit", "credits", "deposit", "deposits", "deposit amount", "credit amount", "money in"]


def _pick(header: list[str], names: list[str]) -> int | None:
    norm = [h.strip().lower() for h in header]
    for n in names:
        if n in norm:
            return norm.index(n)
    return None


BALANCE_COLS = ["balance", "running bal.", "running balance", "running bal", "available balance", "ending balance"]


def parse_csv(data: bytes) -> Parsed:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    # Some banks put account info above the header, so look for it.
    for hi, header in enumerate(rows[:25]):
        d, desc = _pick(header, DATE_COLS), _pick(header, DESC_COLS)
        amt, deb, cred = _pick(header, AMOUNT_COLS), _pick(header, DEBIT_COLS), _pick(header, CREDIT_COLS)
        if d is not None and desc is not None and (amt is not None or deb is not None or cred is not None):
            break
    else:
        raise ImportError_("Couldn't find date, description and amount columns in this CSV.")
    bal_col = _pick(header, BALANCE_COLS)
    txns, balances = [], {}
    for r in rows[hi + 1:]:
        if not any(c.strip() for c in r):
            continue
        get = lambda i: r[i] if i is not None and i < len(r) else ""
        day = parse_date(get(d))
        if not day:
            continue
        if amt is not None and get(amt).strip():
            value = parse_amount(get(amt))
        else:
            out_, in_ = parse_amount(get(deb)), parse_amount(get(cred))
            if out_ is None and in_ is None:
                continue
            value = (abs(in_) if in_ else 0.0) - (abs(out_) if out_ else 0.0)
        if value is None:
            continue
        description = " ".join(get(desc).split())
        memo = _pick(header, ["memo"])
        if not description and memo is not None:
            description = " ".join(get(memo).split())
        txns.append(Txn(day, description or "(no description)", value))
        if bal_col is not None:
            b = parse_amount(get(bal_col))
            if b is not None:
                # Files are newest-first or oldest-first; keep the end-of-day figure either way (fixed below).
                balances.setdefault(day, []).append(b)
    points = []
    if balances:
        newest_first = len(txns) > 1 and txns[0].date > txns[-1].date
        points = [(d, v[0] if newest_first else v[-1]) for d, v in sorted(balances.items())]
    dates = sorted(t.date for t in txns)
    return Parsed(txns, "csv", balances=points, period_start=dates[0] if dates else None,
                  period_end=dates[-1] if dates else None, text="\n".join(text.splitlines()[:40])[:6000])


# ---------- PDF ----------

LINE_RE = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+)?"
    r"(?P<desc>.+?)\s+(?P<amt>\(?-?\$?[\d,]+\.\d{2}\)?(?:\s?CR)?-?)(?:\s+\(?-?\$?[\d,]+\.\d{2}\)?)?$")
PERIOD_RE = re.compile(r"(\d{1,2}/\d{1,2}/(\d{2,4}))|([A-Z][a-z]+ \d{1,2},? (\d{4}))")


def _statement_year_end(text: str) -> date | None:
    dates = []
    for m in PERIOD_RE.finditer(text[:3000]):
        d = parse_date(m.group(0))
        if d:
            dates.append(date.fromisoformat(d))
    return max(dates) if dates else None


def _regex_parse(pages: list[str]) -> list[Txn]:
    end = _statement_year_end("\n".join(pages)) or date.today()
    out = []
    for page in pages:
        for line in page.splitlines():
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            ds = m.group("date")
            if ds.count("/") == 1:
                mo, dy = map(int, ds.split("/"))
                yr = end.year if mo <= end.month else end.year - 1   # Dec charges on a Jan statement
                try:
                    day = date(yr, mo, dy).isoformat()
                except ValueError:
                    continue
            else:
                day = parse_date(ds)
            amt = parse_amount(m.group("amt"))
            if day and amt is not None:
                out.append(Txn(day, " ".join(m.group("desc").split()), amt))
    return out


PDF_PROMPT = """You are extracting transactions from one page of a financial account statement
(bank, credit card, retirement plan such as a 401(k) or IRA, or brokerage).
Return every individual dated transaction on this page: purchases, payments, deposits, withdrawals, fees, interest,
and for retirement or brokerage accounts contributions, employer match, dividends, withdrawals and fees.
Skip balances, totals, summaries, rewards, holdings or positions tables, and exchanges between funds inside the
same account. Use YYYY-MM-DD dates; the statement period is {period}.
amount: negative when money leaves the account (purchases, withdrawals, fees, checks),
positive when money comes in (deposits, payroll, refunds, contributions, employer match, dividends; payments TO a
credit card are positive on a card statement). Copy descriptions as written.

Page text:
<<<
{page}
>>>"""

PDF_SCHEMA = {
    "type": "object",
    "properties": {"transactions": {"type": "array", "items": {
        "type": "object",
        "properties": {"date": {"type": "string"}, "description": {"type": "string"},
                       "amount": {"type": "number"}},
        "required": ["date", "description", "amount"]}}},
    "required": ["transactions"],
}

META_PROMPT = """From this financial statement text, give the statement period and the account's total balance
at the start and end of the period (the whole account's value, not one fund or one line). Use YYYY-MM-DD dates.
Some statements only show one balance "as of" a date: use that as ending_balance and statement_date.
Use null for anything the text doesn't state.

Statement text:
<<<
{text}
>>>"""

META_SCHEMA = {
    "type": "object",
    "properties": {"period_start": {"type": ["string", "null"]}, "period_end": {"type": ["string", "null"]},
                   "beginning_balance": {"type": ["number", "null"]}, "ending_balance": {"type": ["number", "null"]},
                   "statement_date": {"type": ["string", "null"]}},
    "required": ["period_start", "period_end", "beginning_balance", "ending_balance", "statement_date"],
}


@dataclass
class Parsed:
    txns: list[Txn]
    parser: str
    dropped: int = 0
    period_start: str | None = None
    period_end: str | None = None
    beginning_balance: float | None = None
    ending_balance: float | None = None
    balances: list[tuple[str, float]] | None = None   # (date, balance) points, e.g. from a CSV balance column
    text: str = ""                                    # start of the document, for identifying the account

    def balance_points(self) -> list[tuple[str, float]]:
        """Every dated balance this file tells us about. A beginning balance is the balance at the end
        of the day before the period starts, so consecutive statements land on the same point."""
        pts = dict(self.balances or [])
        if self.period_end and self.ending_balance is not None:
            pts[self.period_end] = self.ending_balance
        if self.period_start and self.beginning_balance is not None:
            day = (date.fromisoformat(self.period_start) - timedelta(days=1)).isoformat()
            pts.setdefault(day, self.beginning_balance)
        return sorted(pts.items())


def _amount_in_text(amount: float, text: str) -> bool:
    a = f"{abs(amount):,.2f}"
    return a in text or a.replace(",", "") in text


RANGE_RES = [
    re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:-|–|to|through|thru)\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I),
    re.compile(r"([A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})\s*(?:-|–|to|through|thru)\s*([A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})", re.I),
]
MONEY = r"\$?\s?\(?(-?[\d,]+\.\d{2})\)?"
BEGIN_RE = re.compile(r"(?:beginning|opening|starting|previous)\s+(?:account\s+)?(?:balance|value)[^\n\d$(-]{0,40}" + MONEY, re.I)
END_RE = re.compile(r"(?:ending|closing|new)\s+(?:account\s+)?(?:balance|value)[^\n\d$(-]{0,40}" + MONEY, re.I)
# Balance-only statements often just say "Total account value" or "Balance as of 06/30/2026".
VALUE_RE = re.compile(r"(?:total\s+)?(?:account|portfolio|plan|vested)\s+(?:value|balance)(?:\s+as\s+of\s+(?P<asof>[\w/ ,]{6,20}?))?"
                      r"[^\n\d$(-]{0,30}" + MONEY, re.I)
ASOF_RE = re.compile(r"balance\s+(?:as\s+of|on)\s+(?P<asof>[\w/ ,]{6,20}?)[:\s]+" + MONEY, re.I)


def _date_any(s: str) -> str | None:
    s = s.replace(".", "").replace(",", ", ").replace(",  ", ", ")
    return parse_date(s) or parse_date(s.replace(",", ""))


def _regex_meta(text: str) -> dict:
    meta: dict = {}
    for rx in RANGE_RES:
        m = rx.search(text)
        if m:
            a, b = _date_any(m.group(1)), _date_any(m.group(2))
            if a and b and a <= b:
                meta.update(period_start=a, period_end=b)
                break
    for key, rx in (("beginning_balance", BEGIN_RE), ("ending_balance", END_RE)):
        m = rx.search(text)
        if m:
            meta[key] = parse_amount(m.group(1))
    if meta.get("ending_balance") is None:
        for rx in (ASOF_RE, VALUE_RE):
            m = rx.search(text)
            if m:
                meta["ending_balance"] = parse_amount(m.group(len(m.groups())))
                asof = m.groupdict().get("asof")
                if asof and not meta.get("period_end"):
                    meta["period_end"] = _date_any(asof.strip())
                break
    return meta


def statement_meta(pages: list[str]) -> dict:
    text = "\n".join(pages[:2] + (pages[-1:] if len(pages) > 2 else []))[:14000]
    meta = _regex_meta(text)
    if llm.available():
        try:
            m = llm.chat_json(META_PROMPT.format(text=text), META_SCHEMA)
        except Exception:
            m = {}
        for k in ("period_start", "period_end"):
            d = parse_date(str(m.get(k) or ""))
            if d:
                meta[k] = d
        if not meta.get("period_end"):
            meta["period_end"] = parse_date(str(m.get("statement_date") or "")) or None
        for k in ("beginning_balance", "ending_balance"):
            v = m.get(k)
            # Only trust a balance the model read if that figure is actually in the statement.
            if isinstance(v, (int, float)) and _amount_in_text(v, text):
                meta[k] = float(v)
    if meta.get("period_start") and meta.get("period_end") and meta["period_start"] > meta["period_end"]:
        meta.pop("period_start")
    return meta


def _model_parse(pages: list[str], progress=None, period: str | None = None) -> tuple[list[Txn], int]:
    if not period:
        end = _statement_year_end("\n".join(pages))
        period = f"ending around {end.isoformat()}" if end else "unknown"
    out, dropped = [], 0
    for i, page in enumerate(pages):
        if progress:
            progress(f"Reading PDF page {i + 1} of {len(pages)} with the local model")
        if not page.strip() or not re.search(r"\d+\.\d{2}", page):
            continue
        res = llm.chat_json(PDF_PROMPT.format(period=period, page=page[:12000]), PDF_SCHEMA)
        for t in res.get("transactions", []):
            day = parse_date(str(t.get("date", "")))
            try:
                amt = float(t.get("amount"))
            except (TypeError, ValueError):
                continue
            # Guard against invented rows: the amount must literally appear on the page.
            if not day or not _amount_in_text(amt, page):
                dropped += 1
                continue
            out.append(Txn(day, " ".join(str(t.get("description", "")).split()), amt))
    return out, dropped


def parse_pdf(data: bytes, progress=None) -> Parsed:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    if not any(p.strip() for p in pages):
        raise ImportError_("This PDF has no text layer (probably a scan). Scanned statements aren't supported yet.")
    if progress:
        progress("Reading the statement period and balances")
    meta = statement_meta(pages)
    period = (f"{meta['period_start']} to {meta['period_end']}" if meta.get("period_start") and meta.get("period_end")
              else None)
    txns, parser, dropped = [], "pdf-regex", 0
    if llm.available():
        txns, dropped = _model_parse(pages, progress, period)
        parser = "pdf-model"
    if not txns:
        txns, parser = _regex_parse(pages), "pdf-regex"
    if not txns and meta.get("ending_balance") is None:
        raise ImportError_("Couldn't find transactions or a balance in this PDF.")
    return Parsed(txns, parser, dropped, text="\n".join(pages[:2])[:8000],
                  **{k: meta.get(k) for k in ("period_start", "period_end", "beginning_balance", "ending_balance")})
