from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import unicodedata
from pathlib import Path

from .__about__ import __version__
from .core import Decision, evaluate, normalize_time, validate_work_key
from .report import render_html
from .store import EvidenceStore, Observation

SCHEMA_VERSION = "1.0"
EXIT_OK = 0
EXIT_INPUT = 1
EXIT_STALE = 2
EXIT_BLOCKED = 3


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(f"argument error: {message}")


def _db_path(value: str | None) -> Path:
    return Path(value or os.environ.get("OUTCOMELOCK_DB", ".outcomelock/state.db"))


def _safe(value: object) -> str:
    text = str(value)
    return "".join(
        character
        if not unicodedata.category(character).startswith("C")
        else f"\\u{ord(character):04x}"
        for character in text
    )


def _console(text: object, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    value = str(text)
    encoding = stream.encoding or "utf-8"
    stream.write(value.encode(encoding, errors="backslashreplace").decode(encoding) + "\n")


def _json(payload: dict) -> str:
    return json.dumps({"schema_version": SCHEMA_VERSION, **payload}, indent=2)


def _emit(decisions: list[Decision], as_json: bool) -> None:
    if as_json:
        print(_json({"decisions": [item.as_dict() for item in decisions]}))
        return
    markers = {"proceed": "[OK]", "suppress": "[SKIP]", "blocked": "[BLOCK]"}
    for item in decisions:
        _console(f"{markers[item.state]} {item.state.upper():8} {_safe(item.work_key)}")
        _console(f"  {_safe(item.reason)}")
        if item.fact:
            _console(
                f"  evidence: {_safe(item.fact.source)} | {_safe(item.fact.locator)} | "
                f"{_safe(item.fact.observed_at)}"
            )


def _exit_code(decisions: list[Decision]) -> int:
    if any(item.state == "blocked" for item in decisions):
        return EXIT_BLOCKED
    if any(item.state == "suppress" for item in decisions):
        return EXIT_STALE
    return EXIT_OK


def _require_document(payload: object, kind: str, collection: str) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get(collection), list):
        raise ValueError(f"{kind} must be a JSON object containing a {collection} array")
    allowed = {"schema_version", collection}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"{kind} has unknown top-level fields: {', '.join(sorted(unknown))}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{kind}.schema_version must equal {SCHEMA_VERSION}")
    return payload[collection]


def _load_plan(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    actions = _require_document(payload, "plan", "actions")
    allowed = {"id", "work_key", "title", "planned_at", "metadata"}
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f"actions[{index}] must be an object")
        unknown = set(action) - allowed
        if unknown:
            raise ValueError(f"actions[{index}] has unknown fields: {', '.join(sorted(unknown))}")
        if not isinstance(action.get("work_key"), str):
            raise ValueError(f"actions[{index}].work_key must be a string")
        validate_work_key(action["work_key"])
        for name in ("id", "title"):
            if name in action and not isinstance(action[name], str):
                raise ValueError(f"actions[{index}].{name} must be a string")
        if "metadata" in action and not isinstance(action["metadata"], dict):
            raise ValueError(f"actions[{index}].metadata must be an object")
        if "planned_at" in action:
            if not isinstance(action["planned_at"], str):
                raise ValueError(f"actions[{index}].planned_at must be a string")
            normalize_time(action["planned_at"])
    return actions


