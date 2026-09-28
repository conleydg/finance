"""Work out which account a statement belongs to, and what to ask before saving it.

The local model reads the start of the statement for the institution, account type and the last
digits of the account number; anything it claims about the number must appear in the text. The
questions come from plain rules about what's missing, so they're predictable.
"""
import re
import sqlite3

from . import db, llm
from .importers import Parsed

PROMPT = """This is the start of a financial statement or export. Identify the account it belongs to.
- institution: the bank, card issuer, brokerage or plan administrator (e.g. "Fidelity", "Chase"), or null.
- account_type: checking, savings, credit (credit card), retirement (401(k), 403(b), 457, IRA, Roth IRA, pension,
  or a plan statement), investment (taxable brokerage, HSA investments, 529 college savings), or other.
- subtype: a short label such as "401(k)", "Roth IRA", "HSA", "529", "Brokerage", or null.
- account_name: the account or plan name as printed (e.g. "Example Corp 401(k) Plan"), or null.
- last4: the last 4 digits of the account number if printed (often shown as ****1234 or ending in 1234), else null.
File name: {filename}

Text:
<<<
{text}
>>>"""

SCHEMA = {
    "type": "object",
    "properties": {
        "institution": {"type": ["string", "null"]},
        "account_type": {"type": "string", "enum": ["checking", "savings", "credit", "retirement", "investment", "other"]},
        "subtype": {"type": ["string", "null"]},
        "account_name": {"type": ["string", "null"]},
        "last4": {"type": ["string", "null"]},
    },
    "required": ["institution", "account_type", "subtype", "account_name", "last4"],
}

LAST4_RE = re.compile(r"(?:account|acct|card|plan)[^\n]{0,40}?(?:ending(?: in)?|number|no\.?|#)?\s*[:#x*•.\-]*\s*(\d{4})\b", re.I)
MASKED_RE = re.compile(r"[x*•]{2,}[\s-]?(\d{4})\b", re.I)
KIND_WORDS = [("retirement", r"401\(?k\)?|403\(?b\)?|\b457\b|\bIRA\b|roth|retirement|pension"),
              ("investment", r"brokerage|\b529\b|\bHSA\b|investment account"),
              ("credit", r"credit card|card ?member|minimum payment|\bvisa\b|mastercard"),
              ("savings", r"savings"),
              ("checking", r"checking")]
CONTRIB_RE = re.compile(r"contribut|deferral|\bEE\b|employee|payroll|salary", re.I)
MATCH_RE = re.compile(r"match|\bER\b|employer|profit shar", re.I)


def _digits_in_text(d: str, text: str) -> bool:
    return bool(d) and re.search(rf"(?<!\d){re.escape(d)}(?!\d)", text) is not None


def identify(parsed: Parsed, filename: str) -> dict:
    text = parsed.text or ""
    out = {"institution": None, "account_type": None, "subtype": None, "account_name": None, "last4": None}
    if llm.available() and text.strip():
        try:
            m = llm.chat_json(PROMPT.format(filename=filename, text=text[:6000]), SCHEMA)
            out.update({k: (v.strip() if isinstance(v, str) and v.strip() else None) for k, v in m.items() if k in out})
        except Exception:
            pass
    if out["last4"]:
        digits = re.sub(r"\D", "", out["last4"])[-4:]
        out["last4"] = digits if len(digits) == 4 and _digits_in_text(digits, text) else None
    if not out["last4"]:
        m = MASKED_RE.search(text) or LAST4_RE.search(text)
        out["last4"] = m.group(1) if m else None
    if out["account_type"] in (None, "other"):
        for kind, rx in KIND_WORDS:
            if re.search(rx, text, re.I):
                out["account_type"] = kind
                break
    if out["account_type"] == "other":
        out["account_type"] = None
    return out


def contributions(parsed: Parsed) -> dict:
    you = [t for t in parsed.txns if t.amount > 0 and CONTRIB_RE.search(t.description) and not MATCH_RE.search(t.description)]
    er = [t for t in parsed.txns if t.amount > 0 and MATCH_RE.search(t.description)]
    return {"count": len(you), "total": round(sum(t.amount for t in you), 2),
            "match_count": len(er), "match_total": round(sum(t.amount for t in er), 2),
            "last_date": max((t.date for t in you), default=None)}


