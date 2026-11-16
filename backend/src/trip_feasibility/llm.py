"""The one way this app calls Claude: a structured-output request with the API's failures
turned into coded errors. Used by extraction (parse step 1), fixes, the review, the closures
check (which also uses `search_web`) and the second opinion (`search_json`); each caller
keeps its own prompt, schema, retries and error class (a subclass of LLMError)."""

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


# The basic web search tool: the dynamic-filtering versions need Sonnet or Opus.
WEB_SEARCH_TOOL = "web_search_20250305"


@dataclass
class SearchResult:
    url: str
    title: str


@dataclass
class Search:
    """A finished search call: the results the search tool returned, never the model's text."""

    results: list[SearchResult]
    searches: int  # web searches billed
    input_tokens: int
    output_tokens: int


async def search_web(
    client: MessagesClient,
    error: type[LLMError],
    *,
    model: str,
    effort: str,
    system: str,
    query: str,
    max_uses: int = 1,
) -> Search:
    """Asks the model to run `query` through Anthropic's web search tool and returns the
    results' URLs and titles. Whatever the model writes is ignored."""
    try:
        message = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": query}],
            tools=[{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_uses}],
            output_config={"effort": effort},
        )
    except anthropic.APIConnectionError as e:
        raise error("unavailable", f"couldn't reach the model API: {e}") from e
    except anthropic.RateLimitError as e:
        raise error("unavailable", "the model API is rate-limiting us") from e
    except anthropic.APIStatusError as e:
        raise error("unavailable", f"the model API returned {e.status_code}") from e
    results: list[SearchResult] = []
    for block in message.content:
        if block.type != "web_search_tool_result":
            continue
        if not isinstance(block.content, list):  # an error object, e.g. max_uses_exceeded
            raise error("search_failed", f"web search failed: {block.content.error_code}")
        results += [SearchResult(url=r.url, title=r.title) for r in block.content]
    server_tools = getattr(message.usage, "server_tool_use", None)
    return Search(
        results=results,
        searches=getattr(server_tools, "web_search_requests", 0) or 0,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )


@dataclass
class SearchedReply(Reply):
    """A structured-output call that could search the web: the reply, plus every result
    the searches returned (the only URLs a claim may cite) and the searches billed."""

    results: list[SearchResult]
    searches: int


async def search_json(
    client: MessagesClient,
    error: type[LLMError],
    *,
    model: str,
    max_tokens: int,
    system: str,
    user: str,
    effort: str,
    schema: dict[str, Any],
    max_searches: int,
    max_continuations: int = 3,
) -> SearchedReply:
    """One structured-output answer with Anthropic's web search tool available. A turn the
    API pauses (`pause_turn`, its server-side loop limit) is resumed as is, at most
    `max_continuations` times. A failed search raises `error("search_failed")`."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}]
    output_config = {"effort": effort, "format": {"type": "json_schema", "schema": schema}}
    results: list[SearchResult] = []
    searches = input_tokens = output_tokens = 0
    for _ in range(max_continuations + 1):
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                output_config=output_config,
            )
        except anthropic.APIConnectionError as e:
            raise error("unavailable", f"couldn't reach the model API: {e}") from e
        except anthropic.RateLimitError as e:
            raise error("unavailable", "the model API is rate-limiting us") from e
        except anthropic.APIStatusError as e:
            raise error("unavailable", f"the model API returned {e.status_code}") from e
        input_tokens += message.usage.input_tokens
        output_tokens += message.usage.output_tokens
        server_tools = getattr(message.usage, "server_tool_use", None)
        searches += getattr(server_tools, "web_search_requests", 0) or 0
        for block in message.content:
            if block.type != "web_search_tool_result":
                continue
            if not isinstance(block.content, list):  # an error object, e.g. max_uses_exceeded
                if block.content.error_code == "max_uses_exceeded":
                    continue  # the model answers from what it has
                raise error("search_failed", f"web search failed: {block.content.error_code}")
            results += [SearchResult(url=r.url, title=r.title) for r in block.content]
        if message.stop_reason != "pause_turn":
            break
        # resume: the paused turn goes back as is, with no new user message
        content = [b.model_dump() if hasattr(b, "model_dump") else b for b in message.content]
        messages = [messages[0], {"role": "assistant", "content": content}]
    return SearchedReply(
        stop_reason=message.stop_reason,
        text="".join(b.text for b in message.content if b.type == "text"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        results=results,
        searches=searches,
    )
