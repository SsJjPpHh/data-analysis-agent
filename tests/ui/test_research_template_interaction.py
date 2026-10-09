import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "static" / "js" / "research_workbench.js"
WORKBENCH_TEMPLATE = ROOT / "templates" / "research_workbench.html"


def test_repeated_template_actions_share_the_pending_preparation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the workbench interaction test")

    script = f"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
process.env.NODE_ENV = "test";

global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.sessionStorage = {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }};
global.document = {{
  addEventListener: () => {{}},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
}};
global.window = global;
global.HTMLElement = class HTMLElement {{}};
global.Element = class Element {{}};
global.__RSW_TEST_HARNESS__ = {{}};

let releaseRequest;
let fetchCalls = 0;
global.fetch = () => {{
  fetchCalls += 1;
  return new Promise((resolve) => {{ releaseRequest = resolve; }});
}};

const source = fs.readFileSync({json.dumps(str(WORKBENCH))}, "utf8");
vm.runInThisContext(source, {{ filename: {json.dumps(str(WORKBENCH))} }});

const hooks = global.__RSW_TEST_HARNESS__;
hooks.state.sid = "session-template-click";
hooks.state.activeDataset = {{ dataset_id: "dataset-template-click", name: "study.csv" }};

assert.strictEqual(
  hooks.userFacingResolvedInput({{
    input_id: "data_object",
    label: "counts 矩阵",
    kind: "object",
    value: "dataset-template-click",
  }}),
  "counts 矩阵=当前上传数据（study.csv）",
  "the user-facing prompt must not render a dataset UUID as a matrix value"
);

(async () => {{
  const first = hooks.prepareTemplate("data_quality", {{ silent: true }});
  const second = hooks.prepareTemplate("data_quality", {{ silent: true }});
  await Promise.resolve();
  assert.strictEqual(fetchCalls, 1, "a rapid second click must reuse the same request");

  const preparation = {{
    status: "ready",
    status_label: "可直接使用",
    questions: [],
    resolved_inputs: [],
    plan: {{ plan_id: "plan-template-click", template_id: "data_quality" }},
  }};
  releaseRequest({{
    ok: true,
    json: async () => preparation,
  }});

  const [firstResult, secondResult] = await Promise.all([first, second]);
  assert.deepStrictEqual(firstResult, preparation);
  assert.deepStrictEqual(secondResult, preparation);
}})().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""

    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

    source = WORKBENCH.read_text(encoding="utf-8")
    assert "const templatePreparePromises = new Map();" in source
    assert "准备中 · 点击后自动使用" in source
    assert 'aria-busy="${loading ? "true" : "false"}"' in source


def test_out_of_order_role_preparations_cannot_restore_a_stale_plan():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the workbench interaction test")

    script = f"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
process.env.NODE_ENV = "test";
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.sessionStorage = {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }};
global.document = {{
  addEventListener: () => {{}},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
}};
global.window = global;
global.HTMLElement = class HTMLElement {{}};
global.Element = class Element {{}};
global.__RSW_TEST_HARNESS__ = {{}};

const pending = [];
global.fetch = () => new Promise((resolve) => pending.push(resolve));
const source = fs.readFileSync({json.dumps(str(WORKBENCH))}, "utf8");
vm.runInThisContext(source, {{ filename: {json.dumps(str(WORKBENCH))} }});
const hooks = global.__RSW_TEST_HARNESS__;
hooks.state.sid = "session-race";
hooks.state.activeDataset = {{ dataset_id: "dataset-race", name: "study.csv" }};
hooks.state.templateInputValues.data_quality = {{ group: "gender" }};

(async () => {{
  const first = hooks.prepareTemplate("data_quality", {{ silent: true }});
  await Promise.resolve();
  hooks.state.templateInputValues.data_quality = {{ group: "cvd" }};
  const second = hooks.prepareTemplate("data_quality", {{ silent: true }});
  await Promise.resolve();
  assert.strictEqual(pending.length, 2);

  pending[1]({{ ok: true, json: async () => ({{
    status: "ready", questions: [], resolved_inputs: [],
    plan: {{ plan_id: "plan-cvd", resolved_inputs: {{ group: {{ value: "cvd" }} }} }},
  }}) }});
  await second;
  pending[0]({{ ok: true, json: async () => ({{
    status: "ready", questions: [], resolved_inputs: [],
    plan: {{ plan_id: "plan-gender", resolved_inputs: {{ group: {{ value: "gender" }} }} }},
  }}) }});
  await first;

  assert.strictEqual(
    hooks.state.templatePreparations.data_quality.plan.plan_id,
    "plan-cvd",
    "an older response must not overwrite the latest role selection"
  );
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""

    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)


def test_dataset_replacement_dialog_explains_and_previews_the_transition():
    source = WORKBENCH.read_text(encoding="utf-8")
    template = WORKBENCH_TEMPLATE.read_text(encoding="utf-8")

    for node_id in (
        "rsw-replace-current-name",
        "rsw-replace-current-meta",
        "rsw-replace-incoming-name",
        "rsw-replace-incoming-meta",
        "rsw-replace-impact",
    ):
        assert f'id="{node_id}"' in template
    assert 'aria-describedby="rsw-replace-description rsw-replace-impact"' in template
    assert "function chooseDatasetReplacement(upload)" in source
    assert "chooseDatasetReplacement(upload)" in source
    assert "const incomingSize = Number(upload?.size || 0);" in source
    assert "formatBytes(incomingSize)" in source
    assert 'data-replace-action="cancel"' in template
    assert 'event.key === "Escape" && !$("rsw-replace-modal")?.hidden' in source


def test_desktop_file_bridge_has_a_bounded_browser_upload_fallback():
    source = WORKBENCH.read_text(encoding="utf-8")

    assert "const timeoutId = globalThis.setTimeout(() => finish(null), 1500);" in source
    assert 'if (typeof bridge[method] !== "function") return null;' in source
    assert '$("rsw-file-input")?.click();' in source
    assert "uploadFiles(event.target.files)" in source


def test_desktop_bridge_that_never_initializes_resolves_to_browser_fallback():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the workbench interaction test")

    script = f"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
process.env.NODE_ENV = "test";
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.sessionStorage = {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }};
global.document = {{
  addEventListener: () => {{}},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
}};
global.window = global;
global.HTMLElement = class HTMLElement {{}};
global.Element = class Element {{}};
global.__RSW_TEST_HARNESS__ = {{}};
global.qt = {{ webChannelTransport: {{}} }};
global.QWebChannel = function NeverReady() {{}};
global.setTimeout = (callback) => {{ queueMicrotask(callback); return 1; }};
global.clearTimeout = () => {{}};
const source = fs.readFileSync({json.dumps(str(WORKBENCH))}, "utf8");
vm.runInThisContext(source, {{ filename: {json.dumps(str(WORKBENCH))} }});

(async () => {{
  const selection = await global.__RSW_TEST_HARNESS__.requestDesktopImportSelection("files");
  assert.strictEqual(selection, null);
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""

    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
