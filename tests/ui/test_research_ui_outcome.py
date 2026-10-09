"""User-visible completion semantics, exercised with the real workbench JS."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


WORKBENCH = Path(__file__).resolve().parents[2] / "static/js/research_workbench.js"


def _run_javascript(checks):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the workbench interaction check")
    setup = """
const fs = require("fs"), vm = require("vm"), assert = require("assert");
process.env.NODE_ENV = "test";
global.localStorage = {getItem: () => null, setItem: () => {}};
global.sessionStorage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
const banner = {hidden: true};
global.document = {
  addEventListener: () => {}, querySelector: () => null, querySelectorAll: () => [],
  getElementById: id => id === "rsw-results-ready" ? banner : null,
};
global.window = global;
global.HTMLElement = class HTMLElement {};
global.Element = class Element {};
global.__RSW_TEST_HARNESS__ = {};
"""
    script = setup + f"\nvm.runInThisContext(fs.readFileSync({json.dumps(str(WORKBENCH))}, 'utf8'));\n"
    script += "const hooks = global.__RSW_TEST_HARNESS__;\n" + checks
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_artifact_banner_requires_actual_artifacts_and_resets_after_failure():
    _run_javascript("""
hooks.state.activeView = "assistant";
hooks.state.analysisResult = {payload: {result: {ok: false, error: "R failed", tables: []}}};
hooks.notifyResultsReady();
assert.strictEqual(banner.hidden, true, "A failed R step with no files must not announce ready artifacts");
hooks.state.analysisResult = {payload: {result: {ok: true, tables: [{name: "partial"}]}}};
hooks.notifyResultsReady();
assert.strictEqual(banner.hidden, false, "Existing partial output must remain discoverable");
hooks.state.analysisResult = {payload: {result: {ok: false, tables: []}}};
hooks.notifyResultsReady();
assert.strictEqual(banner.hidden, true, "A later failed run must clear the previous ready banner");
""")


def test_outcome_keeps_conversation_artifacts_and_diagnostics_distinct():
    _run_javascript("""
hooks.showAnalysisOutcome({completed: false, analysis_status: "failed"});
assert.strictEqual(hooks.state.status.title, "分析暂未完成");
assert.strictEqual(hooks.state.status.kind, "warning");
hooks.showAnalysisOutcome({completed: true, analysis_status: "conversation_only"});
assert.strictEqual(hooks.state.status.title, "对话完成");
hooks.showAnalysisOutcome({completed: true, analysis_status: "complete", diagnostic_quality: "not_assessed"});
assert.strictEqual(hooks.state.status.title, "分析产物已齐全");
assert.strictEqual(hooks.state.status.kind, "warning");
assert.match(hooks.state.status.detail, /方法诊断仍需确认/);
hooks.showAnalysisOutcome({completed: true, analysis_status: "complete", diagnostic_quality: "failed"});
assert.match(hooks.state.status.detail, /方法诊断未通过/);
hooks.showAnalysisOutcome({completed: true, analysis_status: "complete", diagnostic_quality: "passed"});
assert.strictEqual(hooks.state.status.kind, "ok");
assert.match(hooks.state.status.detail, /科研结论需研究者复核/);
""")


def test_explanation_only_does_not_offer_to_complete_nonexistent_analysis_outputs():
    _run_javascript("""
document.createElement = () => ({});
let cards = 0;
const target = {appendChild: () => {cards += 1;}};
hooks.appendResultCompletionCard(target, "", {analysis_status: "conversation_only", completed: true});
assert.strictEqual(cards, 0, "An explanation-only conversation must not receive a missing-scientific-output card");
""")
