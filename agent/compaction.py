# -*- coding: utf-8 -*-
"""
Conversation history compaction — LLM-based semantic summarization.

Inspired by Claude Code's compact.ts / prompt.ts:
  - When the *real* prompt-token usage of the previous turn exceeds a fraction
    of the model context window, summarize the oldest portion of history via a
    lightweight LLM call, keeping the most-recent turns verbatim.
  - The summary is injected as a single system message so the agent retains
    full semantic context without bloating the prompt.
  - Images and large tool results are stripped before summarization to keep
    the compaction request itself small.

Trigger口径与前端上下文条一致:
  前端显示 prompt_tokens / context_window；compaction 用上一轮真实
  prompt_tokens 判定，达到 _COMPACT_TRIGGER_RATIO (80%) 即触发。

Usage (in agent.run):
    if should_compact_history(history, last_prompt_tokens, ctx_window):
        yield {"type": "tool_start", "tool": "compaction", ...}
        history, ok = compact_history(history, client, model, summary_model=...)
        yield {"type": "tool_end", "tool": "compaction"}
"""

import hashlib
import json
import logging
import time
from typing import List, Dict, Any, Tuple, Optional

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

# Trigger compaction when the previous turn's real prompt tokens reach this
# fraction of the context window. Lower = trigger earlier, keeping history leaner.
_COMPACT_TRIGGER_RATIO = 0.70  # was 0.80 — trigger earlier before history gets huge

# Rule-based trim kicks in at this lower ratio, BEFORE semantic compaction.
# In the 60–70% zone we just drop oversized tool results without an LLM call.
_TRIM_TRIGGER_RATIO = 0.60

# Tool result messages longer than this are truncated during rule-based trim.
_TRIM_TOOL_RESULT_CAP = 2000  # chars

# Keep this many recent turns verbatim (never summarized).
# Increased so more recent analysis context survives intact.
_KEEP_RECENT_TURNS = 8  # was 6

# Hard cap on chars fed to the summarizer.
# Reduced so the compaction prompt focuses on conclusions, not raw data dumps.
_MAX_SUMMARY_INPUT_CHARS = 24_000  # was 40_000

# Max tokens the summary itself may use.
# Increased to allow richer retention of key numbers and findings.
_SUMMARY_MAX_TOKENS = 2500  # was 1200

# Minimum history messages before compaction is worthwhile.
_MIN_TURNS_FOR_COMPACT = 4

# Marker used to tag the injected summary message so downstream pruning logic
# can recognise and protect it.
COMPACTION_SUMMARY_MARKER = "[CONVERSATION SUMMARY — earlier context compressed]"

# ── Summarization prompt (adapted from Claude Code prompt.ts) ─────────────────

_COMPACT_SYSTEM = (
    "You are a helpful assistant. Your only task is to produce a concise structured "
    "summary of a conversation. Output ONLY the summary — no preamble, no commentary."
)

