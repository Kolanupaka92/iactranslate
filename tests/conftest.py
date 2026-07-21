import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))


_FIXTURE_FILES = (
    "rvtools_sample.xlsx", "vmware_sample.csv", "hyperv_sample.csv",
    "cmdb_sample.csv", "cloud_sample.csv",
)


def _ensure_fixtures() -> None:
    if not all((FIXTURES / f).exists() for f in _FIXTURE_FILES):
        import make_fixtures

        make_fixtures.main()


_ensure_fixtures()


@pytest.fixture
def rvtools_path() -> str:
    return str(FIXTURES / "rvtools_sample.xlsx")


@pytest.fixture
def vmware_csv_path() -> str:
    return str(FIXTURES / "vmware_sample.csv")


@pytest.fixture
def hyperv_path() -> str:
    return str(FIXTURES / "hyperv_sample.csv")


@pytest.fixture
def cmdb_path() -> str:
    return str(FIXTURES / "cmdb_sample.csv")


@pytest.fixture
def cloud_path() -> str:
    return str(FIXTURES / "cloud_sample.csv")
