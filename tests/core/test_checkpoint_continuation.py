import json
from pathlib import Path

from agent.analysis.models import AnalysisPlan
from agent.tools.research import RToolsMixin
from r_runtime.protocol import RRunResult


_GENERATION_A = "a" * 32
_GENERATION_B = "b" * 32


class _Dataset:
    def __init__(self, dataset_id: str = "dataset-a") -> None:
        self.dataset_id = dataset_id
        self.session_id = "session-1"
        self.name = "clinical.csv"
        self.format = "csv"
        self.stored_path = "/tmp/clinical.csv"
        self.profile = {}
        self.assets = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "session_id": self.session_id,
            "name": self.name,
            "format": self.format,
        }


def _plan(plan_id: str, dataset_id: str, generation: str) -> AnalysisPlan:
    return AnalysisPlan(
        plan_id=plan_id,
        source="conversation",
        dataset_id=dataset_id,
        intent="continue analysis",
        category="statistics",
        method_family="descriptive",
        scenario="long workflow",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(),
        resource_class="standard",
        workspace_generation=generation,
    )


class _CheckpointAgent(RToolsMixin):
    def __init__(self) -> None:
        self._session_id = "session-1"
        self._research_workspace_generation = _GENERATION_A
        self._conversation_job_id = "conversation-1"
        self._analysis_plan = _plan("plan-a", "dataset-a", _GENERATION_A)
        self._workflow_run_spec = None
        self.dataset = _Dataset()
        self.requests = []

    def _resolve_research_dataset(self, dataset_id: str = ""):
        return self.dataset

    def _run_r_request(self, request):
        self.requests.append(request)
        return RRunResult(ok=True, stdout="ok")


def test_same_frozen_plan_reuses_one_uncreated_internal_checkpoint_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    agent = _CheckpointAgent()

    first_text = agent._tool_run_r_code(code="summary(dataset)")
    second_text = agent._tool_run_r_code(code="summary(dataset)")

    first, second = agent.requests
    checkpoint_dir = Path(first.checkpoint_dir)
    expected_parent = (
        tmp_path / "outputs" / "research" / "session-1" / "generations" / _GENERATION_A / "checkpoints"
    )
    assert first.checkpoint_dir == second.checkpoint_dir
    assert checkpoint_dir.parent == expected_parent
    assert checkpoint_dir.is_absolute()
    assert not checkpoint_dir.exists()
    assert not expected_parent.exists()
    assert first.artifacts_dir != second.artifacts_dir
    assert "checkpoint_dir" not in first.to_dict()
    assert str(checkpoint_dir) not in first_text
    assert str(checkpoint_dir) not in second_text


def test_new_plan_dataset_or_generation_gets_an_isolated_checkpoint_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    agent = _CheckpointAgent()

    agent._tool_run_r_code(code="summary(dataset)")
    agent._analysis_plan = _plan("../../plan-b", "dataset-a", _GENERATION_A)
    agent._tool_run_r_code(code="summary(dataset)")
    agent.dataset = _Dataset("dataset-b")
    agent._analysis_plan = _plan("plan-a", "dataset-b", _GENERATION_A)
    agent._tool_run_r_code(code="summary(dataset)")
    agent._research_workspace_generation = _GENERATION_B
    agent._analysis_plan = _plan("plan-a", "dataset-b", _GENERATION_B)
    agent._tool_run_r_code(code="summary(dataset)")

    checkpoint_dirs = [Path(request.checkpoint_dir) for request in agent.requests]
    trusted_root = (tmp_path / "outputs" / "research").resolve()
    assert len(set(checkpoint_dirs)) == 4
    assert all(path.is_absolute() for path in checkpoint_dirs)
    assert all(path.resolve().is_relative_to(trusted_root) for path in checkpoint_dirs)
    assert all(".." not in path.parts for path in checkpoint_dirs)
    assert checkpoint_dirs[0].parent.parent.name == _GENERATION_A
    assert checkpoint_dirs[-1].parent.parent.name == _GENERATION_B


def test_missing_or_mismatched_frozen_identity_disables_checkpoint_reuse(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    agent = _CheckpointAgent()

    agent._analysis_plan = None
    no_plan_text = agent._tool_run_r_code(code="summary(dataset)")
    agent._analysis_plan = _plan("plan-a", "dataset-a", "")
    agent._research_workspace_generation = ""
    no_generation_text = agent._tool_run_r_code(code="summary(dataset)")
    agent._analysis_plan = _plan("plan-a", "dataset-a", _GENERATION_A)
    agent._research_workspace_generation = _GENERATION_B
    mismatched_generation_text = agent._tool_run_r_code(code="summary(dataset)")
    agent._analysis_plan = _plan("plan-a", "another-dataset", _GENERATION_B)
    mismatched_dataset_text = agent._tool_run_r_code(code="summary(dataset)")

    assert [request.checkpoint_dir for request in agent.requests] == ["", "", "", ""]
    for text in (
        no_plan_text,
        no_generation_text,
        mismatched_generation_text,
        mismatched_dataset_text,
    ):
        payload = json.loads(text)
        assert "checkpoint" not in json.dumps(payload).lower()