_COMPACT_PROMPT_TEMPLATE = """\
Below is a segment of a research-statistics conversation that needs to be summarized.
The user is working with an AI research assistant that executes statistical methods in R and records
tables, figures, diagnostics, and publication-style interpretations.

CRITICAL INSTRUCTIONS:
- PRESERVE ALL SPECIFIC NUMBERS: sample sizes, estimates, confidence intervals, p-values, effect sizes,
  missingness rates, dates, thresholds, and category counts.
- PRESERVE COLUMN NAMES, dataset names, outcome/exposure/group mappings, and factor reference levels exactly.
- Distinguish executed findings from proposed analyses. Never turn a plan or assumption into a result.
- Preserve statistical methods, adjustment variables, diagnostics, limitations, and unresolved decisions.

<conversation_to_summarize>
{conversation_text}
</conversation_to_summarize>

Write a structured summary using the exact headings below.
Omit a section only if there is truly nothing to report.

## 1. User Goals
What the user explicitly asked for, including study question, population, outcome, exposure, groups, and time range.

## 2. Data & Schema
Datasets used, exact column names, row counts, variable types, missingness, exclusions, and date ranges.

## 3. Executed Statistical Results
For each analysis actually run, preserve the method, model specification, sample size, exact estimates,
uncertainty measures, diagnostic findings, warnings, and whether the result was primary or exploratory.

## 4. Tables & Figures
Artifacts generated, their scientific role, included variables, axes, captions, and key values.

## 5. Conclusions & Insights
Scientific conclusions supported by executed results. Preserve caveats and avoid causal language unless justified.

## 6. Outputs Produced
Research tables, figures, text notes, and delivery packages created. Include artifact names.

## 7. Errors & Fixes
Errors encountered and how they were resolved.

## 8. Pending / Next Steps
Tasks requested but NOT yet completed. What should happen next.

## 9. Current State
Exactly where the conversation left off — last action taken, what the user asked most recently.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _strip_heavy_content(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of msg with images stripped and large content truncated.

    Tool results are aggressively truncated — the summarizer only needs to
    know *what* was queried and *key values*, not the full raw data table.
    """
    # Tight cap for tool results: keep enough to show key numbers/structure,
    # but drop the bulk of raw data rows that add noise to the summary.
    _TOOL_RESULT_CAP = 600  # chars — ~170 tokens per tool result
    _TEXT_CAP = 2000  # chars for regular assistant/user messages

    content = msg.get("content")
    if content is None:
        return msg

    # role=tool: always apply the tight cap
    if msg.get("role") == "tool":
        if isinstance(content, str) and len(content) > _TOOL_RESULT_CAP:
            from agent.tools.results import truncate_tool_result_preserving_refs

            content = truncate_tool_result_preserving_refs(content, _TOOL_RESULT_CAP)
        return {**msg, "content": content}

    # String content (assistant / user text)
    if isinstance(content, str):
        if len(content) > _TEXT_CAP:
            content = content[:_TEXT_CAP] + "\n…[truncated]"
        return {**msg, "content": content}

    # List content (multimodal)
    if isinstance(content, list):
        from agent.vision import strip_images_for_compaction

        content = strip_images_for_compaction(content)
        cleaned = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if len(text) > _TEXT_CAP:
                    text = text[:_TEXT_CAP] + "\n…[truncated]"
                cleaned.append({**block, "text": text})
            else:
                cleaned.append(block)
        return {**msg, "content": cleaned}

    return msg


def _messages_to_text(messages: List[Dict]) -> str:
    """Render messages to a readable text block for the summarizer."""
    parts = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content") or ""

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    text_parts.append(f"[tool_result: {str(block)[:200]}]")
            content = " ".join(text_parts)

        if role == "assistant" and m.get("tool_calls"):
            tool_names = [tc.get("function", {}).get("name", "?") for tc in m.get("tool_calls", [])]
            content = f"{content} [calls: {', '.join(tool_names)}]".strip()

        if role == "tool":
            # Preserve a parseable scientific envelope after the structured
            # trim. Another character prefix would cut the CSV/diagnostic and
            # defeat later sharing-policy checks.
            from agent.tools.results import truncate_tool_result_preserving_refs

            text = str(content)
            try:
                body = text.split("\n", 1)[1] if text.startswith("[TOOL_RESULT]") and "\n" in text else text
                envelope = json.loads(body)
            except (TypeError, ValueError):
                envelope = None
            if isinstance(envelope, dict) and envelope.get("tool") in {"run_r_code", "inspect_dataset"}:
                text = truncate_tool_result_preserving_refs(text, 500)
            else:
                text = text[:500]
            content = f"[tool_result] {text}"

        if isinstance(content, str) and content.strip():
            parts.append(f"[{role.upper()}]: {content.strip()}")

    return "\n\n".join(parts)


def _safe_tail_start(history: List[Dict], desired_keep: int) -> int:
    """Return the index where the verbatim tail should start.

    Adjusts `len(history) - desired_keep` so the tail never begins with an
    orphan `role: tool` message — OpenAI requires every tool message to
    immediately follow the assistant message that contains its tool_calls.
    Walks the cut point earlier until it lands on a non-tool message.
    """
    if desired_keep <= 0:
        return len(history)
    idx = max(0, len(history) - desired_keep)
    # If the tail would start with a tool message, move the cut earlier so the
    # preceding assistant(tool_calls) message is kept together with it.
    while idx > 0 and history[idx].get("role") == "tool":
        idx -= 1
    return idx


# ── Core compaction logic ─────────────────────────────────────────────────────


