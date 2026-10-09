"""Historical R source must stay useful while local paths/values stay private."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess

import pytest

from agent.agent import BusinessAgent
from agent.analysis.model_egress import ModelEgressGuard
from tests.core.test_agent_r_figure_vision import _Client, _Completions, _FinalStream


# Reduced from the actual behavior-category provider request. The original
# prose path regex replaced /s^3 and /s^4 with an invalid R symbol, and even
# damaged JSON around collapse="/" before the model copied it for repair.
ARITHMETIC_CODE = '''skew <- mapply(function(v, m, s) mean((v - m)^3)/s^3, x, m1, s1)
kurt <- mapply(function(v, m, s) mean((v - m)^4)/s^4 - 3, x, m1, s1)
smd <- abs(mean(a) - mean(b))/sqrt((sd(a)^2 + sd(b)^2)/2)
proportion <- as.numeric(counts)/sum(counts)
formula <- y ~ x/z + I(x / sqrt(z))
integer_quotient <- 7 %/% 2
remainder <- 7 %% 2
labels <- paste(levels(group), collapse = "/")
message("a/b", 'quote: " and escaped apostrophe: \\'' )
'''
PRIVATE_TOKEN = "SYNTHETIC_PRIVATE_SUBJECT_314159"


def _history(code: str, *, extra=None):
    arguments = {"code": code, **(extra or {})}
    return [{
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "historical-r", "type": "function", "function": {
            "name": "run_r_code", "arguments": json.dumps(arguments),
        }}],
    }, {"role": "tool", "tool_call_id": "historical-r", "content": json.dumps({
        "tool": "run_r_code", "data": {"ok": True},
    })}]


def _arguments(messages):
    return json.loads(messages[0]["tool_calls"][0]["function"]["arguments"])


def test_actual_arithmetic_formula_and_separator_survive_structured_history_projection():
    history = _history(ARITHMETIC_CODE)
    before = copy.deepcopy(history)
    guard = ModelEgressGuard()

    projected = guard.messages(history)

    assert _arguments(projected)["code"] == ARITHMETIC_CODE
    assert "[已隐藏路径]" not in _arguments(projected)["code"]
    assert projected[0]["tool_calls"][0]["id"] == "historical-r"
    assert projected[0]["tool_calls"][0]["function"]["name"] == "run_r_code"
    assert history == before
    assert guard.messages(projected) == projected


@pytest.mark.parametrize("literal", [
    r'"/Users/research/private-cohort.csv"',
    r"'/home/research/private-cohort.csv'",
    r'"C:\\Users\\research\\private-cohort.csv"',
    r'"\\\\server\\research\\private-cohort.csv"',
    r'"\u002fUsers\u002fresearch\u002fprivate-cohort.csv"',
    r'"\x2fUsers\x2fresearch\x2fprivate-cohort.csv"',
    r'"\057Users\057research\057private-cohort.csv"',
    r'r"(/Users/research/private-cohort.csv)"',
    r'r"---[C:\Users\research\private-cohort.csv]---"',
    r'`/Users/research/private-cohort.csv`',
])
def test_r_literals_and_encoded_paths_stay_private_without_changing_division(literal):
    code = f"local_path <- {literal}\nratio <- sum(x)/sqrt(sum(x^2))\n"

    projected = _arguments(ModelEgressGuard().messages(_history(code)))["code"]

    assert "[已隐藏路径]" in projected
    assert "research" not in projected and "private-cohort" not in projected
    assert "ratio <- sum(x)/sqrt(sum(x^2))" in projected


def test_comments_and_quotes_are_scanned_in_their_actual_r_lexical_context():
    code = (
        'value <- mean(x)/sqrt(length(x)) # input /Users/research/cohort.csv\n'
        'note <- "# is a literal, a/b is a separator"\n'
        '# "quoted comment" /home/research/cohort.csv\n'
        'raw_note <- r"--{# literal /Users/research/cohort.csv}--"\n'
    )

    projected = _arguments(ModelEgressGuard().messages(_history(code)))["code"]

    assert 'value <- mean(x)/sqrt(length(x)) # input [已隐藏路径]' in projected
    assert 'note <- "# is a literal, a/b is a separator"' in projected
    assert "research" not in projected and "cohort.csv" not in projected
    assert 'r"--{# literal [已隐藏路径]}--"' in projected


@pytest.mark.parametrize("literal", [
    f'"{PRIVATE_TOKEN}"',
    "\"" + r"\u0053" + PRIVATE_TOKEN[1:] + "\"",
    f'r"({PRIVATE_TOKEN})"',
])
def test_learned_sensitive_values_are_hidden_inside_r_literal_variants(literal):
    history = _history(f"label <- {literal}\nratio <- sum(x)/sqrt(length(x))")
    history[1]["content"] = json.dumps({"metadata": {"subject_id": PRIVATE_TOKEN}})

    projected = ModelEgressGuard().messages(history)
    code = _arguments(projected)["code"]

    assert PRIVATE_TOKEN not in json.dumps(projected)
    assert "[个体_1]" in code
    assert "sum(x)/sqrt(length(x))" in code


def test_sensitive_values_remain_masked_in_comments_and_executable_identifier_regions():
    guard = ModelEgressGuard({"sensitive_columns": ["clinical_note"]})
    guard.project({"clinical_note": PRIVATE_TOKEN})
    code = f"# {PRIVATE_TOKEN}\n{PRIVATE_TOKEN} <- 1\nratio <- sum(x)/sum(y)\n"

    projected = _arguments(guard.messages(_history(code)))["code"]

    assert PRIVATE_TOKEN not in projected
    assert "[敏感值已隐藏]" in projected
    assert "ratio <- sum(x)/sum(y)" in projected


def test_non_code_tool_arguments_keep_paths_private_and_the_json_protocol_valid():
    history = _history(ARITHMETIC_CODE, extra={
        "description": 'source "/Users/research/cohort.csv"',
        "options": {"path": r"C:\Users\research\cohort.csv", "alpha": 0.05},
    })

    arguments = _arguments(ModelEgressGuard().messages(history))

    assert arguments["code"] == ARITHMETIC_CODE
    assert arguments["options"]["alpha"] == 0.05
    assert "research" not in arguments["description"]
    assert "research" not in arguments["options"]["path"]


def test_structured_r_arguments_in_projection_use_the_same_code_boundary():
    payload = {"arguments": {"code": ARITHMETIC_CODE + 'path <- "/tmp/private-cohort.csv"\n'}}
    before = copy.deepcopy(payload)

    projected = ModelEgressGuard().project(payload)

    assert projected["arguments"]["code"].startswith(ARITHMETIC_CODE)
    assert "/tmp/private-cohort.csv" not in projected["arguments"]["code"]
    assert payload == before


def test_parseable_projected_r_preserves_quotes_raw_delimiters_and_arithmetic():
    rscript = shutil.which("Rscript")
    if rscript is None:
        pytest.skip("Rscript required for the syntax-only R parser check")
    code = ARITHMETIC_CODE + (
        'path <- "/Users/research/cohort.csv"\n'
        "single_path <- '/home/research/cohort.csv'\n"
        'raw_path <- r"---[C:\\Users\\research\\cohort.csv]---"\n'
    )
    projected = _arguments(ModelEgressGuard().messages(_history(code)))["code"]

    parsed = subprocess.run(
        [rscript, "--vanilla", "-e", "invisible(parse(text=commandArgs(TRUE)[1]))", projected],
        check=False, text=True, capture_output=True, timeout=20,
    )

    assert parsed.returncode == 0, parsed.stderr


def test_actual_provider_kwargs_keep_repairable_r_history_and_hide_local_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    client = _Client()
    client.chat.completions = _Completions(first_stream=_FinalStream())
    code = ARITHMETIC_CODE + 'path <- "/Users/research/cohort.csv"\n'
    history = _history(code)
    before = copy.deepcopy(history)
    agent = BusinessAgent(client, "deepseek-flash", workspace_id="", model_sharing_policy={})

    list(agent.run("解释现有结果", history))

    messages = client.chat.completions.calls[0]["messages"]
    past = next(message for message in messages if message.get("tool_calls"))
    arguments = json.loads(past["tool_calls"][0]["function"]["arguments"])
    assert arguments["code"].startswith(ARITHMETIC_CODE)
    assert "/Users/research" not in arguments["code"]
    assert "[已隐藏路径]" in arguments["code"]
    assert history == before
