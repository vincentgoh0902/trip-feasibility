"""Search-enabled LLMs behind one interface: a prompt in, JSON matching a schema out.

Used by the gold drafter (dataset/draft.py) and the LLM-only baseline, so either can run on
any provider. A backend reports the provider and exact model it used, so every output can
be recorded with it (`drafted_by`, eval_runs). Mac-side tooling only: nothing in the
deployed app calls these.

Backends:
- `codex`: the Codex CLI (`codex exec`), signed in with a ChatGPT plan, with live web
  search. It runs read-only in an empty temporary folder, so it sees only the prompt.
- `claude-code`: headless Claude Code (`claude -p`), signed in with a Claude plan (no API
  key). Stripped to a plain model with search: its own system prompt replaced, no settings,
  MCP servers or tools but WebSearch and WebFetch, in an empty temporary folder. A hook
  that adds anything to the context fails the run.
"""

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

TIMEOUT_S = 20 * 60


class SearchLLMError(Exception):
    """The run failed or returned nothing usable; the message never quotes the prompt."""


@dataclass
class SearchResult:
    output: dict[str, Any]
    provider: str
    model: str
    searches: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


class SearchLLM(Protocol):
    provider: str
    model: str

    @property
    def label(self) -> str:
        """Provider, model and tool version, as recorded with every output."""
        ...

    async def run(self, prompt: str, schema: dict[str, Any]) -> SearchResult: ...


class CodexBackend:
    """OpenAI models through the Codex CLI with web search (`codex --search exec`)."""

    provider = "openai"

    def __init__(self, model: str, effort: str = "high", binary: str = "codex") -> None:
        self.model = model
        self.effort = effort
        self.binary = binary
        self.version = ""

    @property
    def label(self) -> str:
        return f"{self.model} ({self.effort}) via {self.version or 'codex-cli'}"

    def command(self, workdir: Path, schema_path: Path, out_path: Path) -> list[str]:
        # --search is a top-level flag: after `exec` it's rejected
        return [
            self.binary, "--search", "exec",
            "--model", self.model,
            "-c", f'model_reasoning_effort="{self.effort}"',
            "--sandbox", "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--cd", str(workdir),
            "--output-schema", str(schema_path),
            "--output-last-message", str(out_path),
            "--json",
            "-",  # the prompt comes on stdin
        ]  # fmt: skip

    async def _version(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "--version", stdout=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        return out.decode().strip()

    async def run(self, prompt: str, schema: dict[str, Any]) -> SearchResult:
        if not self.version:
            self.version = await self._version()
        loop = asyncio.get_running_loop()
        started = loop.time()
        with tempfile.TemporaryDirectory(prefix="search-llm-") as tmp:
            tmp_path = Path(tmp)
            workdir = tmp_path / "work"  # empty: nothing to read but the prompt
            workdir.mkdir()
            schema_path = tmp_path / "schema.json"
            schema_path.write_text(json.dumps(schema))
            out_path = tmp_path / "last.json"
            proc = await asyncio.create_subprocess_exec(
                *self.command(workdir, schema_path, out_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                events, err = await asyncio.wait_for(proc.communicate(prompt.encode()), TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                raise SearchLLMError(f"codex timed out after {TIMEOUT_S} s") from None
            if proc.returncode != 0:
                tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
                raise SearchLLMError(f"codex exited {proc.returncode}: {tail[0][:300]}")
            if not out_path.exists():
                raise SearchLLMError("codex wrote no final message")
            try:
                output = json.loads(out_path.read_text())
            except json.JSONDecodeError:
                raise SearchLLMError("codex's final message isn't JSON") from None
        result = SearchResult(output=output, provider=self.provider, model=self.model)
        result.seconds = loop.time() - started
        parse_codex_events(events.decode(errors="replace"), result)
        return result


def parse_codex_events(jsonl: str, result: SearchResult) -> None:
    """Search count and token usage from `codex exec --json` events."""
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "web_search":
            result.searches += 1
        if event.get("type") == "turn.completed":
            for k, v in (event.get("usage") or {}).items():
                if isinstance(v, int):
                    result.usage[k] = result.usage.get(k, 0) + v


SYSTEM_PROMPT = (
    "Use web search to check facts against official sources. Answer only through the "
    "structured output, in the JSON schema given."
)


class ClaudeCodeBackend:
    """Anthropic models through headless Claude Code with web search (`claude -p`)."""

    provider = "anthropic"

    def __init__(self, model: str, effort: str = "medium", binary: str = "claude") -> None:
        self.model = model
        self.effort = effort
        self.binary = binary
        self.version = ""

    @property
    def label(self) -> str:
        return f"{self.model} ({self.effort}) via {self.version or 'claude-code'}"

    def command(self, schema: dict[str, Any]) -> list[str]:
        tools = ["WebSearch", "WebFetch"]
        return [
            self.binary, "-p",
            "--model", self.model,
            "--effort", self.effort,
            "--system-prompt", SYSTEM_PROMPT,
            "--tools", *tools,
            "--allowedTools", *tools,
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--output-format", "stream-json",
            "--verbose",
            "--json-schema", json.dumps(schema),
        ]  # fmt: skip

    async def _version(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "--version", stdout=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        return f"claude-code {out.decode().split()[0]}" if out.strip() else ""

    async def run(self, prompt: str, schema: dict[str, Any]) -> SearchResult:
        if not self.version:
            self.version = await self._version()
        loop = asyncio.get_running_loop()
        started = loop.time()
        with tempfile.TemporaryDirectory(prefix="search-llm-") as tmp:
            proc = await asyncio.create_subprocess_exec(
                *self.command(schema),
                cwd=tmp,  # empty: nothing to read but the prompt
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                events, err = await asyncio.wait_for(proc.communicate(prompt.encode()), TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                raise SearchLLMError(f"claude timed out after {TIMEOUT_S} s") from None
        if proc.returncode != 0:
            tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
            raise SearchLLMError(f"claude exited {proc.returncode}: {tail[0][:300]}")
        result = SearchResult(output={}, provider=self.provider, model=self.model)
        result.seconds = loop.time() - started
        parse_claude_events(events.decode(errors="replace"), result)
        return result


def parse_claude_events(jsonl: str, result: SearchResult) -> None:
    """Output, searches, usage and the model actually used from `claude -p` stream-json.
    Raises if the run failed, used another model, or a hook added to the context."""
    final = None
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "hook_response":
            if (event.get("stdout") or "").strip() not in ("", "{}"):
                raise SearchLLMError(f"hook {event.get('hook_name')} added to the context")
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use" and block.get("name") == "WebSearch":
                    result.searches += 1
        elif kind == "result":
            final = event
    if final is None or final.get("is_error") or final.get("subtype") != "success":
        raise SearchLLMError(f"claude run failed ({(final or {}).get('subtype', 'no result')})")
    models = list(final.get("modelUsage") or {})
    if models and result.model not in models:  # a helper model (web fetch) may appear too
        raise SearchLLMError(f"claude used {', '.join(models)}, not {result.model}")
    if not isinstance(final.get("structured_output"), dict):
        raise SearchLLMError("claude returned no structured output")
    result.output = final["structured_output"]
    result.usage = {k: v for k, v in (final.get("usage") or {}).items() if isinstance(v, int)}


BACKENDS = {"codex": CodexBackend, "claude-code": ClaudeCodeBackend}


def make_backend(name: str, model: str, effort: str = "high") -> SearchLLM:
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r} (have: {', '.join(BACKENDS)})")
    return BACKENDS[name](model, effort)