def _load_observations(path: str) -> list[Observation]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = _require_document(payload, "evidence", "observations")
    observations = []
    allowed = {"work_key", "verdict", "source", "locator", "summary", "observed_at", "confidence"}
    required = {"work_key", "verdict", "source", "locator", "summary"}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"observations[{index}] must be an object")
        if not required <= set(item):
            raise ValueError(f"observations[{index}] is missing required fields")
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(
                f"observations[{index}] has unknown fields: {', '.join(sorted(unknown))}"
            )
        for name in ("work_key", "verdict", "source", "locator", "summary"):
            if not isinstance(item[name], str):
                raise ValueError(f"observations[{index}].{name} must be a string")
        if "observed_at" in item and not isinstance(item["observed_at"], str):
            raise ValueError(f"observations[{index}].observed_at must be a string")
        if "confidence" in item and (
            isinstance(item["confidence"], bool) or not isinstance(item["confidence"], (int, float))
        ):
            raise ValueError(f"observations[{index}].confidence must be a number")
        observations.append(Observation(**item))
    return observations


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="outcomelock", description="Stop autonomous agents from redoing completed work."
    )
    parser.add_argument("--version", action="version", version=f"outcomelock {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize the local evidence store")
    init.add_argument("--db")

    record = sub.add_parser("assert", help="Record an auditable outcome assertion")
    record.add_argument("work_key")
    record.add_argument(
        "--verdict", required=True, choices=("completed", "superseded", "ambiguous")
    )
    record.add_argument("--source", required=True)
    record.add_argument("--locator", required=True)
    record.add_argument("--summary", required=True)
    record.add_argument("--observed-at")
    record.add_argument("--confidence", type=float, default=1.0)
    record.add_argument("--db")

    ingest = sub.add_parser("import", help="Atomically import assertions from a JSON evidence file")
    ingest.add_argument("evidence")
    ingest.add_argument("--db")

    status = sub.add_parser("status", help="Show local evidence-store counts")
    status.add_argument("--db")

    history = sub.add_parser("history", help="Show the evidence history for one work key")
    history.add_argument("work_key")
    history.add_argument("--json", action="store_true")
    history.add_argument("--db")

    check = sub.add_parser("check", help="Check one work key before acting")
    check.add_argument("work_key")
    check.add_argument("--planned-at")
    check.add_argument("--min-confidence", type=float, default=0.90)
    check.add_argument("--json", action="store_true")
    check.add_argument("--db")

    guard = sub.add_parser("guard", help="Evaluate every action in a JSON plan")
    guard.add_argument("plan")
    guard.add_argument("--min-confidence", type=float, default=0.90)
    guard.add_argument("--report", help="Write a standalone HTML report")
    guard.add_argument("--json", action="store_true")
    guard.add_argument("--db")

    demo = sub.add_parser("demo", help="Generate reusable evidence, plan, database, and report")
    demo.add_argument("--directory", default="outcomelock-demo")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "demo":
        directory = Path(args.directory)
        directory.mkdir(parents=True, exist_ok=True)
        db_path = directory / "state.db"
        db_path.unlink(missing_ok=True)
        observation = Observation(
            work_key="tax.ay2026-27.prefiling-documents",
            verdict="superseded",
            source="official-confirmation",
            locator="email:itr-everification-success",
            summary="Return submitted and e-verification succeeded; pre-filing work is obsolete.",
            observed_at="2026-07-19T12:00:00.900000+00:00",
        )
        store = EvidenceStore(db_path)
        store.record(observation)
        evidence = {
            "schema_version": SCHEMA_VERSION,
            "observations": [
                {
                    "work_key": observation.work_key,
                    "verdict": observation.verdict,
                    "source": observation.source,
                    "locator": observation.locator,
                    "summary": observation.summary,
                    "observed_at": observation.observed_at,
                    "confidence": observation.confidence,
                }
            ],
        }
        plan = {
            "schema_version": SCHEMA_VERSION,
            "actions": [
                {
                    "id": "task-1",
                    "work_key": "tax.ay2026-27.prefiling-documents",
                    "title": "Collect tax documents",
                    "planned_at": "2026-07-19T09:00:00.000000+00:00",
                },
                {
                    "id": "task-2",
                    "work_key": "website.measure.search-console",
                    "title": "Measure search traffic",
                    "planned_at": "2026-07-19T13:00:00.000000+00:00",
                },
            ],
        }
        evidence_path = directory / "evidence.json"
        plan_path = directory / "plan.json"
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        decisions = [
            evaluate(item["work_key"], store.facts(item["work_key"]), item.get("planned_at"))
            for item in plan["actions"]
        ]
        report = render_html(decisions, directory / "report.html", "OutcomeLock stale-work demo")
        _emit(decisions, False)
        _console(f"\nDemo written to {directory.resolve()}")
        _console(f"Open {report.resolve()}")
        return EXIT_OK

    store = EvidenceStore(_db_path(getattr(args, "db", None)))
    if args.command == "init":
        store.initialize()
        print(_json({"database": str(store.path.resolve()), "initialized": True}))
        return EXIT_OK
    if args.command == "assert":
        result = store.record_status(
            Observation(
                work_key=args.work_key,
                verdict=args.verdict,
                source=args.source,
                locator=args.locator,
                summary=args.summary,
                observed_at=args.observed_at,
                confidence=args.confidence,
            )
        )
        print(
            _json(
                {
                    "observation_id": result.observation_id,
                    "work_key": args.work_key,
                    "inserted": result.inserted,
                    "duplicate": not result.inserted,
                }
            )
        )
        return EXIT_OK
    if args.command == "import":
        observations = _load_observations(args.evidence)
        results = store.record_many(observations)
        print(
            _json(
                {
                    "processed": len(results),
                    "inserted": sum(item.inserted for item in results),
                    "duplicates": sum(not item.inserted for item in results),
                    "observation_ids": [item.observation_id for item in results],
                }
            )
        )
        return EXIT_OK

    if args.command == "status":
        store.require_initialized()
        print(
            _json(
                {
                    "database": str(store.path.resolve()),
                    "work_keys": store.work_key_count(),
                    "observations": store.count(),
                }
            )
        )
        return EXIT_OK
    if args.command == "history":
        store.require_initialized()
        facts = store.facts(args.work_key)
        if args.json:
            print(
                _json({"work_key": args.work_key, "observations": [vars(item) for item in facts]})
            )
        elif not facts:
            _console(f"No evidence for {_safe(args.work_key)}")
        else:
            for item in facts:
                _console(
                    f"{_safe(item.observed_at)}  {_safe(item.verdict.upper()):10} "
                    f"confidence={item.confidence:.2f}  {_safe(item.source)}"
                )
                _console(f"  {_safe(item.locator)} | {_safe(item.summary)}")
        return EXIT_OK
    if args.command == "check":
        store.require_initialized()
        decisions = [
            evaluate(
                args.work_key, store.facts(args.work_key), args.planned_at, args.min_confidence
            )
        ]
        _emit(decisions, args.json)
        return _exit_code(decisions)
    if args.command == "guard":
        actions = _load_plan(args.plan)
        store.require_initialized()
        decisions = [
            evaluate(
                item["work_key"],
                store.facts(item["work_key"]),
                item.get("planned_at"),
                args.min_confidence,
            )
            for item in actions
        ]
        if args.report:
            render_html(decisions, args.report)
        _emit(decisions, args.json)
        return _exit_code(decisions)
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return run(parser.parse_args(argv))
    except (ValueError, TypeError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        _console(f"outcomelock: error: {_safe(exc)}", error=True)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
