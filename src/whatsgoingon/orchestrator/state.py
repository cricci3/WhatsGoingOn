from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from whatsgoingon.agent.state import SeriesDelta, format_deltas
from whatsgoingon.orchestrator.budget import CycleBudget


@dataclass(frozen=True)
class Claim:
    """One discrete, checkable assertion pulled out of the Analyst's narrative prose,
    so the Skeptic can target a critique at a specific claim instead of the whole draft.

    supporting_deltas holds the actual SeriesDelta object(s) the claim is based on - not a
    series name the Skeptic would have to re-look-up or a number it would have to trust from
    the prose. This lets the Skeptic check what the Analyst *concluded* from a correct number
    (hasty causality, unjustified confidence) without re-deriving the arithmetic itself, and
    without risking a second, slightly different computation of the same delta."""

    id: str
    text: str
    claim_type: Literal["factual", "causal", "correlation"]
    supporting_deltas: list[SeriesDelta] = field(default_factory=list)  # empty if purely qualitative
    confidence: str | None = None  # Analyst's own stated confidence: "low" | "medium" | "high"


@dataclass(frozen=True)
class Draft:
    """One version of the narrative, produced or revised by the Analyst."""

    narrative: str
    claims: list[Claim]


@dataclass(frozen=True)
class Critique:
    """One issue the Skeptic raised against a specific claim: unsupported claim, hasty
    causality, or inflated confidence (the three failure modes named in TODO.md)."""

    claim_id: str
    severity: str  # "low" | "medium" | "high"
    comment: str


@dataclass(frozen=True)
class DebateRound:
    """One Analyst -> Skeptic pass, kept so the API can later expose the full debate
    transcript (bozza, critiche, finale, changelog), not just the final narrative.

    editor_decision is set only when the Editor reviewed this round and sent it back
    ("revise"), so the Analyst's next draft can see why; the publishing decision lives on
    DebateState.decision instead.

    review_skipped is set (to the reason) when the Skeptic couldn't review this draft, e.g.
    its budget ran out - so an empty critiques list is never mistaken for "the draft held up"."""

    draft: Draft
    critiques: list[Critique]
    editor_decision: EditorDecision | None = None
    review_skipped: str | None = None


@dataclass(frozen=True)
class EditorDecision:
    """The Editor's verdict on the latest round: publish (as-is or lightly edited), or
    send the draft back to the Analyst for another round."""

    action: str  # "publish" | "revise"
    reason: str
    final_narrative: str | None = None  # set only when action == "publish"
    changelog: str | None = None  # set only when action == "publish"


@dataclass(frozen=True)
class ContextEvent:
    """One real-world event the Context agent found, tied to the tracked series it may explain."""

    summary: str
    related_series: list[str]
    source: str | None = None


@dataclass(frozen=True)
class ContextBrief:
    """News context gathered by the Context agent in parallel with the Analyst's first draft.
    Given to the Skeptic (to check causal claims against real events, not just co-movement)
    and to the Analyst's revisions."""

    events: list[ContextEvent]
    notes: str | None = None


@dataclass(frozen=True)
class Fallback:
    """A degradation the orchestrator applied instead of failing: which agent couldn't do its
    part, in which round, why, and what happened instead. Kept on DebateState and shown in the
    transcript and the Editor's prompt, so a degraded cycle is always visibly degraded."""

    agent: str
    round: int
    reason: str
    action: str


@dataclass
class DebateState:
    """Shared state threaded through the orchestrator across rounds - the message-passing
    contract between Analyst, Skeptic, and Editor. Mutated in place by appending DebateRounds
    as the orchestrator loop runs, so every agent sees the full history, not just the latest turn.

    deltas is computed once (agent/state.py's compute_deltas(), same as the Phase 1 agent) and
    passed by reference here rather than recomputed per agent, so Analyst, Skeptic, and Editor
    all reason about the exact same data snapshot for the whole cycle - no drift between an
    as_of used in round 1 and a slightly different one in round 2.

    budget carries the cycle's spending limits and running spend; fallbacks records every
    degradation applied when a limit was hit."""

    month: str
    deltas: list[SeriesDelta]
    max_rounds: int
    rounds: list[DebateRound] = field(default_factory=list)
    decision: EditorDecision | None = None
    budget: CycleBudget = field(default_factory=CycleBudget)
    fallbacks: list[Fallback] = field(default_factory=list)
    context: ContextBrief | None = None  # None: not gathered (disabled, or the agent failed)

    @property
    def delta_summary(self) -> str:
        """Plain-text rendering of `deltas` for prompts - derived on demand so it can never
        drift out of sync with the SeriesDelta objects Claims actually cite."""
        return format_deltas(self.deltas)

    @property
    def round_number(self) -> int:
        """1-indexed number of the round about to run."""
        return len(self.rounds) + 1

    @property
    def latest_draft(self) -> Draft | None:
        return self.rounds[-1].draft if self.rounds else None

    @property
    def latest_critiques(self) -> list[Critique]:
        return self.rounds[-1].critiques if self.rounds else []

    def has_blocking_critiques(self) -> bool:
        """True if the latest round's critiques include anything high-severity - the signal
        the orchestrator uses to decide whether the Analyst gets another round."""
        return any(c.severity == "high" for c in self.latest_critiques)


def format_draft(draft: Draft) -> str:
    """Plain-text rendering of a draft and its tagged claims for prompts."""
    lines = [draft.narrative, "", "Claims:"]
    for claim in draft.claims:
        series = ", ".join(d.name for d in claim.supporting_deltas) or "none"
        lines.append(
            f"- [{claim.id}] ({claim.claim_type}, confidence: {claim.confidence or 'unstated'}; "
            f"supporting series: {series}) {claim.text}"
        )
    return "\n".join(lines)


def format_critiques(critiques: list[Critique]) -> str:
    if not critiques:
        return "(no critiques)"
    return "\n".join(f"- [{c.claim_id}] {c.severity}: {c.comment}" for c in critiques)


def format_review(debate_round: DebateRound) -> str:
    """The Skeptic's side of a round: its critiques, or why there aren't any."""
    if debate_round.review_skipped is not None:
        return f"(NOT REVIEWED - {debate_round.review_skipped})"
    return format_critiques(debate_round.critiques)


def format_fallbacks(fallbacks: list[Fallback]) -> str:
    return "\n".join(f"- round {f.round}, {f.agent}: {f.reason} -> {f.action}" for f in fallbacks)


def format_context(brief: ContextBrief) -> str:
    lines = []
    for event in brief.events:
        related = ", ".join(event.related_series) or "no tracked series"
        source = f" (source: {event.source})" if event.source else ""
        lines.append(f"- [{related}] {event.summary}{source}")
    if not lines:
        lines.append("(no relevant news found)")
    if brief.notes:
        lines.append(f"Notes: {brief.notes}")
    return "\n".join(lines)
