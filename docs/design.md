# Design

## Boundary

OutcomeLock sits between an agent's planner and executor. It does not generate plans and does not judge whether real-world evidence is true. Source adapters create assertions; OutcomeLock applies deterministic policy.

```text
tracker / email / API / user assertion
                 |
                 v
       provenance observation
                 |
                 v
agent plan -> OutcomeLock -> proceed / suppress / blocked -> executor
```

## Data model

An observation contains:

- `work_key`: stable identity for one logical outcome
- `verdict`: `completed`, `superseded`, or `ambiguous`
- `source`: adapter or source class
- `locator`: opaque reference to the source evidence
- `summary`: concise, non-sensitive explanation
- `observed_at`: timezone-aware source timestamp, normalized to UTC with microseconds preserved
- `confidence`: policy input from zero to one
- `content_hash`: deterministic hash of verdict, summary, normalized timestamp, and confidence

SQLite enforces idempotency across work key, source, locator, and content hash. Changing a decision-relevant field creates a new observation rather than mutating or silently discarding history. Plans, evidence documents, and machine-readable CLI output carry `schema_version: "1.0"`.

## Decision algorithm

1. Sort evidence by `observed_at`, then insertion ID.
2. No evidence means `proceed`.
3. Different verdicts at the newest timestamp mean `blocked`.
4. Newest `ambiguous` evidence means `blocked`.
5. Terminal evidence below the confidence threshold means `blocked`.
6. Strong `completed` or `superseded` evidence means `suppress`.

Terminal assertions are monotonic even if a later plan is created. A genuinely reopened outcome uses a new work key such as `tax.ay2027-28.prefiling-documents`.

## Threat model

OutcomeLock protects against:

- stale queues after completion outside the agent ledger
- paraphrased duplicates sharing one work key
- cached plans that predate stronger evidence
- weak evidence treated as certainty
- unresolved contradictions silently choosing a winner
- malformed or schema-expanded plan/evidence input
- duplicate evidence imports
- HTML injection through evidence summaries

OutcomeLock does not protect against:

- a malicious source adapter recording false evidence
- two distinct work keys that actually represent the same outcome
- compromised local SQLite files
- correctness of the real-world task itself
- an executor that bypasses OutcomeLock

## Integration contract

A production integration should enforce all three boundaries:

1. Every executable action has a work key.
2. Every planner-to-executor path calls OutcomeLock.
3. A non-zero OutcomeLock result prevents execution and produces a visible integrity event.
4. Safety reads use an existing initialized store; a missing or mistyped database path is an input error and never an empty-store `proceed` decision.

The CLI exit codes are designed for shell, CI, cron, and agent-tool integration. JSON output is available for structured consumers.
