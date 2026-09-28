"""py2app config for the Finance Mac apps; run via build_apps.sh, not directly."""
import os
import sys
from pathlib import Path

from setuptools import setup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DEMO = os.environ.get("FINANCE_APP_DEMO") == "1"
NAME = "Finance Demo" if DEMO else "Finance"

setup(
    name=NAME,
    app=["entry_demo.py" if DEMO else "entry_finance.py"],
    options={"py2app": {
        "iconfile": "FinanceDemo.icns" if DEMO else "Finance.icns",
        "plist": {
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleIdentifier": "local.finance.demo" if DEMO else "local.finance.app",
            "NSHighResolutionCapable": True,
        },
    }},
    setup_requires=["py2app"],
)
