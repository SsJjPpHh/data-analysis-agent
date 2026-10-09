import pytest
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.protocol import DatasetRef, RRunRequest
from tests.runtime.test_baa_data_plot_hardening import _local_runtime


def test_progress_callback_receives_output_and_heartbeat(tmp_path):
    rscript_bin, runtime_env = _local_runtime()
    if rscript_bin is None:
        pytest.skip("Local R is unavailable")
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("x\n1\n2\n", encoding="utf-8")
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env)
    request = RRunRequest(
        job_id="progress-test",
        dataset=DatasetRef(dataset_id="d1", path=str(csv_path), format="csv", name="d.csv"),
        code="cat('STEP1 done\\n'); Sys.sleep(0.2); cat('STEP2 done\\n'); invisible(NULL)",
        artifacts_dir=str(tmp_path / "artifacts"),
        timeout_seconds=60,
    )
    seen = []
    result = backend.run_code(request, progress_callback=seen.append)
    assert result.ok is True
    joined = "\n".join(seen)
    assert "STEP1" in joined or "STEP2" in joined
    assert all(len(line) <= 200 for line in seen)
