"""Restore actual schema-2 store responses through the production workbench JS."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from data.research_results import ResearchResultStore, empty_state


WORKBENCH = Path(__file__).resolve().parents[2] / "static/js/research_workbench.js"


def _saved_state(tmp_path, monkeypatch, *, complete=True, quality="passed", failed=False, artifacts=True):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    sid, plan_id, turn_id = "restore-session", "frozen-plan", "restored-turn"
    value = empty_state(sid)
    value["dataset"] = {
        "dataset_id": "data-1", "name": "basic_regression.csv",
        "summary": {"rows": 120, "columns": 4, "variables": [{"name": "y"}, {"name": "x"}, {"name": "z"}, {"name": "arm"}]},
    }
    generated = []
    if artifacts:
        for kind, count in [("table", 7), ("figure", 1), ("text", 3)]:
            for index in range(count):
                generated.append({
                    "artifact_id": f"{kind}-{index}", "kind": kind,
                    "name": f"{kind}_{index}", "role": "paper_text" if kind == "text" else f"primary_{kind}",
                    "path": f"artifacts/{kind}_{index}.{'png' if kind == 'figure' else 'csv' if kind == 'table' else 'txt'}",
                    "metadata": {"dataset_id": "data-1", "plan_id": plan_id},
                    "source": "r", "included_in_paper": True,
                })
    assessment = {
        "complete": complete, "r_executed": not failed,
        "diagnostic_quality": quality, "outstanding_roles": [] if complete else ["robustness_comparison"],
        "missing_roles": [] if complete else ["robustness_comparison"],
        "failed_diagnostics": ["model_check"] if quality == "failed" else [],
        "warning_diagnostics": ["model_check"] if quality == "warning" else [],
    }
    value["analysis_turns"] = [{
        "turn_id": turn_id, "title": "线性与广义线性模型", "source": "template",
        "template_id": "regression_glm", "status": "failed" if failed else "succeeded" if complete else "incomplete",
        "portfolio_plan_id": plan_id, "portfolio_complete": complete, "artifacts": generated,
        "execution_refs": [{"plan_id": plan_id, "job_id": "r-step", "ok": not failed,
                            "status": "failed" if failed else "succeeded", "result_assessment": assessment}],
    }]
    value["portfolio_manifests"] = {plan_id: {
        "plan_id": plan_id, "complete": complete,
        "outstanding_roles": [] if complete else ["robustness_comparison"],
        "diagnostic_status": {"model_check": quality}, "latest_assessment": assessment,
    }}
    store = ResearchResultStore(sid)
    store.save(value)
    persisted, exists, saved_at = store.load()
    assert exists and persisted["schema_version"] == 2
    return {"exists": exists, "saved_at": saved_at, "state": persisted}, store.conversation_outcome(turn_id)


def _run_javascript(payload, expected, checks):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to exercise the workbench restore")
    script = """
const fs = require('fs'), vm = require('vm'), assert = require('assert');
process.env.NODE_ENV = 'test';
global.localStorage = {getItem: () => null, setItem: () => {}};
global.sessionStorage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
global.HTMLElement = class HTMLElement {};
global.Element = class Element {};
function element() {
  const classes = new Set();
  return {textContent: '', innerHTML: '', hidden: true, disabled: false, dataset: {}, value: '',
    listeners: {}, className: '', classList: {toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
      add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)},
    addEventListener(name, listener) {this.listeners[name] = listener;}, setAttribute() {}, removeAttribute() {},
    querySelector: () => null, querySelectorAll: () => [],
  };
}
const nodes = Object.fromEntries([
  'rsw-result-view-state', 'rsw-result-state', 'rsw-top-dataset-name', 'rsw-top-stage',
  'rsw-top-readiness', 'rsw-results-ready', 'rsw-run-state', 'rsw-status-board',
  'rsw-status-title', 'rsw-status-detail', 'rsw-stop-run', 'rsw-run-plan-btn',
  'rsw-overview-turns', 'rsw-overview-metrics', 'rsw-turn-list', 'rsw-copy-summary', 'rsw-export-draft',
  'rsw-export-word', 'rsw-export-excel', 'rsw-export-figures', 'rsw-export-package',
].map(id => [id, element()]));
const runTitle = element(), runDetail = element(), send = element(), continuation = element();
nodes['rsw-run-state'].querySelector = selector => selector === 'strong' ? runTitle : selector === 'small' ? runDetail : null;
global.document = {
  listeners: {}, addEventListener(name, listener) {this.listeners[name] = listener;},
  getElementById: id => nodes[id] || null,
  querySelector: selector => selector === '.rsw-composer button[type="submit"]' ? send : null,
  querySelectorAll: selector => selector === '[data-continue-analysis]' ? [continuation] : [],
  createElement: () => element(),
};
global.window = global;
global.addEventListener = () => {};
global.__RSW_TEST_HARNESS__ = {};
const requests = [];
"""
    script += f"const payload = {json.dumps(payload, ensure_ascii=False)};\nconst expected = {json.dumps(expected, ensure_ascii=False)};\n"
    script += """
