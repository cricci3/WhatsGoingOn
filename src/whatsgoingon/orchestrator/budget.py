from __future__ import annotations

import logging
import threading
import time
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


class AgentUnavailable(RuntimeError):
    """An agent can't produce its output because it hit one of its limits. The orchestrator
    catches it and applies an explicit, recorded fallback - it never means "silently stop"."""

    def __init__(self, message: str, *, agent: str) -> None:
        self.agent = agent
        super().__init__(message)


class BudgetExceeded(AgentUnavailable):
    """Raised before a model call once an agent's or the cycle's budget is used up."""

    def __init__(self, *, agent: str, scope: str, spent: float, limit: float, unit: str) -> None:
        self.scope = scope  # "agent" | "cycle"
        self.spent = spent
        self.limit = limit
        self.unit = unit  # "tokens" | "usd"
        what = f"{agent}'s token budget" if scope == "agent" else "the cycle's cost budget"
        super().__init__(f"{what} is used up ({_fmt(spent, unit)} spent of {_fmt(limit, unit)})", agent=agent)


class AgentTimeout(AgentUnavailable):
    """Raised once an agent has used up its time limit for the current invocation."""

    def __init__(self, *, agent: str, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        super().__init__(f"{agent} didn't finish within its {timeout_s:g}s time limit", agent=agent)


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

    agent_timeout_s is a different kind of limit: wall-clock seconds per *invocation* (one
    agent's turn in one round), not cumulative over the cycle - it's what turns "the Skeptic
    isn't answering" into a fallback instead of a hung cycle. See AgentBudget.

    Thread-safe: agents that run in parallel (Analyst and Context) check and record into the
    same budget from different worker threads, so reads and writes of `spend` take a lock.
    """

    max_cost_usd: float | None = None
    agent_max_tokens: dict[str, int] = field(default_factory=dict)
    agent_timeout_s: dict[str, float] = field(default_factory=dict)
    spend: dict[str, AgentSpend] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    @property
    def cost_usd(self) -> float:
        with self._lock:
            return sum(s.cost_usd for s in self.spend.values())

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return sum(s.total_tokens for s in self.spend.values())

    def for_agent(self, agent: str) -> AgentBudget:
        """A view for one invocation of `agent`: its time limit starts counting now."""
        timeout_s = self.agent_timeout_s.get(agent)
        deadline = time.monotonic() + timeout_s if timeout_s is not None else None
        return AgentBudget(cycle=self, agent=agent, timeout_s=timeout_s, deadline=deadline)

    def check(self, agent: str) -> None:
        """Raise BudgetExceeded if `agent` may not make another model call."""
        with self._lock:
            agent_tokens = self.spend.get(agent, AgentSpend()).total_tokens
            cycle_cost = self.cost_usd
        agent_limit = self.agent_max_tokens.get(agent)
        if agent_limit is not None and agent_tokens >= agent_limit:
            raise BudgetExceeded(
                agent=agent, scope="agent", spent=agent_tokens, limit=agent_limit, unit="tokens"
            )
        if self.max_cost_usd is not None and cycle_cost >= self.max_cost_usd:
            raise BudgetExceeded(
                agent=agent, scope="cycle", spent=cycle_cost, limit=self.max_cost_usd, unit="usd"
            )

    def record(self, agent: str, *, model: str, input_tokens: int, output_tokens: int) -> AgentSpend:
        if model not in PRICES_PER_MTOK:
            logger.warning("no price for model %s; costing it at the highest known rate", model)
        with self._lock:
            spent = self.spend.setdefault(agent, AgentSpend())
            spent.calls += 1
            spent.input_tokens += input_tokens
            spent.output_tokens += output_tokens
            spent.cost_usd += estimate_cost_usd(model, input_tokens, output_tokens)
            return spent

    def summary(self) -> dict[str, Any]:
        """JSON-friendly snapshot of limits and spend, for logs and the transcript."""
        with self._lock:
            return self._summary()

    def _summary(self) -> dict[str, Any]:
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
    """One agent invocation's view of the cycle budget: what call_structured() checks before
    each model call and records into after it, without needing to know which agent it's
    running. deadline is a time.monotonic() value (None: no time limit)."""

    cycle: CycleBudget
    agent: str
    timeout_s: float | None = None
    deadline: float | None = None

    def check(self) -> None:
        self.cycle.check(self.agent)
        self.check_time()

    def check_time(self) -> None:
        if self.timed_out():
            raise AgentTimeout(agent=self.agent, timeout_s=self.timeout_s)

    def timed_out(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def remaining_s(self) -> float | None:
        return None if self.deadline is None else max(self.deadline - time.monotonic(), 0.0)

    def record(self, *, model: str, input_tokens: int, output_tokens: int) -> AgentSpend:
        return self.cycle.record(
            self.agent, model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )
