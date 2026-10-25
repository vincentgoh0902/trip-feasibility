"""What an LLM call cost, from its token counts (PRD: cost logged per run, under $0.01).

Anthropic first-party list prices in USD per million tokens, as of 2026-10-06. Thinking
tokens are billed as output and are already in `output_tokens`. A model missing here costs
None (unknown), never a guess.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Price:
    input: Decimal  # per million tokens
    output: Decimal
    # Some models charge more once the prompt passes a size: (threshold, input, output)
    long_prompt: tuple[int, Decimal, Decimal] | None = None


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(
        Decimal("0.10"), Decimal("0.50"), long_prompt=(100_000, Decimal("0.50"), Decimal("2.50"))
    ),
    "claude-sonnet-5-5": Price(Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5-5": Price(Decimal("4.00"), Decimal("20.00")),
}
MILLION = Decimal(1_000_000)


def call_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """One call's cost: long-prompt pricing depends on that call's prompt, not a run's total."""
    price = PRICES.get(model)
    if price is None:
        return None
    rate_in, rate_out = price.input, price.output
    if price.long_prompt and input_tokens > price.long_prompt[0]:
        _, rate_in, rate_out = price.long_prompt
    return (input_tokens * rate_in + output_tokens * rate_out) / MILLION
