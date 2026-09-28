import pytest

from finance import importers
from tests import make_samples

make_samples.main()
S = make_samples.SAMPLES


def test_chase_checking():
    p = importers.parse_csv((S / "chase_checking.csv").read_bytes())
    txns = p.txns
    assert p.parser == "csv" and len(txns) == 18
    assert p.balances[-1] == ("2026-09-06", 8007.23)           # end-of-day running balance
    assert txns[0].date == "2026-08-01" and txns[0].amount == 4210.55
    assert txns[1].amount == -2150.00
    txns, flipped = importers.apply_sign(txns, "auto")
    assert not flipped


def test_amex_auto_flips():
    txns = importers.parse_csv((S / "amex.csv").read_bytes()).txns
    txns, flipped = importers.apply_sign(txns, "auto")
    assert flipped
    assert txns[0].amount == -15.49                  # purchase is money out
    assert [t.amount for t in txns if "THANK YOU" in t.description] == [812.44]
    assert [t.amount for t in txns if t.date == "2026-08-19"] == [21.99]   # refund is money in


def test_bofa_preamble_and_split_columns():
    txns = importers.parse_csv((S / "bofa_savings.csv").read_bytes()).txns
    assert [(t.date, t.amount) for t in txns] == [("2026-08-16", 500.0), ("2026-08-31", 12.40)]


def test_duplicate_rows_kept_but_reimport_is_stable():
    txns = importers.parse_csv((S / "chase_checking.csv").read_bytes()).txns
    fps = importers.fingerprints(1, txns)
    assert len(set(fps)) == len(fps)                 # two identical coffees both kept
    assert fps == importers.fingerprints(1, txns)    # same file again maps to same keys


def test_pdf_regex_fallback(monkeypatch):
    monkeypatch.setattr(importers.llm, "available", lambda: False)
    make_samples.make_pdf(S / "card_statement.pdf")
    p = importers.parse_pdf((S / "card_statement.pdf").read_bytes())
    txns = p.txns
    assert p.parser == "pdf-regex"
    assert len(txns) == len(make_samples.PDF_LINES)
    assert txns[0].date == "2026-08-03" and txns[0].amount == 211.56   # card sign fixed later by apply_sign
    txns, flipped = importers.apply_sign(txns, "auto")
    assert flipped and txns[0].amount == -211.56


@pytest.mark.parametrize("raw,key", [
    ("SQ *BLUE BOTTLE COFFEE", "BLUE BOTTLE COFFEE"),
    ("WHOLEFDS MKT #10234 BOSTON MA", "WHOLEFDS MKT BOSTON"),
    ("SHELL OIL 57442 NEWTON MA", "SHELL OIL NEWTON"),
])
def test_merchant_key(raw, key):
    assert importers.merchant_key(raw) == key


@pytest.mark.parametrize("raw,val", [("(12.50)", -12.5), ("1,204.11", 1204.11), ("$-5.00", -5.0),
                                      ("45.00 CR", 45.0), ("45.00-", -45.0), ("abc", None)])
def test_parse_amount(raw, val):
    assert importers.parse_amount(raw) == val
