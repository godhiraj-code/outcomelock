from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

WORK_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
TERMINAL_VERDICTS = {"completed", "superseded"}
VERDICTS = TERMINAL_VERDICTS | {"ambiguous"}


def normalize_time(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    raw = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def validate_work_key(value: str) -> str:
    if not WORK_KEY_RE.fullmatch(value):
        raise ValueError(
            "work_key must be 3-128 lowercase characters: a-z, 0-9, dot, colon, dash, underscore"
        )
    return value


def content_hash(verdict: str, summary: str, observed_at: str, confidence: float) -> str:
    payload = json.dumps(
        {
            "confidence": confidence,
            "observed_at": observed_at,
            "summary": summary,
            "verdict": verdict,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Fact:
    id: int
    work_key: str
    verdict: str
    source: str
    locator: str
    summary: str
    observed_at: str
    confidence: float
    content_hash: str


@dataclass(frozen=True)
class Decision:
    work_key: str
    state: str
    reason: str
    planned_at: str | None = None
    fact: Fact | None = None

    def as_dict(self) -> dict:
        return {
            "work_key": self.work_key,
            "state": self.state,
            "reason": self.reason,
            "planned_at": self.planned_at,
            "fact": None
            if self.fact is None
            else {
                "id": self.fact.id,
                "verdict": self.fact.verdict,
                "source": self.fact.source,
                "locator": self.fact.locator,
                "summary": self.fact.summary,
                "observed_at": self.fact.observed_at,
                "confidence": self.fact.confidence,
                "content_hash": self.fact.content_hash,
            },
        }


def evaluate(
    work_key: str,
    facts: Iterable[Fact],
    planned_at: str | None = None,
    min_confidence: float = 0.90,
) -> Decision:
    validate_work_key(work_key)
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")
    normalized_plan = normalize_time(planned_at) if planned_at else None
    validated: list[tuple[str, Fact]] = []
    for fact in facts:
        if fact.work_key != work_key:
            raise ValueError("evidence work_key does not match the requested work_key")
        if fact.verdict not in VERDICTS:
            raise ValueError(f"unsupported evidence verdict: {fact.verdict}")
        if not 0 <= fact.confidence <= 1:
            raise ValueError("evidence confidence must be between 0 and 1")
        validated.append((normalize_time(fact.observed_at), fact))
    ordered = sorted(validated, key=lambda item: (item[0], item[1].id), reverse=True)
    if not ordered:
        return Decision(
            work_key, "proceed", "No completion or conflict evidence exists.", normalized_plan
        )

    newest_time = ordered[0][0]
    newest = [item[1] for item in ordered if item[0] == newest_time]
    verdicts = {item.verdict for item in newest}
    if len(verdicts) > 1:
        return Decision(
            work_key,
            "blocked",
            "Contradictory evidence has the same newest timestamp.",
            normalized_plan,
            newest[0],
        )

    fact = min(newest, key=lambda item: (item.confidence, -item.id))
    if fact.verdict == "ambiguous":
        return Decision(
            work_key,
            "blocked",
            "Newest evidence is ambiguous; human reconciliation is required.",
            normalized_plan,
            fact,
        )
    if fact.confidence < min_confidence:
        return Decision(
            work_key,
            "blocked",
            f"Newest terminal evidence confidence {fact.confidence:.2f} is below policy threshold {min_confidence:.2f}.",
            normalized_plan,
            fact,
        )
    relation = (
        "newer than the plan"
        if normalized_plan and fact.observed_at >= normalized_plan
        else "a monotonic terminal assertion"
    )
    return Decision(
        work_key,
        "suppress",
        f"Work is {fact.verdict}; evidence is {relation}.",
        normalized_plan,
        fact,
    )
