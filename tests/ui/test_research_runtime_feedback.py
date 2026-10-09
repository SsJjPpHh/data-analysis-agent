import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "static" / "js" / "research_workbench.js"


def test_recoverable_r_failure_stays_busy_until_a_later_step_succeeds():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the workbench runtime feedback test")

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

const source = fs.readFileSync({json.dumps(str(WORKBENCH))}, "utf8");
vm.runInThisContext(source, {{ filename: {json.dumps(str(WORKBENCH))} }});

const hooks = global.__RSW_TEST_HARNESS__;
hooks.state.streaming = true;
hooks.state.analysisTurnId = "turn-repair";

(async () => {{
  await hooks.handleResearchToolAudit({{
    tool: "run_r_code",
    ok: false,
    content: JSON.stringify({{
      result: {{
        ok: false,
        error: "object 'group' not found",
        code: "table(dataset$group)",
        stdout: "",
        stderr: "object 'group' not found",
        tables: [], figures: [], texts: [], models: [], artifacts: [],
      }},
    }}),
  }});

  assert.strictEqual(hooks.state.status.kind, "busy");
  assert.match(hooks.state.status.title, /正在修正/);
  assert.strictEqual(hooks.state.recoveringRStep, true);

  await hooks.handleResearchToolAudit({{
    tool: "run_r_code",
    ok: true,
    content: JSON.stringify({{
      result: {{
        ok: true,
        error: "",
        code: "table(dataset$gender)",
        stdout: "saved",
        stderr: "",
        tables: [{{ name: "group_table", path: "group_table.json" }}],
        figures: [], texts: [], models: [], artifacts: [],
      }},
    }}),
  }});

  assert.notStrictEqual(hooks.state.status.kind, "error");
  assert.strictEqual(hooks.state.recoveringRStep, false);
  assert.strictEqual(hooks.state.analysisResult.payload.result.ok, true);
  assert.match(hooks.state.analysisResult.payload.result.warnings.join(String.fromCharCode(10)), /object 'group' not found/);
}})().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""

    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)


def test_streaming_markdown_updates_are_coalesced_before_rendering():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the workbench runtime feedback test")

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

const source = fs.readFileSync({json.dumps(str(WORKBENCH))}, "utf8");
vm.runInThisContext(source, {{ filename: {json.dumps(str(WORKBENCH))} }});
const hooks = global.__RSW_TEST_HARNESS__;

const scheduled = [];
const rendered = [];
const renderer = hooks.createStreamingMessageRenderer({{}}, {{
  render: (_target, value) => rendered.push(value),
  schedule: (callback) => {{ scheduled.push(callback); return callback; }},
  cancel: (callback) => {{
    const index = scheduled.indexOf(callback);
    if (index >= 0) scheduled.splice(index, 1);
  }},
}});

for (let index = 1; index <= 100; index += 1) renderer.update(`片段 ${{index}}`);
assert.strictEqual(scheduled.length, 1, "burst updates should schedule one render");
assert.strictEqual(rendered.length, 0);
scheduled.shift()();
assert.deepStrictEqual(rendered, ["片段 100"]);

renderer.update("最终 Markdown");
renderer.flush();
assert.deepStrictEqual(rendered, ["片段 100", "最终 Markdown"]);
assert.strictEqual(scheduled.length, 0);
"""

    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
