"""Observed Simon stage annotations must agree with the fixed design."""
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend as backend, run_method


INPUTS = {"outcome": "y", "null_response_rate": .2, "target_response_rate": .4,
          "alpha": .05, "power": .8, "design_criterion": "optimal"}


def test_simon_stage_and_subgroup_are_consumed_without_changing_design(backend, tmp_path):
    result, probe = run_method(backend, tmp_path, "trial_simon_two_stage", '''
dataset <- data.frame(y=rep(c(0,1),5), stage=rep(1,10), subgroup=rep(c("A","B"),5))
''', {**INPUTS, "stage": "stage", "subgroup": "subgroup"},
        'list(n1=n1,n=n,alpha=actual_alpha,power=actual_power,notes=notes)')
    assert probe["n1"] == 13 and probe["n"] == 43
    assert probe["alpha"] <= .05 and probe["power"] >= .8
    assert "第一阶段 10/13" in probe["notes"]
    assert "第一阶段尚未达到计划人数" in probe["notes"]
    assert "不改变总体 Simon 设计或停止规则" in probe["notes"]
    assert "响应率=0.0000" in probe["notes"] and "响应率=1.0000" in probe["notes"]
    assert any("亚组仅作观察响应描述" in Path(item.path).read_text() for item in result.texts)


@pytest.mark.parametrize("data_code,inputs,message", [
    ('dataset<-data.frame(y=rep(0,10), stage=c(rep(1,9),2))', {"stage":"stage"}, "实际阶段人数"),
    ('dataset<-data.frame(y=rep(0,10), stage=rep(3,10))', {"stage":"stage"}, "实际阶段须完整编码"),
    ('dataset<-data.frame(y=rep(0,14), stage=c(rep(1,13),2), enrollment=c(14,2:13,1))', {"stage":"stage","enrollment_order":"enrollment"}, "入组顺序与实际阶段"),
    ('dataset<-data.frame(y=rep(0,10), subgroup=c(rep("A",9),NA))', {"subgroup":"subgroup"}, "亚组标签须完整"),
])
def test_simon_rejects_inconsistent_observed_annotations(backend, tmp_path, data_code, inputs, message):
    _, probe = run_method(backend, tmp_path, "trial_simon_two_stage", data_code,
                          {**INPUTS, **inputs}, 'list(error=res)', expect_success=False)
    assert message in probe["error"]
