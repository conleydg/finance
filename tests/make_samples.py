"""Synthetic statements in the shapes real banks export. Fake data only."""
from pathlib import Path

SAMPLES = Path(__file__).parent / "samples"

CHASE_CHECKING = """Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
CREDIT,08/01/2026,"ACME CORP PAYROLL PPD ID: 123456",4210.55,ACH_CREDIT,6210.55,,
DEBIT,08/01/2026,"GREENWOOD APTS RENT WEB PMT",-2150.00,ACH_DEBIT,4060.55,,
DEBIT,08/03/2026,"WHOLEFDS MKT #10234 BOSTON MA",-142.37,DEBIT_CARD,3918.18,,
DEBIT,08/05/2026,"SHELL OIL 57442 NEWTON MA",-48.20,DEBIT_CARD,3869.98,,
DEBIT,08/07/2026,"EVERSOURCE ENERGY BILL PAY",-131.09,ACH_DEBIT,3738.89,,
DEBIT,08/09/2026,"SQ *BLUE BOTTLE COFFEE",-6.75,DEBIT_CARD,3732.14,,
DEBIT,08/09/2026,"SQ *BLUE BOTTLE COFFEE",-6.75,DEBIT_CARD,3725.39,,
DEBIT,08/12/2026,"AMEX EPAYMENT ACH PMT",-812.44,ACH_DEBIT,2912.95,,
DEBIT,08/14/2026,"TRADER JOE'S #512",-88.19,DEBIT_CARD,2824.76,,
CREDIT,08/15/2026,"ACME CORP PAYROLL PPD ID: 123456",4210.55,ACH_CREDIT,7035.31,,
DEBIT,08/16/2026,"ONLINE TRANSFER TO SAV XXXX1234",-500.00,ACCT_XFER,6535.31,,
DEBIT,08/20/2026,"CVS/PHARMACY #0123",-23.41,DEBIT_CARD,6511.90,,
DEBIT,08/22/2026,"KUMON NORTH CENTER TUITION",-190.00,DEBIT_CARD,6321.90,,
DEBIT,08/28/2026,"GEICO AUTO INS",-156.30,ACH_DEBIT,6165.60,,
CREDIT,09/01/2026,"ACME CORP PAYROLL PPD ID: 123456",4210.55,ACH_CREDIT,10376.15,,
DEBIT,09/01/2026,"GREENWOOD APTS RENT WEB PMT",-2150.00,ACH_DEBIT,8226.15,,
DEBIT,09/04/2026,"WHOLEFDS MKT #10234 BOSTON MA",-167.02,DEBIT_CARD,8059.13,,
DEBIT,09/06/2026,"SHELL OIL 57442 NEWTON MA",-51.90,DEBIT_CARD,8007.23,,
"""

# Amex style: purchases positive, payments negative, no header preamble.
AMEX = """Date,Description,Amount
08/02/2026,NETFLIX.COM LOS GATOS CA,15.49
08/04/2026,AMAZON MKTPLACE PMTS AMZN.COM/BILL WA,63.18
08/06/2026,UBER *TRIP HELP.UBER.COM,24.10
08/10/2026,THE CHEESECAKE FACTORY #0112,96.44
08/12/2026,ONLINE PAYMENT - THANK YOU,-812.44
08/15/2026,DELTA AIR LINES ATLANTA,412.60
08/18/2026,TARGET 00012345 NEWTON MA,54.77
08/19/2026,AMAZON MKTPLACE PMTS AMZN.COM/BILL WA,-21.99
08/25/2026,SPOTIFY USA,11.99
"""

# Bank of America style: summary block above the real header, separate debit/credit columns.
BOFA_SAVINGS = """Description,,Summary Amt.
Beginning balance as of 08/01/2026,,"10,000.00"
Total credits,,"512.40"
Ending balance as of 08/31/2026,,"10,512.40"

Date,Description,Withdrawals,Deposits,Running Bal.
08/16/2026,Online Banking transfer from CHK 5678,,"500.00","10,500.00"
08/31/2026,Interest Earned,,"12.40","10,512.40"
"""

PDF_LINES = [
    ("08/03", "COSTCO WHSE #0345 WALTHAM MA", "211.56"),
    ("08/07", "VERIZON WIRELESS PAYMENTS", "95.00"),
    ("08/11", "PANERA BREAD #601234", "18.42"),
    ("08/13", "PAYMENT THANK YOU", "-1,204.11"),
    ("08/19", "HOME DEPOT 2611", "134.09"),
    ("08/23", "REGAL CINEMAS FENWAY", "42.00"),
    ("08/27", "CHEWY.COM", "61.35"),
]


