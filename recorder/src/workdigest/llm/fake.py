"""Deterministic provider for tests and offline runs."""

from __future__ import annotations

from collections.abc import Callable

from workdigest.llm.base import LLMProvider, Prompt


class FakeProvider(LLMProvider):
    """Returns scripted answers; records every prompt it receives."""

    name = "fake"

    def __init__(self, responder: Callable[[Prompt], str] | list[str]) -> None:
        """Answer with ``responder(prompt)`` or pop the next scripted string."""
        self._responder = responder
        self.prompts: list[Prompt] = []

    def complete(self, prompt: Prompt) -> str:
        """Return the next scripted answer."""
        self.prompts.append(prompt)
        if callable(self._responder):
            return self._responder(prompt)
        if not self._responder:
            raise AssertionError("FakeProvider ran out of scripted answers")
        return self._responder.pop(0)
