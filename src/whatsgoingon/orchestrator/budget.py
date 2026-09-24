from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# USD per million tokens, (input, output) - Anthropic first-party API list prices. Prompt
# caching isn't used anywhere in the orchestrator, so cache read/write rates aren't modelled.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-fable-5-1": (10.00, 50.00),
}
# An unknown model is priced like the most expensive known one: overestimating the spend
# trips the budget early, underestimating it would let a cycle overspend unnoticed.
UNKNOWN_MODEL_PRICE = max(PRICES_PER_MTOK.values())


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, UNKNOWN_MODEL_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class BudgetExceeded(RuntimeError):
    """Raised before a model call once an agent's or the cycle's budget is used up. The
    orchestrator catches it and applies an explicit, recorded fallback - it never means
    "silently stop"."""

    def __init__(self, *, agent: str, scope: str, spent: float, limit: float, unit: str) -> None:
        self.agent = agent
        self.scope = scope  # "agent" | "cycle"
        self.spent = spent
        self.limit = limit
        self.unit = unit  # "tokens" | "usd"
        what = f"{agent}'s token budget" if scope == "agent" else "the cycle's cost budget"
        super().__init__(f"{what} is used up ({_fmt(spent, unit)} spent of {_fmt(limit, unit)})")


def _fmt(value: float, unit: str) -> str:
    return f"${value:.4f}" if unit == "usd" else f"{int(value):,} tokens"


@dataclass
class AgentSpend:
    """Running totals for one agent across every model call it made in the cycle."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class CycleBudget:
    """Spending limits for one debate cycle, and what has been spent against them.

    Two kinds of limit, because they guard against different things:
    - agent_max_tokens: per-agent token caps (keyed by agent name), so one agent that loops
      on research tools can't eat the whole cycle's budget before the others get a turn;
    - max_cost_usd: one cap on the whole cycle's estimated cost, which is what the bill is
      in - a cycle's cost depends on which model each role runs on, tokens alone don't say.

    None means "no limit". Limits are checked *before* each model call against what's already
    been spent, since a call's size isn't known until it returns - so a cycle can overshoot a
    limit by at most the one call that crossed it, never by more.
    """

    max_cost_usd: float | None = None
    agent_max_tokens: dict[str, int] = field(default_factory=dict)
    spend: dict[str, AgentSpend] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.spend.values())

    @property
    def total_tokens(self) -> int:
        return sum(s.total_tokens for s in self.spend.values())

    def for_agent(self, agent: str) -> AgentBudget:
        return AgentBudget(cycle=self, agent=agent)

    def check(self, agent: str) -> None:
        """Raise BudgetExceeded if `agent` may not make another model call."""
        spent = self.spend.get(agent, AgentSpend())
        agent_limit = self.agent_max_tokens.get(agent)
        if agent_limit is not None and spent.total_tokens >= agent_limit:
            raise BudgetExceeded(
                agent=agent, scope="agent", spent=spent.total_tokens, limit=agent_limit, unit="tokens"
            )
        if self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(
                agent=agent, scope="cycle", spent=self.cost_usd, limit=self.max_cost_usd, unit="usd"
            )

    def record(self, agent: str, *, model: str, input_tokens: int, output_tokens: int) -> AgentSpend:
        if model not in PRICES_PER_MTOK:
            logger.warning("no price for model %s; costing it at the highest known rate", model)
        spent = self.spend.setdefault(agent, AgentSpend())
        spent.calls += 1
        spent.input_tokens += input_tokens
        spent.output_tokens += output_tokens
        spent.cost_usd += estimate_cost_usd(model, input_tokens, output_tokens)
        return spent

    def summary(self) -> dict[str, Any]:
        """JSON-friendly snapshot of limits and spend, for logs and the transcript."""
        return {
            "cost_usd": round(self.cost_usd, 6),
            "max_cost_usd": self.max_cost_usd,
            "total_tokens": self.total_tokens,
            "agents": {
                agent: {
                    "calls": s.calls,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "cost_usd": round(s.cost_usd, 6),
                    "max_tokens": self.agent_max_tokens.get(agent),
                }
                for agent, s in self.spend.items()
            },
        }


@dataclass(frozen=True)
class AgentBudget:
    """One agent's view of the cycle budget: what call_structured() checks before each model
    call and records into after it, without needing to know which agent it's running."""

    cycle: CycleBudget
    agent: str

    def check(self) -> None:
        self.cycle.check(self.agent)

    def record(self, *, model: str, input_tokens: int, output_tokens: int) -> AgentSpend:
        return self.cycle.record(
            self.agent, model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )
