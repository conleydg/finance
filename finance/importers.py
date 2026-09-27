"""Statement parsers. Each returns a list of Txn (date, description, amount) with
amount negative for money out and positive for money in."""
import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime

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


def parse_csv(data: bytes) -> tuple[list[Txn], str]:
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
    txns = []
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
    return txns, "csv"


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


PDF_PROMPT = """You are extracting transactions from one page of a bank or credit card statement.
Return every individual transaction on this page (purchases, payments, deposits, withdrawals, fees, interest).
Skip balances, totals, summaries, and rewards. Use YYYY-MM-DD dates; the statement period is {period}.
amount: negative when money leaves the account holder (purchases, withdrawals, fees, checks),
positive when money comes in (deposits, payroll, refunds, and payments TO a credit card from the holder's
point of view on a card statement are positive). Copy descriptions as written.

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


def _amount_in_text(amount: float, text: str) -> bool:
    a = f"{abs(amount):,.2f}"
    return a in text or a.replace(",", "") in text


def _model_parse(pages: list[str], progress=None) -> tuple[list[Txn], int]:
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


def parse_pdf(data: bytes, progress=None) -> tuple[list[Txn], str, int]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    if not any(p.strip() for p in pages):
        raise ImportError_("This PDF has no text layer (probably a scan). Scanned statements aren't supported yet.")
    if llm.available():
        txns, dropped = _model_parse(pages, progress)
        if txns:
            return txns, "pdf-model", dropped
    txns = _regex_parse(pages)
    if not txns:
        raise ImportError_("Couldn't find transactions in this PDF.")
    return txns, "pdf-regex", 0
