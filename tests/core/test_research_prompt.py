import agent.prompts as prompts
from agent.r_package_inventory import load_r_package_inventory
from r_runtime.inspection_template import DATASET_PROFILE_R_CODE
from agent.compaction import _COMPACT_PROMPT_TEMPLATE


def test_research_prompt_mentions_r_tools(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    assert "R" in prompt
    assert "inspect_dataset" in prompt
    assert "set_analysis_plan" in prompt
    assert "run_r_code" in prompt
    assert "get_r_package_inventory" in prompt
    assert "get_r_package_help" in prompt
    assert "dataset" in prompt
    assert "AnalysisPlan.output_portfolio" in prompt
    assert "Scientific Output Portfolio" in prompt
    assert "baa_workers" in prompt
    assert "baa_parallel_lapply" in prompt
    assert 'resource_class="extended"' in prompt


def test_analysis_plan_prompt_exposes_required_output_state():
    from agent.agent import _analysis_plan_system_section
    from agent.analysis.models import AnalysisPlan, RequiredOutput

    plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="dataset-1",
        intent="分析",
        category="基础统计",
        method_family="classical_statistics",
        scenario="比较",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(
            RequiredOutput("primary", "table", required=True, tier="core"),
            RequiredOutput("optional_plot", "figure", required=False, tier="core"),
        ),
        resource_class="standard",
    )

    section = _analysis_plan_system_section(plan)

    assert "primary [table; tier=core; required=true" in section
    assert "optional_plot [figure; tier=core; required=false" in section
    assert "resource_class=standard" in section


def test_default_dataset_inspection_does_not_extract_raw_sample_values():
    assert "head(na.omit" not in DATASET_PROFILE_R_CODE
    assert 'example = ""' in DATASET_PROFILE_R_CODE


def test_conversation_compaction_preserves_research_statistics_context():
    assert "research-statistics conversation" in _COMPACT_PROMPT_TEMPLATE
    assert "statistical methods" in _COMPACT_PROMPT_TEMPLATE
    assert "business-analytics conversation" not in _COMPACT_PROMPT_TEMPLATE
    assert "The SQL or tool used" not in _COMPACT_PROMPT_TEMPLATE
    assert "PPT" not in _COMPACT_PROMPT_TEMPLATE


def test_research_prompt_requires_result_artifacts(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    assert "Do not leave primary results only in the chat answer" in prompt
    assert "right-side result panel" in prompt
    assert "baa_table()" in prompt
    assert "baa_table_one()" in prompt
    assert "baa_text()" in prompt
    assert "artifact_previews" in prompt
    assert "Never put placeholder cells" in prompt
    assert "Do not tell the user to copy primary tables or figures from chat" in prompt
    assert "User-attached images and R-generated figures are vision inputs" in prompt
    assert "Exact numbers come from artifact_previews" in prompt
    assert "Do not hand-build Table One" in prompt
    assert "left_join/full_join" in prompt
    assert "categorical level" in prompt
    assert "own row" in prompt


def test_research_prompt_avoids_common_recoverable_r_mistakes(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    assert "different-length filtered vectors" in prompt
    assert "na.print" in prompt
    assert "must be a character string" in prompt
    assert "Do not use console printing as the primary result channel" in prompt


def test_research_prompt_forbids_sql_tools(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    assert "Do not use SQL tools" in prompt


def test_default_prompt_is_research_prompt(monkeypatch):
    monkeypatch.delenv("BAA_PRODUCT_MODE", raising=False)
    prompt = prompts.get_system_prompt()
    assert "research statistics assistant" in prompt
    assert "professional business analyst assistant" not in prompt


def test_research_prompt_includes_capability_routing(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    assert "R package capability routing" in prompt
    assert "Do not wait for" in prompt
    assert "user to name a package" in prompt
    assert "requireNamespace" in prompt
    assert "fallback" in prompt
    assert "live package inventory" in prompt
    assert "Do not invent package APIs" in prompt
    assert "repair_diagnostics" in prompt
    assert "help_documents" in prompt
    assert "get_r_package_help(query=...)" in prompt
    assert "cross_package_search" in prompt


def test_research_prompt_routes_major_package_families(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    for expected in (
        "gtsummary",
        "MatchIt",
        "cobalt",
        "survey",
        "DESeq2",
        "ggplot2/ggrepel",
        "clusterProfiler",
        "survival/prognosis",
        "causal inference",
        "single-cell",
        "microbiome",
        "machine learning",
    ):
        assert expected in prompt
    assert "call get_r_package_inventory with" in prompt


def test_research_prompt_allows_large_compute_stacks(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    assert "does not disable long-running" in prompt
    assert "Java, CUDA, CmdStan" in prompt
    assert "Deferred/default-disabled compute stacks" not in prompt


def test_research_prompt_exposes_full_r_package_inventory(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    inventory = load_r_package_inventory()
    assert len(inventory.installable) >= 250
    assert "R package inventory access" in prompt
    assert "active template and loader catalog" in prompt
    assert "user's R installation" in prompt
    assert "current truth" in prompt
    assert "Installable package candidates" not in prompt


def test_research_package_inventory_exposes_known_github_fallbacks():
    inventory = load_r_package_inventory()
    assert inventory.github_fallbacks["ArchR"] == "GreenleafLab/ArchR"
    assert inventory.github_fallbacks["CellChat"] == "jinworks/CellChat"


def test_research_prompt_has_no_package_family_denylist(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    prompt = prompts.get_system_prompt()
    assert "No package family is disabled by application policy" in prompt
    assert "Heavy packages not installed automatically" not in prompt