const ready = {llm: {configured: true}, r_runtime: {available: true}, capabilities: [{id: 'ai', status: 'ready'}, {id: 'r_execute', status: 'ready'}, {id: 'inspect', status: 'ready'}]};
global.fetch = async (url, options = {}) => {
  requests.push({url, method: options.method || 'GET'});
  let response;
  if (url.endsWith('/research/result-state')) response = payload;
  else if (url === '/api/local/session') response = {current_session_id: 'restore-session', csrf_token: 'synthetic-csrf'};
  else if (url.endsWith('/model-sharing')) response = {policy: {version: 1, result_mode: 'summary_only', sensitive_columns: [], send_result_images: false, send_attached_images: true}};
  else if (url.endsWith('/resume')) response = {ok: true};
  else if (url.endsWith('/load-current')) response = {history: []};
  else if (url.endsWith('/research/readiness')) response = ready;
  else if (url.endsWith('/research/datasets')) response = {datasets: [{...payload.state.dataset, profile: {summary: payload.state.dataset.summary}}], active_dataset_id: payload.state.dataset.dataset_id};
  else if (url.endsWith('/research/templates')) response = {templates: [], workflows: []};
  else if (url.includes('/jobs?active=true')) response = {jobs: []};
  else throw new Error('Unexpected endpoint: ' + url);
  return {ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(response))};
};
"""
    script += f"vm.runInThisContext(fs.readFileSync({json.dumps(str(WORKBENCH))}, 'utf8'));\n"
    script += "const hooks = global.__RSW_TEST_HARNESS__;\n"
    script += "(async () => {\n" + checks + "\n})().catch(error => {console.error(error); process.exitCode = 1;});\n"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("complete,quality,failed,artifacts,label,stage,action", [
    (True, "passed", False, True, "已生成", "分析产物齐全", "继续分析"),
    (True, "warning", False, True, "已生成", "分析产物齐全", "继续分析"),
    (True, "failed", False, True, "已生成", "分析产物齐全", "继续分析"),
    (True, "not_assessed", False, True, "已生成", "分析产物齐全", "继续分析"),
    (False, "incomplete", False, True, "待补齐", "分析待补齐", "补齐产物"),
    (False, "incomplete", True, True, "需处理", "执行未完成", "修复执行"),
    (True, "passed", True, True, "需处理", "执行未完成", "修复执行"),
    (False, "incomplete", True, False, "需处理", "执行未完成", "修复执行"),
])
def test_schema2_restore_unifies_badges_stage_ready_banner_and_continuation(
    tmp_path, monkeypatch, complete, quality, failed, artifacts, label, stage, action,
):
    payload, expected = _saved_state(tmp_path, monkeypatch, complete=complete, quality=quality, failed=failed, artifacts=artifacts)
    _run_javascript(payload, expected, f"""
hooks.state.sid = 'restore-session';
hooks.state.activeView = 'assistant';
hooks.state.readiness = ready;
await hooks.loadPersistedResultState();
assert.strictEqual(hooks.state.analysisResult, null, 'Canonical restore must not create a legacy result cache');
assert.strictEqual(nodes['rsw-result-state'].textContent, {json.dumps(label)});
assert.strictEqual(nodes['rsw-result-view-state'].textContent, {json.dumps(label)});
assert.strictEqual(nodes['rsw-top-stage'].textContent, {json.dumps(stage)});
assert.match(nodes['rsw-top-readiness'].innerHTML, /可分析/, 'Runtime readiness is independent of analysis delivery');
assert.strictEqual(nodes['rsw-results-ready'].hidden, {json.dumps(not artifacts)});
const turn = hooks.state.resultState.analysis_turns[0];
const actual = hooks.resultTurnOutcome(turn);
for (const key of ['completed', 'analysis_status', 'portfolio_complete', 'plan_id', 'diagnostic_quality']) {{
  assert.deepStrictEqual(actual[key], expected[key], 'The frontend must match canonical backend ' + key);
}}
assert.strictEqual(hooks.resultCompletionPresentation(turn).actionLabel, {json.dumps(action)});
assert.strictEqual(hooks.resultCompletionPresentation(turn).complete, expected.completed);
assert.strictEqual(continuation.disabled, false, 'Partial/diagnostic-limited results can be continued');
assert.strictEqual(send.disabled, false);
assert.strictEqual(nodes['rsw-stop-run'].hidden, true);
assert.strictEqual(nodes['rsw-run-plan-btn'].hidden, true, 'Restoration must not offer an implicit duplicate execution');
for (const id of ['rsw-copy-summary', 'rsw-export-draft', 'rsw-export-word', 'rsw-export-excel', 'rsw-export-figures', 'rsw-export-package']) {{
  assert.strictEqual(nodes[id].disabled, false, 'Saved/partial evidence remains exportable');
}}
const shouldWarn = !expected.completed || expected.diagnostic_quality !== 'passed';
assert.strictEqual(hooks.state.status.kind, shouldWarn ? 'warning' : 'ok');
assert.strictEqual(runTitle.textContent, shouldWarn ? '需确认' : '空闲');
if ({json.dumps(artifacts)}) {{
  assert.match(nodes['rsw-overview-turns'].innerHTML, /7 表 · 1 图 · 3 文本/);
  assert.match(nodes['rsw-turn-list'].innerHTML, /7 表 · 1 图 · 3 文本/);
}}
assert.ok(nodes['rsw-overview-metrics'].innerHTML.includes('<strong>120</strong>'));
hooks.state.activeView = 'results';
hooks.renderAll();
assert.strictEqual(nodes['rsw-results-ready'].hidden, true);
assert.strictEqual(requests.filter(r => r.url.endsWith('/research/result-state')).length, 1);
assert.ok(requests.every(r => r.method === 'GET'), 'Restoring results does not start analysis or mutate API state');
""")


def test_full_page_init_does_not_overwrite_restored_warning_with_generic_idle(tmp_path, monkeypatch):
    payload, expected = _saved_state(tmp_path, monkeypatch, quality="warning")
    _run_javascript(payload, expected, """
