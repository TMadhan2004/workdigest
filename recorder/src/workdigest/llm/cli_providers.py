"""LLM providers that drive locally installed, logged-in agent CLIs.

Both run in an empty sandbox directory, in read-only modes, so the model cannot touch
repositories even though the CLIs are full agents:

* ``cursor_cli``: ``cursor-agent -p --mode ask --output-format json``.
  Verified against ``cursor-agent --help`` (2026.03.30): ``-p`` "has access to all tools,
  including write and shell"; ``--mode ask`` is read-only Q&A. ``-f``/``--force`` is never
  passed, so shell and write actions are not auto-approved. ``--sandbox enabled`` is not
  used: on this Mac it fails with "Sandbox mode is enabled but not available on this
  system" (checked 24 Oct 2026), and disabling it globally would change the user's Cursor setup.
* ``claude_cli``: ``claude -p --output-format json --tools ""`` with a replaced system
  prompt, ``--setting-sources project`` (so user hooks do not fire) and
  ``--no-session-persistence`` (so no transcript is written). Verified against
  ``claude --help`` (2.1.153).

Our own generation must never be recorded as work: the sandbox directory name starts with
``SANDBOX_PREFIX``, and ingest drops any event whose working directory contains it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from workdigest.ingest.normalize import OWN_SANDBOX_MARKER
from workdigest.llm.base import LLMError, LLMProvider, LLMUnavailableError, Prompt

DEFAULT_TIMEOUT_S = 240
SANDBOX_PREFIX = OWN_SANDBOX_MARKER


class _CliProvider(LLMProvider):
    binary: str = ""

    def __init__(
        self,
        model: str | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        binary: str | None = None,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.binary_path = binary or shutil.which(self.binary)

    def _argv(self, workspace: Path, prompt: Prompt) -> Sequence[str]:
        raise NotImplementedError

    def _parse(self, stdout: str) -> str:
        raise NotImplementedError

    def complete(self, prompt: Prompt) -> str:
        if not self.binary_path:
            raise LLMUnavailableError(f"{self.binary} is not installed or not on PATH")
        with tempfile.TemporaryDirectory(prefix=SANDBOX_PREFIX) as sandbox:
            try:
                completed = subprocess.run(
                    list(self._argv(Path(sandbox), prompt)),
                    cwd=sandbox,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"{self.name}: timed out after {self.timeout_s}s") from exc
            except OSError as exc:
                raise LLMUnavailableError(f"{self.name}: {exc}") from exc
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()[:500]
            if "login" in message.lower() or "authentication" in message.lower():
                raise LLMUnavailableError(f"{self.name}: not logged in ({message})")
            raise LLMError(f"{self.name}: exit {completed.returncode}: {message}")
        return self._parse(completed.stdout)


class CursorCliProvider(_CliProvider):
    """Uses the Cursor subscription through ``cursor-agent``."""

    name = "cursor_cli"
    binary = "cursor-agent"

    def _argv(self, workspace: Path, prompt: Prompt) -> Sequence[str]:
        argv = [
            str(self.binary_path),
            "-p",
            "--mode",
            "ask",
            "--output-format",
            "json",
            "--trust",
            "--workspace",
            str(workspace),
        ]
        if self.model:
            argv += ["--model", self.model]
        return [*argv, prompt.as_single_text()]

    def _parse(self, stdout: str) -> str:
        return _result_field(stdout, self.name)


class ClaudeCliProvider(_CliProvider):
    """Uses Claude Code's headless mode with every tool disabled."""

    name = "claude_cli"
    binary = "claude"

    def _argv(self, workspace: Path, prompt: Prompt) -> Sequence[str]:
        argv = [
            str(self.binary_path),
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--system-prompt",
            prompt.system,
            "--setting-sources",
            "project",
            "--no-session-persistence",
        ]
        if self.model:
            argv += ["--model", self.model]
        return [*argv, prompt.user]

    def _parse(self, stdout: str) -> str:
        return _result_field(stdout, self.name)


def _result_field(stdout: str, name: str) -> str:
    """Both CLIs print ``{"result": "<text>", ...}`` in json mode; fall back to raw text."""
    text = stdout.strip()
    try:
        envelope = json.loads(text)
    except ValueError:
        return text
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            raise LLMError(f"{name}: {str(envelope.get('result'))[:500]}")
        result = envelope.get("result")
        if isinstance(result, str):
            return result
    return text
