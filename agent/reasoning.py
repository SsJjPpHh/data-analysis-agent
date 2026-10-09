"""Helpers for separating provider-emitted ``💭`` blocks from answers."""

import logging

log = logging.getLogger(__name__)


class ThinkTagStreamParser:
    """Split ``<think>...</think>`` blocks even when tags span stream chunks."""

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    @staticmethod
    def _partial_tag_length(text: str, tag: str) -> int:
        lower = text.lower()
        tag = tag.lower()
        for size in range(min(len(lower), len(tag) - 1), 0, -1):
            if lower.endswith(tag[:size]):
                return size
        return 0

    def feed(self, chunk: str) -> tuple[str, str]:
        self._buffer += chunk or ""
        visible: list[str] = []
        reasoning: list[str] = []

        while self._buffer:
            tag = self._CLOSE if self._in_think else self._OPEN
            index = self._buffer.lower().find(tag)
            target = reasoning if self._in_think else visible

            if index >= 0:
                target.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(tag) :]
                self._in_think = not self._in_think
                continue

            keep = self._partial_tag_length(self._buffer, tag)
            if keep:
                target.append(self._buffer[:-keep])
                self._buffer = self._buffer[-keep:]
            else:
                target.append(self._buffer)
                self._buffer = ""
            break

        return "".join(visible), "".join(reasoning)

    def finish(self) -> tuple[str, str]:
        if self._in_think:
            result = ("", self._buffer)
        else:
            result = (self._buffer, "")
        self._buffer = ""
        return result


def split_reasoning_tags(text: str) -> tuple[str, str]:
    """Return ``(visible_answer, reasoning)`` for a complete model response."""
    parser = ThinkTagStreamParser()
    visible, reasoning = parser.feed(text or "")
    visible_tail, reasoning_tail = parser.finish()
    return (
        (visible + visible_tail).strip(),
        (reasoning + reasoning_tail).strip(),
    )