def match(con: sqlite3.Connection, ident: dict) -> dict:
    """Best existing account for this statement: same institution and last 4 digits is a sure match."""
    rows = [dict(r) for r in con.execute(f"SELECT {db.ACCOUNT_COLS} FROM accounts")]
    inst = (ident.get("institution") or "").lower()
    last4 = ident.get("last4")

    def same_inst(a):
        ai = (a["institution"] or "").lower()
        return bool(inst and ai and (inst in ai or ai in inst))

    if last4:
        exact = [a for a in rows if a["last4"] == last4 and (same_inst(a) or not a["institution"] or not inst)]
        if len(exact) == 1:
            return {"account_id": exact[0]["id"], "confidence": "sure",
                    "reason": f"Same account number ending {last4}"}
    kind = ident.get("account_type")
    maybe = [a for a in rows if same_inst(a) and (not kind or a["kind"] == kind) and not (last4 and a["last4"] and a["last4"] != last4)]
    if not maybe and ident.get("account_name"):
        name = ident["account_name"].lower()
        maybe = [a for a in rows if a["name"].lower() == name]
    if len(maybe) == 1:
        return {"account_id": maybe[0]["id"], "confidence": "likely",
                "reason": "Same institution and type" if same_inst(maybe[0]) else "Same name"}
    return {"account_id": None, "confidence": "new" if not maybe else "ambiguous",
            "reason": "No matching account yet" if not maybe else f"{len(maybe)} accounts could match"}


def suggested_name(ident: dict) -> str:
    parts = [ident.get("institution"), ident.get("subtype") or {
        "checking": "Checking", "savings": "Savings", "credit": "Card", "retirement": "Retirement",
        "investment": "Investments"}.get(ident.get("account_type") or "", "Account")]
    name = " ".join(p for p in parts if p) or (ident.get("account_name") or "New account")
    return f"{name} ...{ident['last4']}" if ident.get("last4") else name


def questions(ident: dict, parsed: Parsed, contrib: dict, m: dict) -> list[dict]:
    qs = []
    if m["confidence"] == "ambiguous":
        qs.append({"id": "which", "text": "Several of your accounts could match this statement. Which one is it?"})
    if not ident.get("account_type"):
        qs.append({"id": "type", "text": "What kind of account is this?"})
    if ident.get("account_type") in db.INVEST_KINDS:
        if contrib["count"]:
            pass   # answered by the statement itself
        else:
            qs.append({"id": "contributing", "text": "This statement shows no contributions. Are you still putting money in?",
                       "options": ["Yes", "No", "Not sure"]})
    if parsed.ending_balance is not None and not parsed.period_end:
        qs.append({"id": "balance_date", "text": f"What date is the balance of ${parsed.ending_balance:,.2f} from?"})
    if not parsed.txns and parsed.ending_balance is None:
        qs.append({"id": "nothing", "text": "I couldn't find transactions or a balance in this file. Skip it, or add the balance by hand?"})
    return qs


def summary(ident: dict, parsed: Parsed, contrib: dict) -> list[str]:
    """Plain-language lines on what the model and parser found."""
    lines = []
    what = " ".join(p for p in (ident.get("institution"), ident.get("subtype") or ident.get("account_type")) if p)
    if what or ident.get("last4"):
        lines.append((what or "Account") + (f", ending {ident['last4']}" if ident.get("last4") else ""))
    if parsed.period_start and parsed.period_end:
        lines.append(f"Covers {parsed.period_start} to {parsed.period_end}")
    elif parsed.period_end:
        lines.append(f"As of {parsed.period_end}")
    if parsed.ending_balance is not None:
        lines.append(f"Ending balance ${parsed.ending_balance:,.2f}")
    if parsed.txns:
        lines.append(f"{len(parsed.txns)} transactions")
    elif parsed.ending_balance is not None:
        lines.append("Balance only, no transactions")
    if contrib["count"]:
        lines.append(f"Contributions: {contrib['count']} totalling ${contrib['total']:,.2f}"
                     + (f", plus ${contrib['match_total']:,.2f} employer match" if contrib["match_count"] else ""))
    return lines
