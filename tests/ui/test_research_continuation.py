import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "static" / "js" / "research_workbench.js"


def test_continue_button_submits_once_with_the_selected_turns_frozen_plan():
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

const source = fs.readFileSync({json.dumps(str(WORKBENCH))}, "utf8");
vm.runInThisContext(source, {{ filename: {json.dumps(str(WORKBENCH))} }});

const hooks = global.__RSW_TEST_HARNESS__;
hooks.state.analysisTurnId = "turn-natural-pending";
hooks.handleResearchToolAudit({{
  tool: "set_analysis_plan",
  analysis_turn_id: "turn-natural-pending",
  content: JSON.stringify({{
    ok: true,
    analysis_plan: {{
      plan_id: "plan-natural-pending",
      template_id: "",
      intent: "自由对话生存分析",
    }},
  }}),
}});
assert.deepStrictEqual(hooks.state.continuationContexts["turn-natural-pending"], {{
  templateId: "",
  planId: "plan-natural-pending",
  title: "自由对话生存分析",
}});

hooks.state.resultState = {{
  analysis_turns: [
    {{
      turn_id: "turn-old",
      title: "论文 Table 1 / 三线表",
      template_id: "publication_table",
      execution_refs: [{{ plan_id: "plan-old-bindings" }}],
    }},
    {{
      turn_id: "turn-new",
      title: "另一轮 Table 1",
      template_id: "publication_table",
      execution_refs: [{{ plan_id: "plan-new-bindings" }}],
    }},
  ],
}};
hooks.state.activeTurnId = "turn-new";
hooks.state.activeAnalysisPlan = {{ template_id: "publication_table", plan_id: "plan-new-bindings" }};
hooks.state.continuationContexts = {{
  "turn-old": {{ templateId: "publication_table", planId: "plan-old-bindings" }},
  "turn-new": {{ templateId: "publication_table", planId: "plan-new-bindings" }},
}};

(async () => {{
  const calls = [];
  const oldButton = {{ dataset: {{ continueAnalysis: "turn-old" }} }};
  await hooks.continueAnalysisFromButton(oldButton, async (message) => {{
    calls.push({{
      message,
      templateContext: {{ ...hooks.state.activeTemplateContext }},
    }});
  }});

  assert.strictEqual(calls.length, 1);
  assert.deepStrictEqual(calls[0].templateContext, {{
    id: "publication_table",
    plan_id: "plan-old-bindings",
  }});
  assert.match(calls[0].message, /所选分析轮次「论文 Table 1 \\/ 三线表」/);
  assert.match(calls[0].message, /补全模板既定表格、图形和解释/);
  assert.strictEqual(hooks.continuationTemplateContext("missing-turn"), null);

  hooks.state.resultState.analysis_turns.push({{
    turn_id: "turn-freeform",
    title: "自由对话的多因素分析",
    source: "natural_language",
    template_id: "",
    execution_refs: [{{ plan_id: "plan-freeform" }}],
    portfolio_plan_id: "plan-freeform",
    portfolio_complete: false,
    status: "incomplete",
    summary: {{ table_count: 2, figure_count: 1, text_count: 1, model_count: 1 }},
  }});
  hooks.state.resultState.portfolio_manifests = {{
    "plan-freeform": {{ complete: false, outstanding_roles: ["calibration_plot", "sensitivity_table"] }},
  }};
  assert.deepStrictEqual(hooks.continuationTemplateContext("turn-freeform"), {{
    id: "",
    plan_id: "plan-freeform",
    title: "自由对话的多因素分析",
  }});
  assert.deepStrictEqual(hooks.resultCompletionPresentation(
    hooks.state.resultState.analysis_turns[2]
  ), {{
    complete: false,
    statusText: "仍有 2 项科研产物待补齐",
    actionLabel: "补齐产物",
    countsText: "2 表 · 1 图 · 1 文本 · 1 模型",
    validationText: "自动校验范围：产物文件与结构；科学审核：尚未进行",
  }});

  assert.strictEqual(hooks.resultCompletionPresentation({{status: "succeeded"}}).complete, false);
  hooks.state.resultState.portfolio_manifests["plan-freeform"] = {{
    complete: true, outstanding_roles: [], diagnostic_status: {{convergence: "failed"}},
  }};
  const failedDiagnostic = hooks.resultCompletionPresentation(hooks.state.resultState.analysis_turns[2]);
  assert.strictEqual(failedDiagnostic.complete, true);
  assert.match(failedDiagnostic.statusText, /诊断未通过/);
  assert.match(failedDiagnostic.validationText, /科学审核：尚未进行/);
  hooks.state.resultState.portfolio_manifests["plan-freeform"].diagnostic_status = {{convergence: "passed"}};
  assert.match(hooks.resultCompletionPresentation(hooks.state.resultState.analysis_turns[2]).statusText, /统计结论待复核/);
  hooks.state.resultState.portfolio_manifests["plan-freeform"].diagnostic_status = {{convergence: "not_assessed"}};
  assert.match(hooks.resultCompletionPresentation(hooks.state.resultState.analysis_turns[2]).statusText, /诊断未评估/);

  let releaseFirst;
  let concurrentCalls = 0;
  const firstPending = new Promise((resolve) => {{ releaseFirst = resolve; }});
  const first = hooks.continueAnalysisFromButton(oldButton, async () => {{
    concurrentCalls += 1;
    await firstPending;
  }});
  await Promise.resolve();
  await hooks.continueAnalysisFromButton(
    {{ dataset: {{ continueAnalysis: "turn-new" }} }},
    async () => {{ concurrentCalls += 1; }},
  );
  assert.strictEqual(concurrentCalls, 1);
  releaseFirst();
  await first;
}})().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""

    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
