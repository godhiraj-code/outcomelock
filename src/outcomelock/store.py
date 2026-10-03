from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .core import Fact, VERDICTS, content_hash, normalize_time, validate_work_key

SCHEMA_VERSION = "1.0"
SCHEMA = f"""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version','{SCHEMA_VERSION}');
CREATE TABLE IF NOT EXISTS observations(
  id INTEGER PRIMARY KEY,
  work_key TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('completed','superseded','ambiguous')),
  source TEXT NOT NULL,
  locator TEXT NOT NULL,
  summary TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(work_key,source,locator,content_hash)
);
CREATE INDEX IF NOT EXISTS observations_key_time ON observations(work_key,observed_at,id);
"""


@dataclass(frozen=True)
class Observation:
    work_key: str
    verdict: str
    source: str
    locator: str
    summary: str
    observed_at: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True)
class RecordResult:
    observation_id: int
    inserted: bool


@dataclass(frozen=True)
class _PreparedObservation:
    work_key: str
    verdict: str
    source: str
    locator: str
    summary: str
    observed_at: str
    confidence: float
    content_hash: str
    created_at: str


class EvidenceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._memory_db: sqlite3.Connection | None = None

    @property
    def is_memory(self) -> bool:
        return str(self.path) == ":memory:"

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if self.is_memory:
            if self._memory_db is None:
                self._memory_db = sqlite3.connect(":memory:", check_same_thread=False)
                self._memory_db.row_factory = sqlite3.Row
                self._memory_db.execute("PRAGMA foreign_keys=ON")
            return self._memory_db
        if readonly:
            if not self.path.is_file():
                raise ValueError(f"evidence store does not exist: {self.path}")
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            db = sqlite3.connect(uri, uri=True)
            db.execute("PRAGMA query_only=ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _close(self, db: sqlite3.Connection) -> None:
        if not self.is_memory:
            db.close()

    def initialize(self) -> None:
        db = self.connect()
        try:
            db.executescript(SCHEMA)
            db.commit()
        finally:
            self._close(db)

    def require_initialized(self) -> None:
        db = self.connect(readonly=True)
        try:
            row = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if row is None:
                raise ValueError(f"not an initialized OutcomeLock evidence store: {self.path}")
            if row["value"] != SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported evidence-store schema {row['value']}; expected {SCHEMA_VERSION}"
                )
            db.execute("SELECT 1 FROM observations LIMIT 1").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"not an initialized OutcomeLock evidence store: {self.path}") from exc
        finally:
            self._close(db)

    @staticmethod
    def _prepare(observation: Observation) -> _PreparedObservation:
        if not isinstance(observation.work_key, str):
            raise ValueError("work_key must be a string")
        work_key = validate_work_key(observation.work_key)
        if not isinstance(observation.verdict, str) or observation.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of: {', '.join(sorted(VERDICTS))}")
        text_fields = {
            "source": observation.source,
            "locator": observation.locator,
            "summary": observation.summary,
        }
        for name, value in text_fields.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if isinstance(observation.confidence, bool) or not isinstance(
            observation.confidence, (int, float)
        ):
            raise ValueError("confidence must be a number between 0 and 1")
        confidence = float(observation.confidence)
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if observation.observed_at is not None and not isinstance(observation.observed_at, str):
            raise ValueError("observed_at must be a timezone-aware string")
        observed_at = normalize_time(observation.observed_at)
        digest = content_hash(observation.verdict, observation.summary, observed_at, confidence)
        return _PreparedObservation(
            work_key,
            observation.verdict,
            observation.source,
            observation.locator,
            observation.summary,
            observed_at,
            confidence,
            digest,
            normalize_time(None),
        )

    @staticmethod
    def _insert(db: sqlite3.Connection, item: _PreparedObservation) -> RecordResult:
        cursor = db.execute(
            """INSERT OR IGNORE INTO observations
            (work_key,verdict,source,locator,summary,observed_at,confidence,content_hash,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                item.work_key,
                item.verdict,
                item.source,
                item.locator,
                item.summary,
                item.observed_at,
                item.confidence,
                item.content_hash,
                item.created_at,
            ),
        )
        row = db.execute(
            """SELECT id FROM observations
            WHERE work_key=? AND source=? AND locator=? AND content_hash=?""",
            (item.work_key, item.source, item.locator, item.content_hash),
        ).fetchone()
        return RecordResult(int(row["id"]), cursor.rowcount == 1)

    def record_status(self, observation: Observation) -> RecordResult:
        prepared = self._prepare(observation)
        self.initialize()
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            result = self._insert(db, prepared)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            self._close(db)

    def record(self, observation: Observation) -> int:
        return self.record_status(observation).observation_id

    def record_many(self, observations: list[Observation]) -> list[RecordResult]:
        prepared = [self._prepare(item) for item in observations]
        self.initialize()
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            results = [self._insert(db, item) for item in prepared]
            db.commit()
            return results
        except Exception:
            db.rollback()
            raise
        finally:
            self._close(db)

    def facts(self, work_key: str) -> list[Fact]:
        validate_work_key(work_key)
        self.require_initialized()
        db = self.connect(readonly=True)
        try:
            rows = db.execute(
                """SELECT id,work_key,verdict,source,locator,summary,observed_at,
                confidence,content_hash FROM observations
                WHERE work_key=? ORDER BY observed_at DESC,id DESC""",
                (work_key,),
            ).fetchall()
            return [Fact(**dict(row)) for row in rows]
        finally:
            self._close(db)

    def count(self) -> int:
        self.require_initialized()
        db = self.connect(readonly=True)
        try:
            return int(db.execute("SELECT count(*) FROM observations").fetchone()[0])
        finally:
            self._close(db)

    def work_key_count(self) -> int:
        self.require_initialized()
        db = self.connect(readonly=True)
        try:
            return int(
                db.execute("SELECT count(DISTINCT work_key) FROM observations").fetchone()[0]
            )
        finally:
            self._close(db)
