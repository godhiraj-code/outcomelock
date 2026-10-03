import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from outcomelock.core import Fact, evaluate
from outcomelock.store import EvidenceStore, Observation


class OutcomeLockCoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "state.db"
        self.store = EvidenceStore(self.db)
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def record(
        self,
        verdict="completed",
        observed_at="2026-07-19T12:00:00+00:00",
        confidence=1.0,
        summary="Outcome satisfied",
        locator="tracker:1",
    ):
        return self.store.record(
            Observation(
                work_key="tax.ay2026-27.prefiling-documents",
                verdict=verdict,
                source="test",
                locator=locator,
                summary=summary,
                observed_at=observed_at,
                confidence=confidence,
            )
        )

    def decide(self, planned_at="2026-07-19T09:00:00+00:00"):
        key = "tax.ay2026-27.prefiling-documents"
        return evaluate(key, self.store.facts(key), planned_at)

    def test_no_evidence_proceeds(self):
        self.assertEqual(self.decide().state, "proceed")

    def test_readonly_store_with_uri_reserved_characters(self):
        path = Path(self.temp.name) / "state #100%.db"
        store = EvidenceStore(path)
        store.record(Observation("task.done", "completed", "test", "local", "Done"))
        assert store.count() == 1
        assert store.facts("task.done")[0].summary == "Done"
        connection = store.connect(readonly=True)
        try:
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            connection.close()

    def test_newer_completion_suppresses_stale_plan(self):
        self.record()
        decision = self.decide()
        self.assertEqual(decision.state, "suppress")
        self.assertIn("newer than the plan", decision.reason)

    def test_terminal_assertion_is_monotonic_for_later_plan(self):
        self.record(observed_at="2026-07-18T12:00:00+00:00")
        decision = self.decide(planned_at="2026-07-20T09:00:00+00:00")
        self.assertEqual(decision.state, "suppress")
        self.assertIn("monotonic", decision.reason)

    def test_superseded_suppresses(self):
        self.record(verdict="superseded")
        self.assertEqual(self.decide().state, "suppress")

    def test_ambiguous_blocks(self):
        self.record(verdict="ambiguous")
        self.assertEqual(self.decide().state, "blocked")

    def test_weak_terminal_evidence_blocks(self):
        self.record(confidence=0.50)
        self.assertEqual(self.decide().state, "blocked")

    def test_equal_timestamp_contradiction_blocks(self):
        self.record(verdict="completed", summary="Complete", locator="source:a")
        self.record(verdict="ambiguous", summary="Conflicting", locator="source:b")
        self.assertEqual(self.decide().state, "blocked")

    def test_equal_timestamp_uses_weakest_confidence(self):
        self.record(confidence=1.0, summary="Strong", locator="source:a")
        self.record(confidence=0.5, summary="Weak", locator="source:b")
        decision = self.decide()
        self.assertEqual(decision.state, "blocked")
        self.assertEqual(decision.fact.confidence, 0.5)

    def test_direct_api_rejects_malformed_fact(self):
        malformed = Fact(
            1,
            "different.work.key",
            "invented",
            "test",
            "x",
            "x",
            "2026-07-19T12:00:00+00:00",
            1.0,
            "hash",
        )
        with self.assertRaisesRegex(ValueError, "work_key"):
            evaluate("requested.work.key", [malformed])
        malformed_verdict = Fact(
            2,
            "requested.work.key",
            "invented",
            "test",
            "x",
            "x",
            "2026-07-19T12:00:00+00:00",
            1.0,
            "hash",
        )
        with self.assertRaisesRegex(ValueError, "verdict"):
            evaluate("requested.work.key", [malformed_verdict])

    def test_newer_ambiguity_supersedes_older_completion(self):
        self.record(observed_at="2026-07-18T12:00:00+00:00")
        self.record(
            verdict="ambiguous", observed_at="2026-07-19T13:00:00+00:00", locator="source:new"
        )
        self.assertEqual(self.decide().state, "blocked")

    def test_microseconds_preserve_newest_evidence_order(self):
        self.record(
            verdict="ambiguous",
            observed_at="2026-07-19T12:00:00.100000Z",
            locator="source:early",
        )
        self.record(
            verdict="completed",
            observed_at="2026-07-19T12:00:00.900000Z",
            locator="source:late",
        )
        decision = self.decide()
        self.assertEqual(decision.state, "suppress")
        self.assertEqual(decision.fact.verdict, "completed")
        self.assertIn(".900000", decision.fact.observed_at)

    def test_corrected_timestamp_or_confidence_is_not_discarded(self):
        first = self.record(
            confidence=0.2,
            observed_at="2026-07-19T10:00:00Z",
            locator="source:correctable",
        )
        second = self.record(
            confidence=1.0,
            observed_at="2026-07-19T11:00:00Z",
            locator="source:correctable",
        )
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.decide().fact.confidence, 1.0)

    def test_recording_is_idempotent(self):
        first = self.record()
        second = self.record()
        self.assertEqual(first, second)
        self.assertEqual(self.store.count(), 1)

    def test_concurrent_duplicate_recording_has_one_row(self):
        observation = Observation(
            "task.concurrent",
            "completed",
            "tracker",
            "row:1",
            "Done",
            "2026-07-19T12:00:00+00:00",
            1.0,
        )
        with ThreadPoolExecutor(max_workers=12) as pool:
            ids = list(pool.map(lambda _: self.store.record(observation), range(24)))
        self.assertEqual(set(ids), {1})
        self.assertEqual(self.store.count(), 1)

    def test_in_memory_store_persists_across_calls(self):
        store = EvidenceStore(":memory:")
        store.record(Observation("task.memory", "completed", "test", "row:1", "Done"))
        self.assertEqual(store.count(), 1)
        self.assertEqual(store.facts("task.memory")[0].verdict, "completed")

    def test_invalid_work_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "work_key"):
            self.store.record(Observation("Bad Key", "completed", "test", "x", "x"))

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.record(observed_at="2026-07-19T12:00:00")

    def test_invalid_confidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            self.record(confidence=1.1)


if __name__ == "__main__":
    unittest.main()
