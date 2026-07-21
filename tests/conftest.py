import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))


def _ensure_fixtures() -> None:
    xlsx = FIXTURES / "rvtools_sample.xlsx"
    csv = FIXTURES / "vmware_sample.csv"
    if not xlsx.exists() or not csv.exists():
        import make_fixtures

        make_fixtures.main()


_ensure_fixtures()


@pytest.fixture
def rvtools_path() -> str:
    return str(FIXTURES / "rvtools_sample.xlsx")


@pytest.fixture
def vmware_csv_path() -> str:
    return str(FIXTURES / "vmware_sample.csv")
