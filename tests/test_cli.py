import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from outcomelock.cli import main
from outcomelock.store import EvidenceStore, Observation


class OutcomeLockCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "state.db"
        self.plan = self.root / "plan.json"

    def tearDown(self):
        self.temp.cleanup()

    def call(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def write_plan(self, actions, **extra):
        payload = {"schema_version": "1.0", "actions": actions, **extra}
        self.plan.write_text(json.dumps(payload), encoding="utf-8")

    def test_assert_and_check_exit_stale(self):
        command = [
            "assert",
            "task.finished",
            "--verdict",
            "completed",
            "--source",
            "test",
            "--locator",
            "test:1",
            "--summary",
            "Done",
            "--observed-at",
            "2026-07-19T12:00:00.123456Z",
            "--db",
            str(self.db),
        ]
        code, output, _ = self.call(command)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["inserted"])
        code, duplicate, _ = self.call(command)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(duplicate)["duplicate"])
        code, output, _ = self.call(["check", "task.finished", "--db", str(self.db), "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["decisions"][0]["state"], "suppress")

    def test_missing_store_fails_closed_without_creating_file(self):
        code, _, error = self.call(["check", "task.unknown", "--db", str(self.db), "--json"])
        self.assertEqual(code, 1)
        self.assertIn("does not exist", error)
        self.assertFalse(self.db.exists())
        self.write_plan([{"work_key": "task.unknown"}])
        code, _, error = self.call(["guard", str(self.plan), "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("does not exist", error)
        self.assertFalse(self.db.exists())

    def test_argparse_errors_map_to_input_exit(self):
        code, _, error = self.call(["check"])
        self.assertEqual(code, 1)
        self.assertIn("argument error", error)
        code, _, error = self.call(
            [
                "assert",
                "task.finished",
                "--verdict",
                "completed",
                "--source",
                "x",
                "--locator",
                "x",
                "--summary",
                "x",
                "--confidence",
                "nope",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("argument error", error)

    def test_guard_mixed_plan_writes_escaped_report(self):
        EvidenceStore(self.db).record(
            Observation(
                "task.finished",
                "completed",
                "tracker",
                "</td><script>locator()</script>",
                "</td><script>alert(1)</script>",
                "2026-07-19T12:00:00.123456+00:00",
                1.0,
            )
        )
        self.write_plan(
            [
                {"work_key": "task.finished", "planned_at": "2026-07-19T09:00:00Z"},
                {"work_key": "task.new", "planned_at": "2026-07-19T13:00:00Z"},
            ]
        )
        report = self.root / "report.html"
        code, output, _ = self.call(
            ["guard", str(self.plan), "--db", str(self.db), "--report", str(report)]
        )
        self.assertEqual(code, 2)
        self.assertIn("SUPPRESS", output)
        content = report.read_text(encoding="utf-8")
        self.assertNotIn("<script>", content)
        self.assertIn("&lt;/td&gt;&lt;script&gt;", content)

    def test_blocked_takes_exit_precedence(self):
        EvidenceStore(self.db).record(
            Observation("task.unclear", "ambiguous", "test", "x", "Unknown")
        )
        self.write_plan([{"work_key": "task.unclear"}])
        code, _, _ = self.call(["guard", str(self.plan), "--db", str(self.db)])
        self.assertEqual(code, 3)

    def test_runtime_validation_matches_schema_types(self):
        self.write_plan([{"work_key": "task.new", "planned_at": 123}])
        code, _, error = self.call(["guard", str(self.plan), "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("planned_at must be a string", error)
        self.assertNotIn("Traceback", error)
        self.write_plan([{"work_key": "task.new", "metadata": []}])
        code, _, error = self.call(["guard", str(self.plan), "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("metadata must be an object", error)

    def test_unknown_plan_field_and_version_fail_closed(self):
        self.write_plan([{"work_key": "task.new", "execute": True}])
        code, _, error = self.call(["guard", str(self.plan), "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("unknown fields", error)
        self.plan.write_text(json.dumps({"actions": [{"work_key": "task.new"}]}), encoding="utf-8")
        code, _, error = self.call(["guard", str(self.plan), "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("schema_version", error)

    def test_malformed_json_returns_input_error(self):
        self.plan.write_text("{bad", encoding="utf-8")
        code, _, error = self.call(["guard", str(self.plan), "--db", str(self.db)])
        self.assertEqual(code, 1)
        self.assertIn("outcomelock: error", error)
        self.assertNotIn("Traceback", error)

    def test_import_is_atomic_idempotent_and_strict(self):
        evidence = self.root / "evidence.json"
        valid = {
            "work_key": "task.finished",
            "verdict": "completed",
            "source": "tracker",
            "locator": "row:1",
            "summary": "Done",
            "observed_at": "2026-07-19T12:00:00.123456Z",
            "confidence": 1.0,
        }
        payload = {"schema_version": "1.0", "observations": [valid]}
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        first, output, _ = self.call(["import", str(evidence), "--db", str(self.db)])
        second, duplicate_output, _ = self.call(["import", str(evidence), "--db", str(self.db)])
        self.assertEqual((first, second), (0, 0))
        self.assertEqual(json.loads(output)["inserted"], 1)
        self.assertEqual(json.loads(duplicate_output)["duplicates"], 1)
        self.assertEqual(EvidenceStore(self.db).count(), 1)

        atomic_db = self.root / "atomic.db"
        payload["observations"].append({**valid, "work_key": "Bad Key", "locator": "row:2"})
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        code, _, error = self.call(["import", str(evidence), "--db", str(atomic_db)])
        self.assertEqual(code, 1)
        self.assertIn("work_key", error)
        self.assertFalse(atomic_db.exists())

        payload = {"schema_version": "1.0", "observations": [{**valid, "source": 7}]}
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        code, _, error = self.call(["import", str(evidence), "--db", str(atomic_db)])
        self.assertEqual(code, 1)
        self.assertIn("source must be a string", error)
        self.assertNotIn("Traceback", error)

    def test_status_history_and_terminal_control_escaping(self):
        store = EvidenceStore(self.db)
        store.record(
            Observation("task.finished", "completed", "tracker\x1b[31m", "row\n1", "Done\r")
        )
        code, status_output, _ = self.call(["status", "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(status_output)["schema_version"], "1.0")
        code, output, _ = self.call(["history", "task.finished", "--db", str(self.db)])
        self.assertEqual(code, 0)
        self.assertIn("\\u001b", output)
        self.assertIn("\\u000a", output)
        self.assertNotIn("\x1b", output)

    def test_demo_generates_reusable_artifacts(self):
        directory = self.root / "demo"
        code, output, _ = self.call(["demo", "--directory", str(directory)])
        self.assertEqual(code, 0)
        for name in ("report.html", "plan.json", "evidence.json", "state.db"):
            self.assertTrue((directory / name).is_file())
        self.assertEqual(json.loads((directory / "plan.json").read_text())["schema_version"], "1.0")
        self.assertIn("SUPPRESS", output)


if __name__ == "__main__":
    unittest.main()
