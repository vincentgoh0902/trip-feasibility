"""Prepared model replies: how anonymous visitors use the app without a model call being
paid for (decision 100).

`PreparingClient` wraps the real client (`demo.warm` only): each finished reply is stored in
`api_cache` under a hash of the whole request (model, prompt, messages, settings).
`PreparedClient` is all an anonymous parse or run gets: it serves those stored replies and
has no way to reach the model; a request nobody prepared raises `LLMError("not_prepared")`.
A stored reply answers exactly the request it was made for, so an edited stop or another
date is a new request: the visitor gets the free checks without the model's part.
"""

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from trip_feasibility.checks.deps import ResponseCache
from trip_feasibility.llm import LLMError, MessagesClient

SOURCE = "prepared"
TTL = timedelta(days=3650)  # kept until the request changes (a new prompt version is a new key)
NOT_PREPARED = "the demo only has the model's answers for its sample itineraries as written"


def request_key(request: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode())
    return f"{SOURCE}:{digest.hexdigest()}"


def to_stored(message: Any) -> dict[str, Any]:
    """A finished reply as JSON: its text blocks and token counts."""
    return {
        "stop_reason": message.stop_reason,
        "content": [{"type": b.type, "text": b.text} for b in message.content if b.type == "text"],
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        },
    }


def from_stored(data: dict[str, Any], *, free: bool = False) -> SimpleNamespace:
    """The SDK message shape back; `free` reports no tokens (nothing was spent this time)."""
    usage = {"input_tokens": 0, "output_tokens": 0} if free else data["usage"]
    return SimpleNamespace(
        stop_reason=data["stop_reason"],
        content=[SimpleNamespace(**b) for b in data["content"]],
        usage=SimpleNamespace(**usage),
    )


class PreparedClient:
    def __init__(self, cache: ResponseCache) -> None:
        self._cache = cache
        self.messages = self  # `client.messages.create(...)`, like the SDK

    async def create(self, **request: Any) -> Any:
        stored = await self._cache.get(request_key(request), any_age=True)
        if stored is None:
            raise LLMError("not_prepared", NOT_PREPARED)
        return from_stored(stored, free=True)


class PreparingClient:
    def __init__(self, client: MessagesClient, cache: ResponseCache) -> None:
        self._client, self._cache = client, cache
        self.messages = self
        self.stored = 0

    async def create(self, **request: Any) -> Any:
        message = await self._client.messages.create(**request)
        if message.stop_reason == "end_turn":
            await self._cache.put(SOURCE, request_key(request), to_stored(message), TTL)
            self.stored += 1
        return message
