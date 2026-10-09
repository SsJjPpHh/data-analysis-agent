"""Exercise fixed preparation outputs with real R and explicit user rules."""

import json

import pytest

from tests.science.test_method_numerics import backend, run_method  # noqa: F401


DATA = '''dataset <- data.frame(id=c("s1","s1","s3","s4"),
 age=c(20,NA,40,NA), sex=c(" M ","F","M",NA), other=c(NA,NA,1,2))'''


def test_profile_preserves_numeric_types_and_counts_missing_ids(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "data_quality", DATA, {"sample_id": "id"},
                      "list(profile=.profile,duplicates=.id_duplicates,patterns=.patterns)")
    age = next(x for x in p["profile"] if x["变量"] == "age")
    assert age["类型"] == "numeric"
    assert age["缺失数"] == 2
    assert age["缺失比例"] == .5
    assert p["duplicates"] == 1
    assert sum(x["行数"] for x in p["patterns"]) == 4


def test_import_field_map_contains_actual_selected_names(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "data_import_workflow", DATA,
                      {"candidate_variables": ["age", "sex"]}, "list(mapping=.mapping)")
    assert {x["字段"] for x in p["mapping"]} == {"age", "sex"}


def test_explicit_cleaning_rules_keep_source_and_row_count(backend, tmp_path):
    rules = {"sex": {"trim": True, "recode": {"M": "男", "F": "女"}}}
    _, p = run_method(backend, tmp_path, "data_cleaning_recoding", DATA,
                      {"candidate_variables": ["age", "sex"], "cleaning_rules": json.dumps(rules)},
                      "list(original=dataset$sex,cleaned=.cleaned$sex,n=nrow(.cleaned),audit=.audit)")
    assert p["original"][:3] == [" M ", "F", "M"]
    assert p["cleaned"][:3] == ["男", "女", "男"]
    assert p["n"] == 4
    audit = p["audit"][0] if isinstance(p["audit"], list) else p["audit"]
    assert audit["修改行数"] == 3


def test_bad_numeric_conversion_is_rejected(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "data_cleaning_recoding", DATA,
                      {"cleaning_rules": '{"sex":{"target_type":"numeric"}}'},
                      "list(message=res)", expect_success=False)
    assert "丢失" in p["message"]


def test_eda_index_refers_to_an_actual_plot(backend, tmp_path):
    result, _ = run_method(backend, tmp_path, "automated_eda_report", DATA, {}, "list(n=nrow(.profile))")
    assert "eda_missing_plot" in {x.name for x in result.figures}


def test_large_data_summary_uses_selected_group_and_measure(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "large_data_engine",
                      'dataset <- data.frame(g=c("A","A","B","B"),x=c(1,3,5,7),z=c(9,8,7,6))',
                      {"group": "g", "measure": "x"}, "list(summary=as.data.frame(.summary),size=.size)")
    assert [(x["g"], x["x"], x["行数"]) for x in p["summary"]] == [("A", 2, 2), ("B", 6, 2)]
    assert all("z" not in x for x in p["summary"])


def test_co_missing_matrix_only_uses_selected_variables(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "missing_visual_diagnostics", DATA,
                      {"variables_with_missingness": ["age", "sex"]},
                      "list(matrix=.co_missing,vars=colnames(.co_missing),patterns=.patterns)")
    assert p["vars"] == ["age", "sex"]
    assert p["matrix"] == [[2, 1], [1, 1]]


@pytest.mark.parametrize("tid", ["data_quality", "data_import_workflow", "data_cleaning_recoding",
                                 "automated_eda_report", "large_data_engine", "missing_visual_diagnostics"])
def test_preparation_artifacts_match_declared_kinds(backend, tmp_path, tid):
    from agent.analysis.catalog import AnalysisCatalog

    result, _ = run_method(backend, tmp_path, tid, DATA, {}, "list(ok=TRUE)")
    artifacts = {a.name: a for c in (result.tables, result.figures, result.texts, result.models) for a in c}
    for expected in AnalysisCatalog().get(tid).required_outputs:
        if expected.conditions:
            continue
        assert expected.role in artifacts
        assert artifacts[expected.role].kind == expected.kind
