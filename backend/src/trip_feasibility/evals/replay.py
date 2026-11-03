"""Replaying model replies, so an eval rerun repeats the same LLM output (decision 87).

The model varies from run to run, so two parsed-mode runs differ even when nothing changed.
`ReplayClient` wraps a client: each reply is saved under a hash of the whole request (model,
prompt, messages, settings), and the same request later gets the saved reply, at no cost.
A change to the prompt, the text or the model is a new request and goes to the model. Only
finished replies are saved (not refusals or cut-offs). Eval runs only (`run_eval.py
--replay`); the app never replays.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from trip_feasibility.llm import MessagesClient


def _reply(data: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=data["stop_reason"],
        content=[SimpleNamespace(**b) for b in data["content"]],
        usage=SimpleNamespace(**data["usage"]),
    )


class ReplayClient:
    def __init__(self, client: MessagesClient, root: Path) -> None:
        self._client, self._root = client, root
        self.messages = self  # `client.messages.create(...)`, like the SDK
        self.hits = self.misses = 0

    async def create(self, **request: Any) -> Any:
        key = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode())
        path = self._root / f"{key.hexdigest()[:24]}.json"
        if path.exists():
            self.hits += 1
            return _reply(json.loads(path.read_text()))
        self.misses += 1
        message = await self._client.messages.create(**request)
        if message.stop_reason == "end_turn":
            self._root.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "stop_reason": message.stop_reason,
                        "content": [
                            {"type": b.type, "text": b.text}
                            for b in message.content
                            if b.type == "text"
                        ],
                        "usage": {
                            "input_tokens": message.usage.input_tokens,
                            "output_tokens": message.usage.output_tokens,
                        },
                    }
                )
            )
        return message
