import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.argv.append("--demo")
from finance.desktop import main  # noqa: E402

main()
