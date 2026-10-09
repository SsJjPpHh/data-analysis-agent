"""Frozen-plan R binding checks, job-path probes, and trusted accessors."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from data.research_results import generation_root
from data.session import validate_session_id

_DIRECT_FILE_READ_RE = re.compile(
    r"""
    \b(?:
        (?:utils::)?read\.(?:csv2?|delim2?|table)
        |(?:readr::)?read_(?:csv2?|tsv|delim|table|file|lines)
        |(?:data\.table::)?fread
        |(?:readxl::)?read_(?:excel|xlsx)
        |(?:openxlsx::)?read\.xlsx
        |(?:base::)?readlines
        |(?:base::)?readrds
    )\s*\(\s*(?:[a-z_][a-z0-9_.]*\s*=\s*)?(?P<quote>["'])(?P<path>[^"']+)(?P=quote)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DIRECT_FILE_PROBE_RE = re.compile(
    r"""
    \b(?:file\.exists|file\.info|dir\.exists)
    \s*\(\s*(?:[a-z_][a-z0-9_.]*\s*=\s*)?(?P<quote>["'])(?P<path>[^"']+)(?P=quote)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DIRECT_FILE_EXTENSIONS = (
    ".csv",
    ".tsv",
    ".txt",
    ".rds",
    ".xlsx",
    ".xls",
    ".json",
    ".parquet",
    ".feather",
)

_ROLE_SYMBOL_ASSIGNMENT_RE = re.compile(
    r"(?m)(?:^|;)\s*(?P<symbol>[A-Za-z.][A-Za-z0-9._]*)\s*"
    r"(?P<operator><-|=(?!=))\s*(?P<rhs>[^;\r\n]+)"
)
_ROLE_LITERAL_VALUE_RE = re.compile(
    r'''\s*(?:(?:c|as\.character)\s*\(\s*)?(?P<quote>["'])(?P<value>[^"'\r\n]+)'''
    r'''(?P=quote)\s*\)?\s*;?\s*(?:#.*)?$''',
    re.IGNORECASE,
)
_TABLE_ONE_BY_RE = re.compile(
    r'''(?<![A-Za-z0-9._])by\s*=\s*(?P<rhs>'''
    r'''(?:(?:c|as\.character)\s*\(\s*)?["'][^"'\r\n]+["']\s*\)?'''
    r'''|[A-Za-z.][A-Za-z0-9._]*)''',
    re.IGNORECASE,
)
_PLAN_INPUT_CALL_RE = re.compile(
    r'''(?<![A-Za-z0-9._])baa_plan_input\s*\(\s*["'](?P<input_id>[a-z0-9_]+)["']\s*\)''',
    re.IGNORECASE,
)
_FROZEN_ACCESSOR_MUTATION_RE = re.compile(
    r'''(?mix)
    (?:^\s*baa_plan_input\s*(?:<-|=(?!=)))
    |
    (?:(?:base::)?(?:assign|rm|remove|unlockBinding)\s*\([^\r\n]*
       (?:baa_plan_input|\.baa_assert_frozen_inputs_used))
    |
    (?:(?:base::)?(?:environment|parent\.env|body|formals)\s*\(\s*
       (?:baa_plan_input|\.baa_assert_frozen_inputs_used)\s*\))
    |
    (?:(?:base::)?(?:get|get0|mget)\s*\(\s*["']
       (?:baa_plan_input|\.baa_assert_frozen_inputs_used)["'])
    ''',
)
_FROZEN_DYNAMIC_REFLECTION_RE = re.compile(
    r'''(?ix)
    (?<![A-Za-z0-9._])(?:
        \.GlobalEnv(?![A-Za-z0-9._])|\.BaseNamespaceEnv(?![A-Za-z0-9._])|
        (?:
        baseenv|globalenv|parent\.frame|sys\.frame|sys\.frames|sys\.function|
        as\.environment|pos\.to\.env|unlockBinding|makeActiveBinding|eval|evalq|
        eval\.parent|parse|source|sys\.source|getNamespace|asNamespace|
        getFromNamespace|getNamespaceInfo|getExportedValue|global_env|base_env|
        caller_env|current_env|env_get|env_poke|env_bind|env_unlock
        )(?![A-Za-z0-9._])\s*\(
    )
    ''',
)
_ROLE_FAMILY_PATTERNS = {
    "strata": re.compile(r"strata|stratum|分层", re.IGNORECASE),
    "event": re.compile(r"event|status|事件|状态", re.IGNORECASE),
    "outcome": re.compile(r"outcome|response|endpoint|结局|终点|因变量", re.IGNORECASE),
    "exposure": re.compile(r"exposure|predictor|treatment|暴露|自变量|处理变量", re.IGNORECASE),
    "group": re.compile(r"group|arm|分组|组别|组变量", re.IGNORECASE),
    "time": re.compile(r"time|duration|followup|随访时间|生存时间|时间变量", re.IGNORECASE),
    "identifier": re.compile(
        r"^id$|identifier|subject.?id|patient.?id|cluster.?id|center.?id|"
        r"个体.?id|中心.?id|样本编号|病例编号|标识|编号",
        re.IGNORECASE,
    ),
}
_ROLE_SYMBOL_FAMILIES = {
    "strata": re.compile(r"^(?:strata|strata_var|stratum_var|stratify_var)$", re.IGNORECASE),
    "group": re.compile(r"^(?:group|group_var|group_col|arm_var)$", re.IGNORECASE),
    "outcome": re.compile(
        r"^(?:outcome|outcome_var|response_var|endpoint_var|dependent_var)$",
        re.IGNORECASE,
    ),
    "exposure": re.compile(
        r"^(?:exposure|exposure_var|predictor_var|treatment_var)$",
        re.IGNORECASE,
    ),
    "time": re.compile(r"^(?:time_var|duration_var|followup_var|survival_time_var)$", re.IGNORECASE),
    "event": re.compile(r"^(?:event_var|status_var|event_status_var)$", re.IGNORECASE),
    "identifier": re.compile(
        r"^(?:id_var|identifier_var|subject_id_var|patient_id_var|cluster_id_var|center_id_var)$",
        re.IGNORECASE,
    ),
}


def _r_call_arguments(code: str, function_name: str) -> list[str]:
    """Return balanced arguments for executable direct helper calls.

    R snippets often contain method notes or commented examples.  Those must
    not be mistaken for an analysis call during frozen-plan verification.
    """

    calls: list[str] = []
    text = str(code or "")
    index = 0
    quote = ""
    escaped = False
    comment = False
    while index < len(text):
        char = text[index]
        if comment:
            if char in "\r\n":
                comment = False
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        if char == "#":
            comment = True
            index += 1
            continue
        if not text.startswith(function_name, index):
            index += 1
            continue
        before = text[index - 1] if index else ""
        end_name = index + len(function_name)
        after = text[end_name] if end_name < len(text) else ""
        if (before and (before.isalnum() or before in "._")) or (
            after and (after.isalnum() or after in "._")
        ):
            index = end_name
            continue
        cursor = end_name
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "(":
            index = end_name
            continue
        start = cursor + 1
        depth = 1
        inner_quote = ""
        inner_escaped = False
        inner_comment = False
        close_index = -1
        for cursor in range(start, len(text)):
            inner_char = text[cursor]
            if inner_comment:
                if inner_char in "\r\n":
                    inner_comment = False
                continue
            if inner_quote:
                if inner_escaped:
                    inner_escaped = False
                elif inner_char == "\\":
                    inner_escaped = True
                elif inner_char == inner_quote:
                    inner_quote = ""
                continue
            if inner_char in {'"', "'"}:
                inner_quote = inner_char
            elif inner_char == "#":
                inner_comment = True
            elif inner_char == "(":
                depth += 1
            elif inner_char == ")":
                depth -= 1
                if depth == 0:
                    close_index = cursor
                    break
        if close_index >= 0:
            calls.append(text[start:close_index])
            index = close_index + 1
        else:
            index = end_name
    return calls


def _r_named_arguments(arguments: str) -> dict[str, str]:
    """Split top-level named R arguments without evaluating user code."""

    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False
    text = str(arguments or "")
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    result: dict[str, str] = {}
    for part in parts:
        match = re.match(r"\s*([A-Za-z.][A-Za-z0-9._]*)\s*=\s*(.+?)\s*$", part, re.DOTALL)
        if match:
            result[match.group(1).casefold()] = match.group(2).strip()
    return result


def _r_without_comments(code: str) -> str:
    """Remove R comments while preserving quoted hashes and line boundaries."""

    output: list[str] = []
    quote = ""
    escaped = False
    comment = False
    for char in str(code or ""):
        if comment:
            if char in "\r\n":
                comment = False
                output.append(char)
            continue
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(char)
        elif char == "#":
            comment = True
        else:
            output.append(char)
    return "".join(output)


def _r_plan_input_call_sites(code: str) -> list[tuple[str, int, int]]:
    """Find executable frozen-input calls and their source spans."""

    text = str(code or "")
    function_name = "baa_plan_input"
    result: list[tuple[str, int, int]] = []
    index = 0
    quote = ""
    escaped = False
    while index < len(text):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        if not text.startswith(function_name, index):
            index += 1
            continue
        before = text[index - 1] if index else ""
        end_name = index + len(function_name)
        after = text[end_name] if end_name < len(text) else ""
        if (before and (before.isalnum() or before in "._")) or (
            after and (after.isalnum() or after in "._")
        ):
            index = end_name
            continue
        cursor = end_name
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "(":
            index = end_name
            continue
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] not in {'"', "'"}:
            index = end_name
            continue
        argument_quote = text[cursor]
        cursor += 1
        start = cursor
        while cursor < len(text) and text[cursor] != argument_quote:
            if text[cursor] == "\\":
                cursor += 1
            cursor += 1
        input_id = text[start:cursor]
        cursor = min(len(text), cursor + 1)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor < len(text) and text[cursor] == ")":
            cursor += 1
        call_end = cursor
        if re.fullmatch(r"[a-z0-9_]+", input_id):
            result.append((input_id, index, call_end))
        index = call_end
    return result


def _r_plan_input_ids(code: str) -> set[str]:
    """Find executable baa_plan_input("id") calls outside R string literals."""

    return {input_id for input_id, _start, _end in _r_plan_input_call_sites(code)}


def _r_blank_string_literals(code: str) -> str:
    """Blank quoted R strings while preserving offsets and line boundaries."""

    output: list[str] = []
    quote = ""
    escaped = False
    for char in str(code or ""):
        if quote:
            if char in "\r\n":
                output.append(char)
            else:
                output.append(" ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(" ")
        else:
            output.append(char)
    return "".join(output)


def _r_matching_delimiter(
    text: str,
    start: int,
    opening: str,
    closing: str,
) -> int:
    """Return the matching delimiter index in already de-stringed R text."""

    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _r_blank_inert_regions(code: str) -> str:
    """Blank obvious non-executing R regions while retaining line structure.

    This is deliberately narrow: it covers literal-false branches, quoted
    language objects, and an unused function closure.  It does not attempt to
    prove arbitrary R control flow; the runtime accessor audit remains the
    authoritative execution record.
    """

    chars = list(str(code or ""))

    def blank(start: int, end: int) -> None:
        for index in range(max(0, start), min(len(chars), end)):
            if chars[index] not in "\r\n":
                chars[index] = " "

    # quote()/expression()/alist()/substitute() create language objects and do
    # not consume a protected value for an analysis merely because its symbol
    # appears in the captured expression.
    text = "".join(chars)
    inert_call = re.compile(
        r"(?<![A-Za-z0-9._])(?:base::)?(?:quote|expression|alist|substitute)"
        r"(?![A-Za-z0-9._])\s*\(",
        re.IGNORECASE,
    )
    cursor = 0
    while match := inert_call.search(text, cursor):
        opening = text.find("(", match.start(), match.end())
        closing = _r_matching_delimiter(text, opening, "(", ")")
        if closing < 0:
            break
        blank(match.start(), closing + 1)
        cursor = closing + 1

    # Bodies guarded by a literal FALSE are unreachable. Preserve a following
    # else branch because that branch is executable.
    text = "".join(chars)
    false_branch = re.compile(
        r"(?<![A-Za-z0-9._])(?:if|while)\s*\(\s*FALSE\s*\)",
        re.IGNORECASE,
    )
    cursor = 0
    while match := false_branch.search(text, cursor):
        body_start = match.end()
        while body_start < len(text) and text[body_start].isspace():
            body_start += 1
        if body_start < len(text) and text[body_start] == "{":
            body_end = _r_matching_delimiter(text, body_start, "{", "}")
            body_end = len(text) if body_end < 0 else body_end + 1
        else:
            boundaries = [
                boundary
                for boundary in (
                    text.find(";", body_start),
                    text.find("\n", body_start),
                    text.find("\r", body_start),
                )
                if boundary >= 0
            ]
            body_end = min(boundaries) if boundaries else len(text)
        blank(match.start(), body_end)
        cursor = max(body_end, match.end())

    # A protected symbol mentioned only inside a closure that is never
    # referenced cannot affect execution. If the closure is referenced later,
    # keep it: it may be passed to an apply/map helper rather than called as
    # name(...).
    text = "".join(chars)
    closure = re.compile(
        r"(?<![A-Za-z0-9._])(?P<name>[A-Za-z.][A-Za-z0-9._]*)\s*"
        r"(?:<-|=(?!=))\s*function\s*\(",
        re.IGNORECASE,
    )
    cursor = 0
    while match := closure.search(text, cursor):
        opening = text.find("(", match.start(), match.end())
        formals_end = _r_matching_delimiter(text, opening, "(", ")")
        if formals_end < 0:
            break
        body_start = formals_end + 1
        while body_start < len(text) and text[body_start].isspace():
            body_start += 1
        if body_start < len(text) and text[body_start] == "{":
            closing = _r_matching_delimiter(text, body_start, "{", "}")
            body_end = len(text) if closing < 0 else closing + 1
        else:
            boundaries = [
                boundary
                for boundary in (
                    text.find(";", body_start),
                    text.find("\n", body_start),
                    text.find("\r", body_start),
                )
                if boundary >= 0
            ]
            body_end = min(boundaries) if boundaries else len(text)
        name = match.group("name")
        later = text[body_end:]
        if re.search(
            rf"(?<![A-Za-z0-9._]){re.escape(name)}(?![A-Za-z0-9._])",
            later,
        ) is None:
            blank(body_start, body_end)
        cursor = max(body_end, match.end())

    return "".join(chars)


def _r_role_assignments(code: str) -> list[re.Match[str]]:
    """Exclude named call arguments from statement-level assignment checks.

    A metadata list can put ``outcome = ...`` at the start of a line without
    assigning that symbol. Braced expression blocks inside calls still execute
    real assignments, and ``<-`` remains an assignment in either context.
    """

    lexical = _r_blank_string_literals(code)
    stack: list[str] = []
    cursor = 0
    result: list[re.Match[str]] = []
    for match in _ROLE_SYMBOL_ASSIGNMENT_RE.finditer(code):
        start = match.start("symbol")
        for char in lexical[cursor:start]:
            if char in "([{":
                stack.append(char)
            elif char in ")]}":
                if stack and stack[-1] == {")": "(", "]": "[", "}": "{"}[char]:
                    stack.pop()
        cursor = start
        if not lexical[start:match.end("symbol")].strip():
            continue
        if match.group("operator") == "=" and stack and stack[-1] in "([":
            continue
        result.append(match)
    return result


def _r_symbol_has_meaningful_use(
    code: str, symbol: str, *, after: int, registered_template: bool = False,
) -> bool:
    """Require an accessor-bound symbol to affect later executable code.

    This is a conservative data-flow guard for generated R.  It catches the
    common bypass where a frozen value is fetched only to satisfy the audit and
    then ignored or overwritten.  Generated code can always use a second symbol
    for transformed data rather than rebinding the protected one.
    """

    text = _r_blank_inert_regions(_r_blank_string_literals(code[after:]))
    if registered_template:
        # The fixed getter consumes its literal *name*, rather than a normal
        # R symbol token. Inspect that argument only when the complete source
        # is the registered script, and the call survives inert-code masking.
        getter = re.compile(
            rf'''(?<![A-Za-z0-9._])(?P<helper>\.(?:mt_get|wc_get|cl_opt))\s*\(\s*(["']){re.escape(symbol)}\2\s*(?:,|\))'''
        )
        for match in getter.finditer(code[after:]):
            name = match.group("helper")
            if text[match.start():match.start() + len(name)] == name:
                return True
        # The registered imputation script collects its optional columns with
        # one explicit, fixed name list and an exists/get closure. The list
        # contents are data flow here, although ordinary quoted names are not.
        collector = re.compile(
            r'''lapply\s*\(\s*c\((?P<names>[^()\r\n]+)\)\s*,\s*'''
            r'''function\(nm\)\s*if\s*\(exists\(nm\)\)\s*get\(nm\)\s*else\s*NULL\s*\)'''
        )
        for match in collector.finditer(code[after:]):
            names = _r_string_vector(f'c({match.group("names")})', {})
            if names and symbol in names and text[match.start():match.start() + len("lapply")] == "lapply":
                return True
    token = re.compile(
        rf"(?<![A-Za-z0-9._]){re.escape(symbol)}(?![A-Za-z0-9._])"
    )
    trivial = re.compile(
        rf"(?:invisible|print|message|cat|str|class|typeof|length|identity)\s*"
        rf"\(\s*{re.escape(symbol)}\s*\)\s*",
        re.IGNORECASE,
    )
    for statement in re.split(r"[;\r\n]+", text):
        candidate = statement.strip()
        if not candidate or token.search(candidate) is None:
            continue
        if trivial.fullmatch(candidate):
            continue
        if re.match(
            rf"^{re.escape(symbol)}\s*(?:<-|=(?!=))",
            candidate,
            flags=re.IGNORECASE,
        ):
            continue
        return True
    return False


def _r_direct_accessor_is_meaningful(code: str, start: int, end: int) -> bool:
    """Return whether a non-assigned accessor call participates in real code."""

    left = max(code.rfind("\n", 0, start), code.rfind(";", 0, start)) + 1
    newline = code.find("\n", end)
    semicolon = code.find(";", end)
    boundaries = [item for item in (newline, semicolon) if item >= 0]
    right = min(boundaries) if boundaries else len(code)
    statement = code[left:right].strip()
    call = code[start:end].strip()
    if statement == call:
        return False
    escaped_call = re.escape(call)
    if re.fullmatch(
        rf"(?:invisible|print|message|cat|str|class|typeof|length|identity)\s*"
        rf"\(\s*{escaped_call}\s*\)\s*",
        statement,
        flags=re.IGNORECASE,
    ):
        return False
    return True


def _r_string_vector(expression: str, symbols: Mapping[str, list[str]]) -> list[str] | None:
    """Read a literal character vector or a previously literal-bound symbol."""

    value = str(expression or "").strip()
    if value in symbols:
        return list(symbols[value])
    literal = _ROLE_LITERAL_VALUE_RE.fullmatch(value)
    if literal is not None:
        return [literal.group("value")]
    match = re.fullmatch(r"c\s*\((.*)\)\s*", value, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    body = match.group(1)
    quoted = re.findall(r'''["']([^"'\r\n]+)["']''', body)
    remainder = re.sub(r'''["'][^"'\r\n]+["']''', "", body)
    if not quoted or re.fullmatch(r"[\s,]*", remainder) is None:
        return None
    return quoted


def _r_identity_preserving_source(expression: str, *, single_index: bool = False) -> str:
    """Unwrap narrow R conversions that preserve frozen character values."""

    value = str(expression or "").strip()
    while True:
        if single_index:
            indexed = re.fullmatch(
                r"(.+?)\s*(?:\[\s*1\s*\]|\[\[\s*1\s*\]\])", value,
                flags=re.DOTALL,
            )
            if indexed is not None:
                value = indexed.group(1).strip()
                continue
        match = re.fullmatch(
            r"(?:base::)?(?:as\.character|identity)\s*\(\s*(.+)\s*\)",
            value,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            return value
        inner = match.group(1).strip()
        if not inner or inner == value:
            return value
        value = inner


def _r_registered_helper_source(expression: str) -> str:
    """Read identity getter sources from registered helpers without R.

    Callers must first verify the complete registered script's provenance.
    The getter returns an existing symbol, and the selector validates choices
    before returning the same value. Neither is a general R normalization.
    """

    source = _r_identity_preserving_source(expression)
    # A previously accessor-bound symbol necessarily exists. This fixed
    # optional normalization therefore takes its identity-preserving branch.
    optional = re.fullmatch(
        r'''if\s*\(\s*exists\(\s*(["'])(?P<name>[A-Za-z.][A-Za-z0-9._]*)\1\s*\)\s*\)'''
        r'''\s*as\.character\(\s*(?P=name)\s*\)\s*else\s*["'][^"']*["']''',
        source,
    )
    if optional:
        return optional.group("name")
    for helper in (".mt_get", ".wc_get", ".cl_opt", ".mt_select", ".sb_input"):
        prefix = re.match(rf"^{re.escape(helper)}\s*\(", source)
        if prefix is None:
            continue
        calls = _r_call_arguments(source, helper)
        if len(calls) != 1:
            return ""
        body = calls[0]
        closing_index = prefix.end() + len(body)
        if source[closing_index:] != ")":
            return ""
        # These registered call sites use positional arguments. Split only
        # top-level commas, retaining nested getter / literal-vector calls.
        parts = []
        depth = 0
        quote = ""
        escaped = False
        start = 0
        for index, character in enumerate(body):
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
                continue
            if character in {"\"", "'"}:
                quote = character
            elif character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
            elif character == "," and depth == 0:
                parts.append(body[start:index].strip())
                start = index + 1
        parts.append(body[start:].strip())
        if helper in {".mt_get", ".wc_get", ".cl_opt", ".sb_input"}:
            if len(parts) not in {1, 2}:
                return ""
            literal = _ROLE_LITERAL_VALUE_RE.fullmatch(parts[0])
            return literal.group("value") if literal else ""
        if len(parts) != 3:
            return ""
        return _r_registered_helper_source(parts[0])
    return source if re.fullmatch(r"[A-Za-z.][A-Za-z0-9._]*", source) else ""


def _r_registered_role_data_source(
    expression: str, symbols: Mapping[str, str], expected: set[str],
) -> str:
    """Prove that a registered local role holds data from its frozen column.

    The legacy role guard interprets ``outcome`` and ``strata`` as column-name
    variables. Fixed scripts also use those names for transformed observations
    or a survey formula. Only explicit column indexing / the fixed formula
    builder is accepted, and every such source must match the frozen role.
    """

    lexical = _r_blank_string_literals(expression)
    sources = re.findall(r"\[\[\s*([A-Za-z.][A-Za-z0-9._]*)\s*\]\]", lexical)
    sources.extend(re.findall(r"\.wc_rhs\(\s*([A-Za-z.][A-Za-z0-9._]*)\s*\)", lexical))
    if not sources or any(symbols.get(symbol) not in expected for symbol in sources):
        return ""
    return symbols[sources[0]]


def _is_registered_template_script(code: str, plan: Any) -> bool:
    """Verify provenance from the existing renderer and the same frozen plan.

    A helper name alone confers no trust. Even a changed comment or an added
    assignment invalidates this narrow exception and follows normal checks.
    """

    if getattr(plan, "source", "") != "template" or not getattr(plan, "template_id", ""):
        return False
    from agent.analysis.script_library import render_script

    try:
        registered = render_script(plan)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return registered is not None and str(code) == registered


def _r_assignment_preserves_symbol(
    rhs: str, symbol: str, *, registered_template: bool = False,
    single_value: bool = False,
) -> bool:
    source = _r_identity_preserving_source(rhs, single_index=single_value)
    if source.casefold() == str(symbol or "").strip().casefold():
        return True
    return registered_template and _r_registered_helper_source(source) == str(symbol or "").strip()


def _resolved_input_field(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(field, default)
    return getattr(item, field, default)


def _r_literal(value: Any) -> str:
    """Serialize a frozen, already validated plan value as inert R data."""

    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else "NA_real_"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "c(" + ", ".join(_r_literal(item) for item in value) + ")"
    if isinstance(value, Mapping):
        return "list(" + ", ".join(
            f"{json.dumps(str(key), ensure_ascii=True)} = {_r_literal(item)}"
            for key, item in value.items()
        ) + ")"
    return json.dumps(str(value), ensure_ascii=True)


def _r_chunked_character_literal(value: str, *, chunk_size: int = 512) -> str:
    """Serialize long generated R source without one oversized string token.

    R rejects very long strings containing Unicode escapes under some GUI
    locales.  Small inert literals joined by ``paste0`` preserve the exact
    source while keeping every parser token comfortably below that limit.
    """

    text = str(value or "")
    if len(text) <= chunk_size:
        return _r_literal(text)
    chunks = [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]
    return "paste0(\n    " + ",\n    ".join(_r_literal(chunk) for chunk in chunks) + "\n  )"


def _checkpoint_dir_for_frozen_plan(
    *,
    session_id: str,
    workspace_generation: str,
    dataset_id: str,
    plan: Any,
) -> Path | None:
    """Derive an internal checkpoint namespace without creating it."""
    generation = str(workspace_generation or "").strip()
    current_dataset_id = str(dataset_id or "").strip()
    plan_id = str(getattr(plan, "plan_id", "") or "").strip()
    plan_dataset_id = str(getattr(plan, "dataset_id", "") or "").strip()
    plan_generation = str(getattr(plan, "workspace_generation", "") or "").strip()
    if (
        not generation
        or not current_dataset_id
        or not plan_id
        or plan_dataset_id != current_dataset_id
        or plan_generation != generation
    ):
        return None
    try:
        safe_session_id = validate_session_id(session_id)
    except ValueError:
        return None
    if not safe_session_id:
        return None
    try:
        root = generation_root(safe_session_id, generation)
    except ValueError:
        return None
    identity = "\0".join((safe_session_id, generation, current_dataset_id, plan_id))
    namespace = sha256(identity.encode("utf-8")).hexdigest()[:32]
    return root / "checkpoints" / f"plan-{namespace}"


def _looks_like_direct_file_path(path: str) -> bool:
    value = str(path or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return False
    return any(lowered.endswith(ext) for ext in _DIRECT_FILE_EXTENSIONS) or "/" in lowered or "\\" in lowered


class BindingHelpersMixin:
    @staticmethod
    def _bool_value(value: Any, default: bool = False) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 1
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        return default

    @staticmethod
    def _blocked_platform_probe_reason(code: str) -> str:
        compact = re.sub(r"\s+", " ", str(code or "").strip().lower())
        helper_probe_patterns = (
            'exists("baa_',
            "exists('baa_",
            "args(baa_",
            "formals(baa_",
            'getanywhere("baa_',
            "getanywhere('baa_",
            'help("baa_',
            "help('baa_",
            'pattern = "baa_',
            "pattern='baa_",
            "pattern = 'baa_",
            'ls("package:.globalenv"',
            "ls('package:.globalenv'",
        )
        artifact_probe_patterns = (
            "read.csv(attr(baa_",
            "readr::read_csv(attr(baa_",
            "readlines(attr(baa_",
            "file.exists(attr(baa_",
            "read.csv(baa_path(",
            "readr::read_csv(baa_path(",
            "readlines(baa_path(",
            "file.exists(baa_path(",
            "file.info(baa_path(",
            'read.csv("/users/',
            "read.csv('/users/",
            'readr::read_csv("/users/',
            "readr::read_csv('/users/",
            'readlines("/users/',
            "readlines('/users/",
            'file.exists("/users/',
            "file.exists('/users/",
        )
        if any(pattern in compact for pattern in helper_probe_patterns):
            return (
                "Platform baa_* helpers are preloaded. Do not spend a run_r_code "
                "call inspecting helper existence or signatures; call the helper "
                "directly in the analysis code."
            )
        if any(pattern in compact for pattern in artifact_probe_patterns):
            return (
                "Each run_r_code call uses an isolated artifact directory. "
                "Saved baa_* artifacts are platform-managed; do not read saved "
                "artifact files for preview. Use artifact_previews or compute "
                "needed values directly from dataset in the analysis code."
            )
        job_escape_patterns = (
            "download.file(",
            "utils::download.file(",
            "system(",
            "system2(",
            "shell(",
            "base::system(",
            "base::system2(",
            "base::shell(",
            "sys.chmod(",
            "unlink(",
        )
        if any(pattern in compact for pattern in job_escape_patterns):
            return (
                "Analysis jobs cannot call download.file(), system(), system2(), "
                "or shell(). Write outputs with baa_table(), baa_plot(), "
                "baa_text(), or baa_model_summary() inside the job directory."
            )
        for pattern in (_DIRECT_FILE_READ_RE, _DIRECT_FILE_PROBE_RE):
            for match in pattern.finditer(compact):
                path = match.group("path")
                if _looks_like_direct_file_path(path):
                    return (
                        "Each run_r_code call uses an isolated artifact directory. "
                        f"Do not read saved artifact files by filename (`{path}`); "
                        "previous baa_* outputs are already available through "
                        "artifact_previews and the result panel. If more values are "
                        "needed, recompute them from `dataset` in the same R call and "
                        "persist outputs with baa_table(), baa_table_one(), baa_plot(), "
                        "baa_text(), or baa_model_summary()."
                    )
        return ""

    @staticmethod
    def _frozen_binding_report(code: str, plan: Any, dataset: Any) -> dict[str, Any]:
        bindings = dict(getattr(plan, "variable_bindings", {}) or {})
        resolved_inputs = dict(getattr(plan, "resolved_inputs", {}) or {})
        protected_inputs: dict[str, dict[str, Any]] = {}
        for input_id, item in resolved_inputs.items():
            kind = str(_resolved_input_field(item, "kind", "") or "")
            if kind not in {
                "dataset",
                "object",
                "column",
                "columns",
                "level",
                "parameter",
                "specification",
            }:
                continue
            raw_value = _resolved_input_field(item, "value")
            values = list(raw_value) if isinstance(raw_value, (list, tuple)) else [raw_value]
            protected_inputs[str(input_id)] = {
                "kind": kind,
                "label": str(_resolved_input_field(item, "label", input_id) or input_id),
                "value": raw_value,
                "expected": [str(value) for value in values],
            }
        if not bindings and not protected_inputs:
            return {"status": "not_applicable", "expected": {}, "observed": [], "mismatches": []}

        expected_by_family: dict[str, set[str]] = {}
        roles_by_family: dict[str, list[str]] = {}
        for role, value in bindings.items():
            role_text = str(role or "").strip()
            column = str(value or "").strip()
            if not role_text or not column:
                continue
            family = next(
                (
                    name
                    for name, pattern in _ROLE_FAMILY_PATTERNS.items()
                    if pattern.search(role_text)
                ),
                "",
            )
            if not family:
                continue
            expected_by_family.setdefault(family, set()).add(column)
            roles_by_family.setdefault(family, []).append(role_text)

        summary = (
            dataset.profile.get("summary")
            if isinstance(getattr(dataset, "profile", None), dict)
            and isinstance(dataset.profile.get("summary"), dict)
            else {}
        )
        variables = summary.get("variables") if isinstance(summary, dict) else []
        dataset_columns = {
            str(item.get("name") or "")
            for item in (variables or [])
            if isinstance(item, Mapping) and str(item.get("name") or "")
        }
        code_text = _r_without_comments(str(code or ""))
        registered_template = _is_registered_template_script(str(code or ""), plan)
        observed: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        accessor_mutation = _FROZEN_ACCESSOR_MUTATION_RE.search(code_text)
        if accessor_mutation is not None:
            mismatches.append(
                {
                    "family": "typed_input",
                    "symbol": "baa_plan_input",
                    "value": accessor_mutation.group(0).strip(),
                    "source": "accessor_mutation",
                    "expected": ["platform-managed frozen input accessor"],
                    "reason": "frozen_accessor_override",
                }
            )
        dynamic_reflection = _FROZEN_DYNAMIC_REFLECTION_RE.search(
            _r_blank_string_literals(code_text)
        )
        if dynamic_reflection is not None:
            mismatches.append(
                {
                    "family": "typed_input",
                    "symbol": dynamic_reflection.group(0),
                    "value": dynamic_reflection.group(0),
                    "source": "dynamic_reflection",
                    "expected": ["direct, statically auditable R analysis code"],
                    "reason": "frozen_accessor_reflection",
                }
            )
        resolved_symbols: dict[str, str] = {}
        vector_symbols: dict[str, list[str]] = {}
        scalar_character_symbols: set[str] = set()
        accessor_sites = [
            item
            for item in _r_plan_input_call_sites(code_text)
            if item[0] in protected_inputs
        ]
        helper_input_ids = {input_id for input_id, _start, _end in accessor_sites}
        direct_verified_inputs: set[str] = set()

        family_by_input: dict[str, str] = {}
        for input_id, item in protected_inputs.items():
            role_text = " ".join((input_id, item["label"]))
            family_by_input[input_id] = next(
                (
                    name
                    for name, pattern in _ROLE_FAMILY_PATTERNS.items()
                    if pattern.search(role_text)
                ),
                "",
            )
        for input_id in sorted(helper_input_ids):
            item = protected_inputs[input_id]
            observed.append(
                {
                    "family": family_by_input.get(input_id) or "typed_input",
                    "input_id": input_id,
                    "symbol": f'baa_plan_input("{input_id}")',
                    "value": item["value"],
                    "source": "frozen_input_accessor",
                }
            )

        def validate(family: str, symbol: str, value: str, source: str) -> None:
            if family not in expected_by_family:
                return
            item = {"family": family, "symbol": symbol, "value": value, "source": source}
            observed.append(item)
            expected = expected_by_family[family]
            if value not in expected:
                mismatches.append(
                    {
                        **item,
                        "roles": roles_by_family.get(family, []),
                        "expected": sorted(expected),
                        "dataset_column": value in dataset_columns,
                    }
                )

        role_assignments = _r_role_assignments(code_text)
        assigned_symbols = {item.group("symbol") for item in role_assignments}
        accessor_assignments: dict[str, list[tuple[str, int, int]]] = {}
        for match in role_assignments:
            symbol = match.group("symbol")
            rhs = match.group("rhs").strip()
            normalized_rhs = _r_identity_preserving_source(rhs)
            indexed_rhs = _r_identity_preserving_source(rhs, single_index=True)
            indexed_helper = _PLAN_INPUT_CALL_RE.fullmatch(indexed_rhs)
            if indexed_helper is not None:
                indexed_input = protected_inputs.get(indexed_helper.group("input_id"))
                if (
                    indexed_input and indexed_input["kind"] in {"column", "level"}
                    and len(indexed_input["expected"]) == 1
                ):
                    normalized_rhs = indexed_rhs
            elif indexed_rhs in scalar_character_symbols:
                normalized_rhs = indexed_rhs
            # A different index must not be disguised as a meaningful direct
            # accessor merely by assigning it to an arbitrary non-role name.
            # Dataset[[baa_plan_input(...)]] remains a data selection; this
            # check only covers indexing the frozen value itself.
            selection = re.fullmatch(
                r"(.+?)\s*(?:\[\[[^\r\n]*\]\]|\[[^\r\n\]]*\])",
                _r_identity_preserving_source(rhs),
            )
            selected_accessor = (
                _PLAN_INPUT_CALL_RE.fullmatch(
                    _r_identity_preserving_source(selection.group(1)),
                ) if selection else None
            )
            if selected_accessor is not None and _PLAN_INPUT_CALL_RE.fullmatch(normalized_rhs) is None:
                selected_id = selected_accessor.group("input_id")
                selected_input = protected_inputs.get(selected_id)
                if selected_input:
                    mismatches.append({
                        "family": family_by_input.get(selected_id) or "typed_input",
                        "input_id": selected_id, "symbol": symbol, "value": rhs,
                        "source": "frozen_input_dataflow",
                        "expected": selected_input["expected"],
                        "reason": "frozen_input_selection_changed",
                    })
            if registered_template:
                helper_source = _r_registered_helper_source(normalized_rhs)
                if helper_source in resolved_symbols or helper_source in vector_symbols:
                    normalized_rhs = helper_source
                elif re.match(r"^\.sb_input\s*\(", normalized_rhs):
                    # The fixed .sb_inputs list is keyed by typed input id and
                    # constructed from the corresponding accessor symbols.
                    selected = protected_inputs.get(helper_source)
                    if selected and len(selected["expected"]) == 1:
                        resolved_symbols[symbol] = selected["expected"][0]
                        normalized_rhs = symbol
            helper_match = _PLAN_INPUT_CALL_RE.fullmatch(normalized_rhs)
            if helper_match is not None:
                input_id = helper_match.group("input_id")
                protected = protected_inputs.get(input_id)
                if protected is not None:
                    accessor_assignments.setdefault(input_id, []).append(
                        (symbol, match.start(), match.end())
                    )
                    if protected["kind"] == "columns":
                        vector_symbols[symbol] = list(protected["expected"])
                    elif len(protected["expected"]) == 1:
                        resolved_symbols[symbol] = protected["expected"][0]
                        if protected["kind"] in {"column", "level"}:
                            scalar_character_symbols.add(symbol)
            source_symbol = (
                normalized_rhs
                if re.fullmatch(r"[A-Za-z.][A-Za-z0-9._]*", normalized_rhs)
                else ""
            )
            if source_symbol in resolved_symbols:
                resolved_symbols[symbol] = resolved_symbols[source_symbol]
                if source_symbol in scalar_character_symbols:
                    scalar_character_symbols.add(symbol)
            if source_symbol in vector_symbols:
                vector_symbols[symbol] = list(vector_symbols[source_symbol])
            literal_vector = _r_string_vector(normalized_rhs, vector_symbols)
            if literal_vector is not None:
                vector_symbols[symbol] = literal_vector
            family = next(
                (
                    name
                    for name, pattern in _ROLE_SYMBOL_FAMILIES.items()
                    if pattern.fullmatch(symbol)
                ),
                "",
            )
            if not family or family not in expected_by_family:
                continue
            if helper_match is not None and resolved_symbols.get(symbol):
                validate(family, symbol, resolved_symbols[symbol], "frozen_input_accessor")
                continue
            if source_symbol and resolved_symbols.get(symbol):
                validate(
                    family,
                    symbol,
                    resolved_symbols[symbol],
                    "frozen_input_normalization",
                )
                continue
            if registered_template:
                # A local outcome vector / strata formula is a use of the
                # frozen column, rather than replacement of its column name.
                derived = _r_registered_role_data_source(
                    rhs, resolved_symbols, expected_by_family[family],
                )
                if derived:
                    validate(family, symbol, derived, "registered_frozen_column_data")
                    continue
                # Optional fixed getters return NULL when the renderer has
                # not emitted that input symbol. This local name has no role
                # binding (e.g. group_col alongside a distinct risk_group).
                absent_getter = re.fullmatch(
                    r'''\.(?:mt_get|wc_get|cl_opt)\s*\(\s*(["'])([A-Za-z.][A-Za-z0-9._]*)\1\s*\)''',
                    normalized_rhs,
                )
                if absent_getter and absent_getter.group(2) not in assigned_symbols:
                    continue
            literal = _ROLE_LITERAL_VALUE_RE.fullmatch(rhs)
            if literal is None:
                item = {
                    "family": family,
                    "symbol": symbol,
                    "value": "<dynamic expression>",
                    "source": "role_assignment",
                }
                observed.append(item)
                mismatches.append(
                    {
                        **item,
                        "roles": roles_by_family.get(family, []),
                        "expected": sorted(expected_by_family[family]),
                        "reason": "dynamic_role_assignment",
                    }
                )
                continue
            value = literal.group("value")
            resolved_symbols[symbol] = value
            validate(family, symbol, value, "role_assignment")

        # Fetching a frozen value is not sufficient: it must feed executable
        # analysis code and its protected symbol must not later be overwritten.
        # The runtime audit still verifies branch execution; this preflight
        # supplies the complementary static data-flow check.
        for input_id in sorted(helper_input_ids):
            protected = protected_inputs[input_id]
            assigned_spans = accessor_assignments.get(input_id, [])
            meaningful = False
            reassigned = False
            for symbol, assignment_start, assignment_end in assigned_spans:
                later_assignments = [
                    item
                    for item in role_assignments
                    if item.start() > assignment_start
                    and item.group("symbol").casefold() == symbol.casefold()
                ]
                unsafe_assignment = next(
                    (
                        item
                        for item in later_assignments
                        if not _r_assignment_preserves_symbol(
                            item.group("rhs"), symbol,
                            registered_template=registered_template,
                            single_value=(protected["kind"] in {"column", "level"} and len(protected["expected"]) == 1),
                        )
                    ),
                    None,
                )
                if unsafe_assignment is not None:
                    reassigned = True
                    mismatches.append(
                        {
                            "family": family_by_input.get(input_id) or "typed_input",
                            "input_id": input_id,
                            "symbol": symbol,
                            "value": unsafe_assignment.group("rhs").strip(),
                            "source": "frozen_input_dataflow",
                            "expected": protected["expected"],
                            "reason": "frozen_input_reassigned",
                        }
                    )
                    continue
                use_after = (
                    later_assignments[-1].end() if later_assignments else assignment_end
                )
                if _r_symbol_has_meaningful_use(
                    code_text, symbol, after=use_after,
                    registered_template=registered_template,
                ):
                    meaningful = True

            for site_input_id, site_start, site_end in accessor_sites:
                if site_input_id != input_id:
                    continue
                inside_assignment = any(
                    start <= site_start and site_end <= end
                    for _symbol, start, end in assigned_spans
                )
                if not inside_assignment and _r_direct_accessor_is_meaningful(
                    code_text, site_start, site_end
                ):
                    meaningful = True

            if not meaningful and not reassigned:
                mismatches.append(
                    {
                        "family": family_by_input.get(input_id) or "typed_input",
                        "input_id": input_id,
                        "symbol": f'baa_plan_input("{input_id}")',
                        "value": "<accessed but not consumed>",
                        "source": "frozen_input_dataflow",
                        "expected": protected["expected"],
                        "reason": "frozen_input_not_consumed",
                    }
                )

        table_one_arguments = _r_call_arguments(code_text, "baa_table_one")
        for arguments in table_one_arguments:
            rhs = _r_named_arguments(arguments).get("by", "").strip()
            if not rhs or "group" not in expected_by_family:
                continue
            helper_match = _PLAN_INPUT_CALL_RE.fullmatch(rhs)
            if helper_match is not None:
                protected = protected_inputs.get(helper_match.group("input_id"))
                if protected is not None and len(protected["expected"]) == 1:
                    validate(
                        "group",
                        "baa_table_one(by)",
                        protected["expected"][0],
                        "frozen_input_accessor",
                    )
                    continue
            literal = _ROLE_LITERAL_VALUE_RE.fullmatch(rhs)
            if literal is not None:
                validate("group", "baa_table_one(by)", literal.group("value"), "helper_argument")
                continue
            value = resolved_symbols.get(rhs)
            if value:
                validate("group", "baa_table_one(by)", value, "helper_argument")
                continue
            item = {
                "family": "group",
                "symbol": "baa_table_one(by)",
                "value": "<dynamic expression>",
                "source": "helper_argument",
            }
            observed.append(item)
            mismatches.append(
                {
                    **item,
                    "roles": roles_by_family.get("group", []),
                    "expected": sorted(expected_by_family["group"]),
                    "reason": "dynamic_helper_binding",
                }
            )

        # Table One freezes not only `by`, but the complete baseline-variable
        # sets and their continuous/categorical interpretation.  Literal code
        # is supported for backwards compatibility; new code should use the
        # trusted accessor above.
        set_inputs = {
            input_id: item
            for input_id, item in protected_inputs.items()
            if item["kind"] == "columns"
            and (
                "continuous" in input_id.casefold()
                or "categor" in input_id.casefold()
                or "连续" in item["label"]
                or "分类" in item["label"]
            )
        }
        for arguments in table_one_arguments:
            named = _r_named_arguments(arguments)
            actual_vars = _r_string_vector(named.get("vars", ""), vector_symbols)
            if actual_vars is None or not set_inputs:
                continue
            expected_union = {
                value for item in set_inputs.values() for value in item["expected"]
            }
            actual_set = set(actual_vars)
            if actual_set != expected_union:
                for input_id, item in set_inputs.items():
                    if input_id in helper_input_ids:
                        continue
                    mismatches.append(
                        {
                            "family": "typed_input",
                            "input_id": input_id,
                            "symbol": "baa_table_one(vars)",
                            "value": sorted(actual_set),
                            "source": "helper_argument",
                            "expected": item["expected"],
                            "reason": "frozen_column_set_mismatch",
                            "unexpected": sorted(actual_set - expected_union),
                            "missing": sorted(set(item["expected"]) - actual_set),
                        }
                    )
                continue
            for input_id, item in set_inputs.items():
                if input_id in helper_input_ids:
                    continue
                argument_name = (
                    "continuous"
                    if "continuous" in input_id.casefold() or "连续" in item["label"]
                    else "categorical"
                )
                actual_typed = _r_string_vector(named.get(argument_name, ""), vector_symbols)
                if actual_typed is not None and set(actual_typed) == set(item["expected"]):
                    direct_verified_inputs.add(input_id)
                elif actual_typed is not None:
                    mismatches.append(
                        {
                            "family": "typed_input",
                            "input_id": input_id,
                            "symbol": f"baa_table_one({argument_name})",
                            "value": actual_typed,
                            "source": "helper_argument",
                            "expected": item["expected"],
                            "reason": "frozen_type_set_mismatch",
                        }
                    )
        observed_families = {item["family"] for item in observed}
        observed_families.update(
            family_by_input[input_id]
            for input_id in helper_input_ids
            if family_by_input.get(input_id)
        )
        missing_families = sorted(set(expected_by_family) - observed_families)
        missing_inputs = sorted(
            set(protected_inputs) - helper_input_ids - direct_verified_inputs
        )
        expected_payload = {key: sorted(values) for key, values in expected_by_family.items()}
        expected_payload.update(
            {f"input:{input_id}": item["expected"] for input_id, item in protected_inputs.items()}
        )
        return {
            "status": (
                "not_applicable"
                if not expected_by_family and not protected_inputs
                else "failed"
                if mismatches
                else "unverified"
                if missing_families or missing_inputs
                else "passed"
            ),
            "expected": expected_payload,
            "observed": observed,
            "mismatches": mismatches,
            "missing_families": missing_families,
            "missing_inputs": missing_inputs,
            "scope": "complete frozen typed inputs via baa_plan_input(), plus verified legacy literals",
        }

    @staticmethod
    def _frozen_input_prelude(plan: Any) -> str:
        resolved_inputs = dict(getattr(plan, "resolved_inputs", {}) or {})
        if not resolved_inputs:
            return ""
        entries = ",\n  ".join(
            (
                f"{json.dumps(str(input_id), ensure_ascii=True)} = dataset"
                if str(_resolved_input_field(item, "kind", "")) in {"dataset", "object"}
                and str(_resolved_input_field(item, "value", ""))
                == str(getattr(plan, "dataset_id", "") or "")
                else (
                    f"{json.dumps(str(input_id), ensure_ascii=True)} = "
                    f"{_r_literal(_resolved_input_field(item, 'value'))}"
                )
            )
            for input_id, item in resolved_inputs.items()
        )
        return f"""
# Trusted values compiled from the active frozen analysis plan. The accessor
# and its runtime audit share a private closure and their global bindings are
# locked before model-generated code is evaluated.
.baa_frozen_accessors <- local({{
  frozen_inputs <- list(
    {entries}
  )
  accessed_inputs <- new.env(parent = emptyenv())
  getter <- function(input_id) {{
    input_id <- as.character(input_id)[1]
    if (!nzchar(input_id) || !(input_id %in% names(frozen_inputs))) {{
      stop(paste0("Unknown frozen analysis input: ", input_id), call. = FALSE)
    }}
    assign(input_id, TRUE, envir = accessed_inputs)
    frozen_inputs[[input_id]]
  }}
  assertion <- function(required_inputs) {{
    missing_inputs <- setdiff(
      required_inputs,
      ls(envir = accessed_inputs, all.names = TRUE)
    )
    if (length(missing_inputs)) {{
      stop(
        paste0(
          "BAA_FROZEN_INPUT_AUDIT: required inputs were not accessed at runtime: ",
          paste(missing_inputs, collapse = ", ")
        ),
        call. = FALSE
      )
    }}
    invisible(TRUE)
  }}
  # Lock the closure bindings themselves.  The access-log environment remains
  # internally mutable, but generated code is statically forbidden from
  # reflecting into either accessor environment.
  lockEnvironment(environment(getter), bindings = TRUE)
  list(get = getter, assert_used = assertion)
}})
baa_plan_input <- .baa_frozen_accessors$get
.baa_assert_frozen_inputs_used <- .baa_frozen_accessors$assert_used
rm(.baa_frozen_accessors)
lockBinding("baa_plan_input", .GlobalEnv)
lockBinding(".baa_assert_frozen_inputs_used", .GlobalEnv)
"""

    @staticmethod
    def _frozen_input_epilogue(plan: Any) -> str:
        resolved_inputs = dict(getattr(plan, "resolved_inputs", {}) or {})
        required_ids = tuple(
            str(input_id)
            for input_id, item in resolved_inputs.items()
            if str(_resolved_input_field(item, "kind", ""))
            in {
                "dataset",
                "object",
                "column",
                "columns",
                "level",
                "parameter",
                "specification",
            }
        )
        return (
            f".baa_assert_frozen_inputs_used({_r_literal(required_ids)})"
            if required_ids
            else ""
        )

    @staticmethod
    def _frozen_user_code_block(code: str, plan: Any) -> str:
        """Parse and evaluate generated R only after a trusted AST safety pass.

        The runtime accessor audit lives in a closure. R reflection can mutate
        such a closure if model-generated code is evaluated without a boundary,
        so protected-plan code may not use dynamic lookup/evaluation primitives
        or reflect on the frozen accessors. Ordinary calls such as
        ``environment(stats::lm)`` remain valid because they do not target a
        protected symbol.
        """

        resolved_inputs = dict(getattr(plan, "resolved_inputs", {}) or {})
        protected = any(
            str(_resolved_input_field(item, "kind", ""))
            in {
                "dataset",
                "object",
                "column",
                "columns",
                "level",
                "parameter",
                "specification",
            }
            for item in resolved_inputs.values()
        )
        if not protected:
            return str(code or "")
        return f"""
local({{
  .baa_user_code <- {_r_chunked_character_literal(str(code or ""))}
  .baa_user_expr <- parse(text = .baa_user_code, keep.source = FALSE)
  .baa_protected_symbols <- c(
    "baa_plan_input", ".baa_assert_frozen_inputs_used"
  )
  .baa_forbidden_symbols <- c(".GlobalEnv", ".BaseNamespaceEnv")
  .baa_dynamic_calls <- c(
    "baseenv", "globalenv", "parent.frame", "sys.frame", "sys.frames",
    "sys.function", "as.environment", "pos.to.env", "unlockBinding",
    "makeActiveBinding", "eval", "evalq", "eval.parent",
    "parse", "source", "sys.source", "getNamespace",
    "asNamespace", "getFromNamespace", "getNamespaceInfo",
    "getExportedValue", "global_env", "base_env", "caller_env",
    "current_env", "env_get", "env_poke", "env_bind", "env_unlock"
  )
  .baa_reflection_calls <- c(
    "environment", "parent.env", "body", "formals", "bindingIsLocked",
    "bindingIsActive", "trace", "untrace", "debug", "debugonce"
  )
  .baa_indirect_calls <- c(
    "get", "get0", "mget", "dynGet", "assign", "delayedAssign",
    "do.call", "match.fun"
  )
  .baa_sensitive_literals <- c(
    .baa_protected_symbols, "accessed_inputs", "frozen_inputs",
    .baa_dynamic_calls, .baa_reflection_calls
  )
  .baa_contains_protected <- function(node) {{
    if (is.symbol(node)) {{
      return(as.character(node) %in% .baa_protected_symbols)
    }}
    if (is.null(node) || is.atomic(node)) return(FALSE)
    any(vapply(as.list(node), .baa_contains_protected, logical(1)))
  }}
  .baa_contains_sensitive_literal <- function(node) {{
    if (is.character(node)) {{
      return(any(node %in% .baa_sensitive_literals))
    }}
    if (is.symbol(node)) return(FALSE)
    if (is.null(node) || is.atomic(node)) return(FALSE)
    any(vapply(as.list(node), .baa_contains_sensitive_literal, logical(1)))
  }}
  .baa_call_name <- function(node) {{
    if (!is.call(node) || !length(node)) return("")
    head <- node[[1L]]
    if (is.symbol(head)) return(as.character(head))
    if (is.call(head) && length(head) >= 3L &&
        as.character(head[[1L]]) %in% c("::", ":::")) {{
      return(as.character(head[[3L]]))
    }}
    ""
  }}
  .baa_ast_error <- function(detail) {{
    stop(
      paste0("BAA_FROZEN_INPUT_AST: ", detail),
      call. = FALSE
    )
  }}
  .baa_walk <- function(node, callable_head = FALSE) {{
    if (missing(node)) return(invisible(TRUE))
    if (is.symbol(node)) {{
      name <- as.character(node)
      if (name %in% .baa_forbidden_symbols) {{
        .baa_ast_error("global/base environment reflection is not allowed")
      }}
      return(invisible(TRUE))
    }}
    if (is.null(node) || is.atomic(node)) return(invisible(TRUE))
    if (is.expression(node) || is.pairlist(node)) {{
      for (item in as.list(node)) .baa_walk(item, FALSE)
      return(invisible(TRUE))
    }}
    if (!is.call(node)) return(invisible(TRUE))
    operator <- if (is.symbol(node[[1L]])) as.character(node[[1L]]) else ""
    call_name <- .baa_call_name(node)
    if (call_name %in% .baa_dynamic_calls) {{
      .baa_ast_error(paste0(
        "dynamic call is not allowed: ", call_name,
        " —— 受保护计划禁止 eval/parse/do.call/environment 等动态调用；",
        "请写出显式函数调用（如 rbind(a, b) 而非 do.call(rbind, list(a, b))），",
        "数据与参数一律通过 baa_plan_input() 获取。"
      ))
    }}
    if (call_name %in% .baa_indirect_calls &&
        (.baa_contains_protected(node) || .baa_contains_sensitive_literal(node))) {{
      .baa_ast_error(paste0("indirect protected lookup is not allowed: ", call_name))
    }}
    if (operator %in% c("::", ":::") && length(node) >= 3L) {{
      target <- as.character(node[[3L]])
      if (target %in% .baa_dynamic_calls ||
          (!callable_head && target %in% .baa_reflection_calls)) {{
        .baa_ast_error(paste0("dynamic function lookup is not allowed: ", target))
      }}
    }}
    assignment_target <- if (operator %in% c("->", "->>")) 3L else 2L
    if (operator %in% c("<-", "=", "<<-", "->", "->>") &&
        length(node) >= assignment_target &&
        .baa_contains_protected(node[[assignment_target]])) {{
      .baa_ast_error("protected accessor assignment is not allowed")
    }}
    if (call_name %in% .baa_reflection_calls && length(node) >= 2L &&
        any(vapply(as.list(node)[-1L], .baa_contains_protected, logical(1)))) {{
      .baa_ast_error("reflection on a protected accessor is not allowed")
    }}
    head <- node[[1L]]
    if (is.call(head)) .baa_walk(head, TRUE)
    if (length(node) >= 2L) {{
      for (item in as.list(node)[-1L]) .baa_walk(item, FALSE)
    }}
    invisible(TRUE)
  }}
  .baa_walk(.baa_user_expr)
  eval(.baa_user_expr, envir = .GlobalEnv)
}})
"""

    @staticmethod
    def _worker_compatibility_prelude() -> str:
        return r"""
# Compatibility shim for older worker helper bundles. New helpers already
# support metadata; older helpers fail with "unused argument (metadata=...)".
.baa_wrap_metadata <- function(fn_name) {
  if (!exists(fn_name, mode = "function", inherits = TRUE)) {
    return(invisible(FALSE))
  }
  fn <- get(fn_name, mode = "function", inherits = TRUE)
  if ("metadata" %in% names(formals(fn))) {
    return(invisible(TRUE))
  }
  orig_name <- paste0(".baa_original_", fn_name)
  assign(orig_name, fn, envir = .GlobalEnv)
  assign(
    fn_name,
    eval(substitute(
      function(..., metadata = list()) original(...),
      list(original = as.name(orig_name))
    )),
    envir = .GlobalEnv
  )
  invisible(TRUE)
}
for (.baa_fn in c("baa_table", "baa_plot", "baa_text", "baa_model_summary", "baa_table_one")) {
  .baa_wrap_metadata(.baa_fn)
}
rm(.baa_fn, .baa_wrap_metadata)
"""