def _call_summarizer(
    client,
    summary_model: str,
    prompt: str,
) -> str:
    """Call the LLM with a fully-built summarization prompt. Returns summary or raises."""
    from infrastructure.llm_transport import default_llm_timeout
    from .retry import call_with_retry

    # Same transport policy as the research loop: without an explicit timeout
    # a wedged provider call would stall the whole turn before it even starts.
    response = call_with_retry(
        client.chat.completions.create,
        model=summary_model,
        messages=[
            {"role": "system", "content": _COMPACT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=_SUMMARY_MAX_TOKENS,
        stream=False,
        timeout=default_llm_timeout(),
    )
    return response.choices[0].message.content or ""


def compact_history(
    history: List[Dict],
    client,
    model: str,
    summary_model: Optional[str] = None,
) -> Tuple[List[Dict], bool]:
    """
    Summarize the oldest portion of history, keeping the most recent turns verbatim.

    Args:
        history:       full conversation history (list of message dicts)
        client:        the LLM client (same one the session uses)
        model:         the session model — used as the summarizer model when
                       `summary_model` is not given (guarantees a valid model
                       for the active provider/endpoint).
        summary_model: optional explicit model id for the summary call. If the
                       caller does not provide one, `model` is used as-is so we
                       never request a model the provider does not host.

    Returns:
        (new_history, did_compact)
        new_history[0] is a system message containing the summary if compacted.
    """
    if len(history) < _MIN_TURNS_FOR_COMPACT:
        return history, False

    # Split: summarize the head, keep a tool-call-safe verbatim tail.
    desired_keep = min(_KEEP_RECENT_TURNS, len(history) // 2)
    tail_start = _safe_tail_start(history, desired_keep)
    to_summarize = history[:tail_start]
    to_keep = history[tail_start:]

    if not to_summarize:
        return history, False

    # Strip heavy content and build text
    stripped = [_strip_heavy_content(m) for m in to_summarize]
    conversation_text = _messages_to_text(stripped)

    # Truncate input if still too large
    if len(conversation_text) > _MAX_SUMMARY_INPUT_CHARS:
        conversation_text = conversation_text[:_MAX_SUMMARY_INPUT_CHARS] + "\n…[earlier context truncated]"

    # Use the session model for summarization unless the caller supplied a
    # lighter one. Never hard-code a model id — a custom provider/endpoint may
    # not host it, which would 404 the whole compaction.
    use_model = summary_model or model

    t0 = time.monotonic()
    try:
        summary = _call_summarizer(
            client, use_model, _COMPACT_PROMPT_TEMPLATE.format(conversation_text=conversation_text)
        )
    except Exception as exc:
        log.warning("[compaction] summarization failed: %s — keeping history as-is", exc)
        return history, False

    if not summary.strip():
        log.warning("[compaction] summarizer returned empty output — keeping history as-is")
        return history, False

    elapsed = time.monotonic() - t0
    log.info(
        "[compaction] summarized %d→1 messages in %.1fs (kept %d recent turns)",
        len(to_summarize),
        elapsed,
        len(to_keep),
    )

    return [_summary_message(summary)] + to_keep, True


# ── Cross-turn rolling compaction ────────────────────────────────────────────
#
# ``compact_history`` only rewrites the caller's working copy, so without
# persistence every turn re-summarizes the same growing prefix (an LLM call
# per turn once the raw history crosses the trigger).  The session therefore
# stores the last summary as a small state record:
#
#     {"summary": <text>, "covered": <int>, "boundary": <fingerprint>}
#
# where ``covered`` is how many leading history messages the summary replaces
# and ``boundary`` is a cheap fingerprint of ``history[covered - 1]`` that
# invalidates the record if the history was replaced rather than appended to
# (clear / restore / rewind).  ``reuse_compaction_view`` rebuilds the working
# view from a stored record with zero LLM cost; ``compact_history_rolling``
# merges the previous summary with the newly uncovered segment when reuse is
# no longer enough.

# When reusing a stored summary, require the rebuilt view to fit comfortably
# below the compaction trigger so we don't ping-pong around the threshold.
_REUSE_HEADROOM_RATIO = 0.85


def _summary_message(summary_text: str) -> Dict[str, Any]:
    return {
        "role": "system",
        # Tagged with COMPACTION_SUMMARY_MARKER so _hard_prune can protect it.
        "content": (
            COMPACTION_SUMMARY_MARKER
            + "\n\n"
            + summary_text.strip()
            + "\n\n[End of summary. Continue from the current state described above.]"
        ),
        "_compaction_summary": True,
    }


def _boundary_fingerprint(message: Optional[Dict[str, Any]]) -> str:
    """Cheap identity of a history message for compaction-state validation."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        try:
            rendered = json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            rendered = str(content)
    else:
        rendered = str(content or "")
    raw = f"{message.get('role', '')}|{len(rendered)}|{rendered[:200]}"
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()


def _compaction_state_valid(state: Any, history: List[Dict]) -> bool:
    """True when a stored compaction record still describes this history."""
    if not isinstance(state, dict):
        return False
    covered = state.get("covered")
    if not isinstance(covered, int) or isinstance(covered, bool):
        return False
    if covered <= 0 or covered >= len(history):
        return False
    if not str(state.get("summary") or "").strip():
        return False
    return bool(state.get("boundary")) and state["boundary"] == _boundary_fingerprint(
        history[covered - 1]
    )


def reuse_compaction_view(
    history: List[Dict], state: Any
) -> Optional[List[Dict]]:
    """Rebuild the compacted working view from a stored record — no LLM call.

    Returns ``[summary] + history[start:]`` or ``None`` when the record is
    missing/invalid.  ``start`` walks back over orphan tool results so the
    view never opens with a ``role: tool`` message whose assistant
    ``tool_calls`` were already summarized away.
    """
    if not _compaction_state_valid(state, history):
        return None
    start = int(state["covered"])
    while start > 0 and history[start].get("role") == "tool":
        start -= 1
    return [_summary_message(str(state["summary"]))] + list(history[start:])


def reuse_view_is_small_enough(
    view: List[Dict], context_window: int, chars_per_token: float = 3.5
) -> bool:
    """True when the reused view fits comfortably below the trigger."""
    if not context_window or context_window <= 0:
        return False
    est = _estimate_history_tokens(view, chars_per_token)
    return est < context_window * _COMPACT_TRIGGER_RATIO * _REUSE_HEADROOM_RATIO


def _build_roll_prompt(previous_summary: str, segment_text: str) -> str:
    prelude = (
        "A conversation was previously summarized. Below is that previous summary, "
        "followed by a newer segment it does not cover. Produce ONE updated merged "
        "summary covering both.\n\n"
        f"<previous_summary>\n{previous_summary}\n</previous_summary>\n\n"
    )
    return prelude + _COMPACT_PROMPT_TEMPLATE.format(conversation_text=segment_text)


def compact_history_rolling(
    history: List[Dict],
    client,
    model: str,
    prior_state: Optional[Dict[str, Any]] = None,
    summary_model: Optional[str] = None,
) -> Tuple[List[Dict], bool, Optional[Dict[str, Any]]]:
    """Summarize the uncovered prefix of history, merging any stored summary.

    Works like :func:`compact_history` but understands a persisted prior
    record: only messages after ``prior_state["covered"]`` are sent to the
    summarizer, the previous summary is folded in, and the returned state
    covers the raw history prefix up to the new verbatim tail.
    """
    if len(history) < _MIN_TURNS_FOR_COMPACT:
        return history, False, None

    prior = prior_state if _compaction_state_valid(prior_state, history) else None
    covered = int(prior["covered"]) if prior else 0
    previous_summary = str(prior["summary"]) if prior else ""

    base = history[covered:]
    if len(base) < _MIN_TURNS_FOR_COMPACT and not previous_summary:
        return history, False, None

    desired_keep = min(_KEEP_RECENT_TURNS, len(base) // 2)
    tail_start = _safe_tail_start(base, desired_keep)
    to_summarize = base[:tail_start]
    to_keep = base[tail_start:]

    if not to_summarize:
        return history, False, prior

    stripped = [_strip_heavy_content(m) for m in to_summarize]
    segment_text = _messages_to_text(stripped)
    if len(segment_text) > _MAX_SUMMARY_INPUT_CHARS:
        segment_text = segment_text[:_MAX_SUMMARY_INPUT_CHARS] + "\n…[earlier context truncated]"

    use_model = summary_model or model
    prompt = (
        _build_roll_prompt(previous_summary, segment_text)
        if previous_summary
        else _COMPACT_PROMPT_TEMPLATE.format(conversation_text=segment_text)
    )

    t0 = time.monotonic()
    try:
        summary = _call_summarizer(client, use_model, prompt)
    except Exception as exc:
        log.warning("[compaction] summarization failed: %s — keeping history as-is", exc)
        return history, False, prior

    if not summary.strip():
        log.warning("[compaction] summarizer returned empty output — keeping history as-is")
        return history, False, prior

    new_covered = covered + tail_start
    state: Dict[str, Any] = {
        "summary": summary.strip(),
        "covered": new_covered,
        "boundary": _boundary_fingerprint(history[new_covered - 1]),
    }
    log.info(
        "[compaction] %s summary covering %d/%d messages in %.1fs (kept %d recent turns)",
        "rolled" if previous_summary else "created",
        new_covered,
        len(history),
        time.monotonic() - t0,
        len(to_keep),
    )
    return [_summary_message(summary)] + to_keep, True, state


def _estimate_history_tokens(history: List[Dict], chars_per_token: float = 3.5) -> int:
    """Rough token estimate of the current history (chars / chars_per_token)."""
    import json as _json

    total_chars = 0
    for m in history:
        try:
            total_chars += len(_json.dumps(m, ensure_ascii=False))
        except Exception:
            total_chars += len(str(m))
    return max(1, int(total_chars / chars_per_token))


def trim_oversized_tool_results(history: List[Dict]) -> Tuple[List[Dict], int]:
    """Rule-based trim: truncate tool result messages that exceed _TRIM_TOOL_RESULT_CAP.

    This is a cheap, zero-LLM operation that runs in the 60–70% context zone
    BEFORE semantic compaction is considered.  It only shrinks bulky tool
    results (large query outputs, profile text) and leaves all other messages
    intact, so the agent retains full structural context.

    Returns:
        (trimmed_history, n_trimmed) — n_trimmed is the number of messages that
        were actually shortened (useful for logging).
    """
    trimmed = []
    n_trimmed = 0
    for msg in history:
        if msg.get("role") == "tool":
            raw = msg.get("content", "")
            if isinstance(raw, str) and len(raw) > _TRIM_TOOL_RESULT_CAP:
                from agent.tools.results import truncate_tool_result_preserving_refs

                msg = {
                    **msg,
                    "content": truncate_tool_result_preserving_refs(
                        raw,
                        _TRIM_TOOL_RESULT_CAP,
                    ),
                }
                n_trimmed += 1
        trimmed.append(msg)
    return trimmed, n_trimmed


def should_trim_history(
    history: List[Dict],
    last_prompt_tokens: int,
    context_window: int,
    chars_per_token: float = 3.5,
) -> bool:
    """Return True when the context is in the 60–70% zone (trim range).

    Used to gate trim_oversized_tool_results() — avoids unnecessary iteration
    when the context is comfortably below the trim threshold.
    """
    if len(history) < _MIN_TURNS_FOR_COMPACT:
        return False
    if not context_window or context_window <= 0:
        return False

    lo = context_window * _TRIM_TRIGGER_RATIO
    hi = context_window * _COMPACT_TRIGGER_RATIO

    # Signal 1: real token usage from previous turn
    if last_prompt_tokens:
        if lo <= last_prompt_tokens < hi:
            return True

    # Signal 2: estimated history size
    est = _estimate_history_tokens(history, chars_per_token)
    return lo <= est < hi


def should_compact_history(
    history: List[Dict],
    last_prompt_tokens: int,
    context_window: int,
    chars_per_token: float = 3.5,
) -> bool:
    """
    Decide whether to run semantic compaction.

    Triggers on EITHER of two signals reaching _COMPACT_TRIGGER_RATIO (80%) of
    the context window:

      1. last_prompt_tokens — the real prompt-token count the LLM reported on
         the previous turn (same measure the frontend context bar shows).
      2. an estimate of the CURRENT history size — covers the case where the
         previous turn stuffed huge tool results into history, or where usage
         data is missing (e.g. right after a server restart).

    Using the current-history estimate as a second signal means compaction is
    not blocked just because `last_prompt_tokens` happens to be 0/stale.

    Returns False when there is not enough history to bother, when the window
    is unknown, or when both signals are still below the threshold.
    """
    if len(history) < _MIN_TURNS_FOR_COMPACT:
        return False
    if not context_window or context_window <= 0:
        return False

    threshold = context_window * _COMPACT_TRIGGER_RATIO

    # Signal 1: real usage from the previous turn.
    if last_prompt_tokens and last_prompt_tokens >= threshold:
        return True

    # Signal 2: estimated size of the history we are about to send.
    est = _estimate_history_tokens(history, chars_per_token)
    return est >= threshold
