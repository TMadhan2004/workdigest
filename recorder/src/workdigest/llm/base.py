"""Provider-neutral LLM interface.

Every call returns JSON validated against a pydantic model. Output is never trusted as
is: callers ground it against evidence afterwards (see ``digest/grounding.py``).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.S)


class LLMError(RuntimeError):
    """The provider failed or returned unusable output."""


class LLMUnavailableError(LLMError):
    """The provider cannot be used (not installed, not logged in, disabled)."""


@dataclass(frozen=True, slots=True)
class Prompt:
    """A system instruction plus the user content (the evidence)."""

    system: str
    user: str

    def as_single_text(self) -> str:
        """For CLI agents that take one prompt string."""
        return f"{self.system}\n\n---\n\n{self.user}"


class LLMProvider(ABC):
    """Minimal interface: send a prompt, get text back."""

    name: str = "abstract"

    @abstractmethod
    def complete(self, prompt: Prompt) -> str:
        """Return the model's raw text answer."""

    def complete_json(self, prompt: Prompt, model: type[ModelT], retries: int = 1) -> ModelT:
        """Return the answer parsed into ``model``, retrying once on invalid JSON."""
        last_error: Exception | None = None
        current = prompt
        for _ in range(retries + 1):
            raw = self.complete(current)
            try:
                return model.model_validate(extract_json(raw))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                current = Prompt(
                    system=prompt.system,
                    user=f"{prompt.user}\n\nYour previous answer was not valid JSON for the "
                    f"required schema ({str(exc)[:300]}). Reply with JSON only.",
                )
        raise LLMError(f"{self.name}: invalid JSON after {retries + 1} attempts: {last_error}")


def extract_json(text: str) -> object:
    """Pull one JSON value out of a model answer (handles code fences and chatter)."""
    candidate = text.strip()
    fence = _FENCE.search(candidate)
    if fence:
        candidate = fence["body"].strip()
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    starts = [i for i in (candidate.find("{"), candidate.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON object in model output")
    start = min(starts)
    closer = "}" if candidate[start] == "{" else "]"
    end = candidate.rfind(closer)
    if end <= start:
        raise ValueError("unterminated JSON in model output")
    return json.loads(candidate[start : end + 1])