await hooks.init();
assert.strictEqual(nodes['rsw-result-state'].textContent, '已生成');
assert.strictEqual(nodes['rsw-result-view-state'].textContent, '已生成');
assert.strictEqual(nodes['rsw-top-stage'].textContent, '分析产物齐全');
assert.strictEqual(runTitle.textContent, '需确认');
assert.strictEqual(runDetail.textContent, '分析产物已齐全');
assert.strictEqual(hooks.state.status.kind, 'warning');
assert.match(hooks.state.status.detail, /方法诊断仍需确认/);
assert.strictEqual(hooks.state.analysisResult, null);
assert.ok(requests.some(r => r.url.includes('/jobs?active=true')));
assert.ok(!requests.some(r => r.url.includes('/chat/stream') || r.url.includes('/analysis/run')));
""")


def test_selecting_saved_turn_updates_all_surfaces_from_its_own_manifest(tmp_path, monkeypatch):
    payload, expected = _saved_state(tmp_path, monkeypatch, quality="warning")
    pending = copy.deepcopy(payload["state"]["analysis_turns"][0])
    pending.update(turn_id="pending-turn", portfolio_plan_id="pending-plan", portfolio_complete=False, status="incomplete")
    pending["execution_refs"][0].update(plan_id="pending-plan", result_assessment={"complete": False, "r_executed": True, "diagnostic_quality": "not_assessed"})
    payload["state"]["analysis_turns"].append(pending)
    payload["state"]["portfolio_manifests"]["pending-plan"] = {"complete": False, "outstanding_roles": ["calibration_plot"], "diagnostic_status": {"calibration": "not_assessed"}}
    _run_javascript(payload, expected, """
await hooks.init();
assert.strictEqual(hooks.state.activeTurnId, 'pending-turn');
assert.strictEqual(nodes['rsw-result-state'].textContent, '待补齐');
assert.strictEqual(nodes['rsw-top-stage'].textContent, '分析待补齐');
nodes['rsw-turn-list'].listeners.click({target: {closest: () => ({dataset: {turnId: 'restored-turn'}})}});
assert.strictEqual(hooks.state.activeTurnId, 'restored-turn');
assert.strictEqual(nodes['rsw-result-state'].textContent, '已生成');
assert.strictEqual(nodes['rsw-result-view-state'].textContent, '已生成');
assert.strictEqual(nodes['rsw-top-stage'].textContent, '分析产物齐全');
assert.strictEqual(runDetail.textContent, '分析产物已齐全');
assert.strictEqual(hooks.continuationTemplateContext().plan_id, 'frozen-plan');
nodes['rsw-turn-list'].listeners.click({target: {closest: () => ({dataset: {turnId: 'pending-turn'}})}});
assert.strictEqual(nodes['rsw-result-state'].textContent, '待补齐');
assert.strictEqual(nodes['rsw-result-view-state'].textContent, '待补齐');
assert.strictEqual(nodes['rsw-top-stage'].textContent, '分析待补齐');
assert.strictEqual(hooks.continuationTemplateContext().plan_id, 'pending-plan');
assert.strictEqual(hooks.resultCompletionPresentation(hooks.state.resultState.analysis_turns[1]).complete, false);
assert.strictEqual(continuation.disabled, false);
""")