def make_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for line in ["Example Card Services", "Account ending 4321",
                 "Opening/Closing Date 07/29/26 - 08/28/26", "Payment Due Date 09/22/26",
                 "New Balance $1,204.11   Minimum Payment Due $35.00", "", "ACCOUNT ACTIVITY",
                 "Date of Transaction   Merchant Name or Transaction Description   $ Amount"]:
        c.drawString(50, y, line)
        y -= 18
    for d, desc, amt in PDF_LINES:
        c.drawString(50, y, d)
        c.drawString(130, y, desc)
        c.drawRightString(540, y, amt)
        y -= 18
    y -= 10
    c.drawString(50, y, "Total fees charged in 2026 $0.00")
    c.save()


# Two overlapping 401(k) statements: Q2, and June to August. June is in both, worded differently.
K401_A = ("401k_2026Q2.pdf", "Statement Period: 04/01/2026 - 06/30/2026", "41,210.55", "45,902.10", [
    ("04/15/2026", "Employee Contribution Pre-Tax", "480.00"), ("04/15/2026", "Employer Match", "240.00"),
    ("04/30/2026", "Employee Contribution Pre-Tax", "480.00"), ("04/30/2026", "Employer Match", "240.00"),
    ("05/15/2026", "Employee Contribution Pre-Tax", "480.00"), ("05/15/2026", "Employer Match", "240.00"),
    ("05/29/2026", "Employee Contribution Pre-Tax", "480.00"), ("05/29/2026", "Employer Match", "240.00"),
    ("06/15/2026", "Employee Contribution Pre-Tax", "480.00"), ("06/15/2026", "Employer Match", "240.00"),
    ("06/30/2026", "Dividends Reinvested", "112.37"), ("06/30/2026", "Recordkeeping Fee", "-12.50"),
])
K401_B = ("401k_2026JunAug.pdf", "Statement Period: 06/01/2026 - 08/31/2026", "44,610.20", "49,120.33", [
    ("06/15/2026", "EE Contribution (Pre-Tax)", "480.00"), ("06/15/2026", "ER Match", "240.00"),
    ("06/30/2026", "Dividend Reinvestment", "112.37"), ("06/30/2026", "Plan Admin Fee", "-12.50"),
    ("07/15/2026", "EE Contribution (Pre-Tax)", "480.00"), ("07/15/2026", "ER Match", "240.00"),
    ("07/31/2026", "EE Contribution (Pre-Tax)", "480.00"), ("07/31/2026", "ER Match", "240.00"),
    ("08/14/2026", "EE Contribution (Pre-Tax)", "480.00"), ("08/14/2026", "ER Match", "240.00"),
    ("08/28/2026", "EE Contribution (Pre-Tax)", "480.00"), ("08/28/2026", "ER Match", "240.00"),
])


def make_401k_pdf(path: Path, period: str, begin: str, end: str, rows) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for line in ["Example Retirement Services", "Example Corp 401(k) Plan", "Account ending in 4821", period,
                 f"Beginning Balance ${begin}", f"Ending Balance ${end}", "", "ACCOUNT ACTIVITY"]:
        c.drawString(50, y, line)
        y -= 18
    for d, desc, amt in rows:
        c.drawString(50, y, d)
        c.drawString(140, y, desc)
        c.drawRightString(540, y, amt)
        y -= 18
    c.save()


# Balance-only statements: two accounts that would both be called "IRA", told apart by number.
BALANCE_ONLY = [
    ("ira_a_2026-06.pdf", ["Example Brokerage", "Traditional IRA", "Account number: XXXX-3307",
                           "Total Account Value as of 06/30/2026: $52,310.44"]),
    ("ira_b_2026-06.pdf", ["Example Brokerage", "Traditional IRA", "Account number: XXXX-9150",
                           "Total Account Value as of 06/30/2026: $8,120.00"]),
    ("ira_a_2026-09.pdf", ["Example Brokerage", "Traditional IRA", "Account number: XXXX-3307",
                           "Total Account Value as of 09/30/2026: $53,904.12"]),
]


def make_text_pdf(path: Path, lines: list[str]) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for line in lines:
        c.drawString(50, y, line)
        y -= 18
    c.save()


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / "chase_checking.csv").write_text(CHASE_CHECKING)
    (SAMPLES / "amex.csv").write_text(AMEX)
    (SAMPLES / "bofa_savings.csv").write_text(BOFA_SAVINGS)
    make_pdf(SAMPLES / "card_statement.pdf")
    for name, period, begin, end, rows in (K401_A, K401_B):
        make_401k_pdf(SAMPLES / name, period, begin, end, rows)
    for name, lines in BALANCE_ONLY:
        make_text_pdf(SAMPLES / name, lines)


if __name__ == "__main__":
    main()
