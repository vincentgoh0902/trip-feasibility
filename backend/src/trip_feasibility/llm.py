"""The one way this app calls Claude: a structured-output request with the API's failures
turned into coded errors. Used by extraction (parse step 1) and fixes; each caller keeps its
own prompt, schema, retries and error class (a subclass of LLMError)."""

from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from trip_feasibility.config import Settings


class MessagesClient(Protocol):
    """The part of anthropic.AsyncAnthropic used here (tests pass a fake)."""

    @property
    def messages(self) -> Any: ...


class LLMError(Exception):
    """A model call that produced nothing usable; `code` says why (unavailable, refused,
    truncated, or a caller's own code)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def make_client(settings: Settings) -> anthropic.AsyncAnthropic:
    if settings.anthropic_api_key is None:
        raise LLMError("unavailable", "ANTHROPIC_API_KEY is not set")
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())


@dataclass
class Reply:
    """A finished call: tokens are counted even when its stop reason makes it unusable."""

    stop_reason: str | None
    text: str
    input_tokens: int
    output_tokens: int

    def usable_text(self, error: type[LLMError], *, refused: str, truncated: str) -> str:
        """The JSON text, or `error` with the caller's wording for a refusal or a cut-off."""
        if self.stop_reason == "refusal":
            raise error("refused", refused)
        if self.stop_reason == "max_tokens":
            raise error("truncated", truncated)
        return self.text


async def create_json(
    client: MessagesClient,
    error: type[LLMError],
    *,
    model: str,
    max_tokens: int,
    system: str,
    messages: list[dict[str, Any]],
    effort: str,
    schema: dict[str, Any],
) -> Reply:
    """One call with the output held to `schema`; API failures raise `error("unavailable")`."""
    try:
        message = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIConnectionError as e:
        raise error("unavailable", f"couldn't reach the model API: {e}") from e
    except anthropic.RateLimitError as e:
        raise error("unavailable", "the model API is rate-limiting us") from e
    except anthropic.APIStatusError as e:
        raise error("unavailable", f"the model API returned {e.status_code}") from e
    return Reply(
        stop_reason=message.stop_reason,
        text="".join(b.text for b in message.content if b.type == "text"),
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )
