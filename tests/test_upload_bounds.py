"""A hostile inventory must be cheap to reject.

The cap on workloads used to fire after parse and normalisation, which meant
the whole file was turned into Pydantic objects before anyone counted them. A
24 MB CSV of 547,000 unique rows — well under the upload limit — peaked at
1,327 MB before rejection, on a 1,024 MB instance. Registration is open, so
that was one request from anyone to OOM-kill the API.

These tests hold the property rather than the outcome: rejection must happen
at the read, with memory bounded by the cap and not by the file.
"""
import resource
import sys
import time

import pytest

from iactranslate.config import MAX_VMS
from iactranslate.pipeline import run_pipeline
from iactranslate.sources.base import InventoryTooLarge


def _rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 ** 2 if sys.platform == "darwin" else 1024)


@pytest.fixture(scope="module")
def oversized_csv(tmp_path_factory):
    """MAX_VMS + 5,000 unique rows — over the cap, far under the upload limit."""
    path = tmp_path_factory.mktemp("dos") / "big.csv"
    with open(path, "w") as f:
        f.write("VM,CPUs,Memory,OS according to the configuration file\n")
        for i in range(MAX_VMS + 5_000):
            f.write(f"prod-web-{i:07d},2,4096,Ubuntu Linux (64-bit)\n")
    return str(path)


def test_an_oversized_inventory_is_rejected_at_the_read(oversized_csv, tmp_path):
    with pytest.raises(InventoryTooLarge) as e:
        run_pipeline(input_path=oversized_csv, project_name="dos", out_dir=str(tmp_path), target="aws")
    assert f"{MAX_VMS:,}" in str(e.value)


def test_rejection_does_not_scale_with_the_file(oversized_csv, tmp_path):
    """The whole point. Time and memory must be bounded by the cap, not by
    how much the attacker uploaded. Thresholds are loose on purpose — they
    catch the regression (16 s / 1.3 GB), not CI noise."""
    before = _rss_mb()
    t0 = time.time()
    with pytest.raises(InventoryTooLarge):
        run_pipeline(input_path=oversized_csv, project_name="dos", out_dir=str(tmp_path), target="aws")
    elapsed = time.time() - t0
    grew = _rss_mb() - before
    assert elapsed < 3.0, f"took {elapsed:.1f}s to reject — the cap is firing too late"
    assert grew < 200, f"RSS grew {grew:.0f} MB rejecting a capped file — the file is being loaded"


def test_it_is_a_value_error_so_the_api_returns_400():
    """The API maps ValueError to 400. A new exception class that escaped that
    would turn a rejected upload back into a 500."""
    assert issubclass(InventoryTooLarge, ValueError)


def test_a_legitimate_inventory_at_the_cap_still_parses(tmp_path):
    """Exactly MAX_VMS rows is allowed; the reader asks for one more only to
    detect overflow."""
    path = tmp_path / "at-cap.csv"
    with open(path, "w") as f:
        f.write("VM,CPUs,Memory,OS according to the configuration file\n")
        for i in range(MAX_VMS):
            f.write(f"vm-{i:06d},1,1024,Ubuntu Linux (64-bit)\n")
    from iactranslate.sources import parse
    assert len(parse(str(path))) == MAX_VMS


def test_the_fixtures_are_untouched():
    """Bounding the read must not change what a normal file yields."""
    from iactranslate.sources import parse
    assert len(parse("tests/fixtures/rvtools_sample.xlsx")) == 7
