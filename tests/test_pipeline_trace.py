"""Pipeline runs as named, timed stages — the observability trace."""
import json

from iactranslate.pipeline import run_pipeline


def test_trace_records_every_stage(rvtools_path, tmp_path):
    r = run_pipeline(input_path=rvtools_path, project_name="t",
                     out_dir=str(tmp_path / "t"), target="aws")
    assert r.trace is not None
    names = [s.stage for s in r.trace.stages]
    # Ordered, named stages the docs also use.
    assert names == ["parse", "normalize", "plan", "validate", "policy", "package"]
    assert all(s.duration_ms >= 0 for s in r.trace.stages)


def test_total_is_sum_of_stages(rvtools_path, tmp_path):
    r = run_pipeline(input_path=rvtools_path, project_name="t",
                     out_dir=str(tmp_path / "t"), target="aws")
    assert abs(r.trace.total_ms - sum(s.duration_ms for s in r.trace.stages)) < 1e-6


def test_zip_stage_present_when_zipping(rvtools_path, tmp_path):
    r = run_pipeline(input_path=rvtools_path, project_name="t",
                     out_dir=str(tmp_path / "t"), target="aws", make_zip=True)
    assert "zip" in [s.stage for s in r.trace.stages]


def test_trace_artifact_written(rvtools_path, tmp_path):
    r = run_pipeline(input_path=rvtools_path, project_name="t",
                     out_dir=str(tmp_path / "t"), target="aws")
    path = r.project_dir / "pipeline-trace.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["total_ms"] == r.trace.total_ms
    assert len(data["stages"]) == len(r.trace.stages)
