"""The detector framework: how every anomaly detector is written and stored.

    from observatory import ingest
    from observatory import detectors

    detectors.run(ingest.connect())              # every registered detector
    detectors.run(db, only=["t1_arithmetic"])    # a subset

WHAT A DETECTOR IS
    A named, idempotent function from the warehouse to a score per
    ``(section, race, round)``, plus a national summary.  Subclass
    :class:`BaseDetector`, set ``name``, implement ``compute``::

        class Turnout(BaseDetector):
            name = "t2_turnout"

            def compute(self, db, scores):
                scores.insert_select("SELECT section_id, race, round, ...")
                return DetectorResult({"median": ...})

    ``run()`` is provided and is what everything calls.  It wraps one
    transaction: the detector's previous rows are deleted, ``compute`` writes
    the new ones through ``scores``, and the summary and state payloads are
    stamped.  A detector that raises leaves the previous run's scores intact.

THE SCORE CONVENTION IS BINDING
    **Higher = more anomalous**, always, for every detector, so the composite
    (Task 18) can combine them without a per-detector sign table.  Zero means
    "looked at it, nothing to say" -- clean sections are scored 0 rather than
    left out, so ``sections_scored`` means what it says and the dashboard can
    tell "clean" from "not examined".

    Probabilistic detectors store ``score = -log10(max(p, 1e-16))``; use
    :func:`score_from_p`.  Everything else documents its scale in ``params``.

RACE / ROUND IS BINDING TOO
    A race-specific detector writes its real ``(race, round)``.  A cross-race
    detector writes ``race = 'ALL'`` (:data:`CROSS_RACE`) with a **real**
    round -- never a fake round.  Both go in the same table; the primary key
    ``(detector, section_id, race, round)`` keeps them apart.

TWO WAYS TO WRITE SCORES, PICK BY WHERE THE MATH LIVES
    * :meth:`ScoreWriter.insert_select` -- the whole detector is one SELECT.
      Nothing leaves the server.  Tier 1 is written this way, and any detector
      that can be is much cheaper at national scale (~2.8 M score rows).
    * :meth:`ScoreWriter.add_frame` / :meth:`ScoreWriter.add` -- the math needs
      Python (scipy, polars).  Rows are buffered and COPYed in batches, so a
      detector may stream a state at a time without holding Brazil in memory.

STATE PAYLOADS
    Some detectors compute a per-UF artifact the dashboard needs but that is
    not a section score: a fitted curve, a digit histogram, a baseline.
    Return them as ``DetectorResult(summary, state_payloads={"RR": {...}})``
    and the framework persists them to ``analysis.state_payloads``.  The
    exporter reads that table generically, so a detector that uses this
    mechanism needs no exporter change.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

import polars as pl

from ..db import ANALYSIS_DDL, Database

__all__ = [
    "CROSS_RACE",
    "P_FLOOR",
    "SCORES_TABLE",
    "SUMMARY_TABLE",
    "STATE_PAYLOADS_TABLE",
    "BaseDetector",
    "Detector",
    "DetectorResult",
    "ScoreWriter",
    "ensure_analysis_schema",
    "read_scores",
    "read_state_payloads",
    "read_summary",
    "score_from_p",
    "summary_of",
]

SCORES_TABLE = "analysis.section_scores"
SUMMARY_TABLE = "analysis.detector_summary"
STATE_PAYLOADS_TABLE = "analysis.state_payloads"

#: The ``race`` value a cross-race detector writes.
CROSS_RACE = "ALL"

#: Smallest p a score may be derived from -- below this, floating point is
#: telling us about itself rather than about the election.
P_FLOOR = 1e-16

#: Columns a detector's SELECT (or frame) must produce, in this order.
SCORE_COLUMNS: tuple[str, ...] = ("section_id", "race", "round", "score", "detail")

#: Rows buffered before a COPY.  Big enough that the round trip disappears,
#: small enough that a national detector never holds more than a few hundred
#: MB of pending scores.
DEFAULT_BATCH_ROWS = 200_000


def score_from_p(p: float) -> float:
    """``-log10(max(p, 1e-16))`` -- the shared scale for probabilistic detectors.

    p = 1 -> 0, p = 1e-4 -> 4, p = 0 -> 16.  Monotone the right way round:
    less probable is more anomalous is a higher score.
    """
    return -math.log10(max(float(p), P_FLOOR))


def ensure_analysis_schema(db: Database) -> None:
    """Create ``analysis.*`` if it is not there.  Idempotent and cheap."""
    db.executemany_sql(ANALYSIS_DDL)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectorResult:
    """What a detector says about the country, on top of its section scores.

    ``summary`` is free-form JSON: whatever a reader needs to judge the
    detector's own output (baselines, tails, thresholds, the top of the ranked
    list).  A detector whose whole output is section scores returns
    ``DetectorResult({})``.

    ``state_payloads`` maps UF -> arbitrary JSON, persisted to
    ``analysis.state_payloads`` by the framework.
    """

    summary: dict[str, Any] = field(default_factory=dict)
    state_payloads: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class DetectorRun:
    """Bookkeeping about one execution -- what ``detectors.run`` prints."""

    name: str
    rows_scored: int
    rows_flagged: int
    sections_scored: int
    sections_flagged: int
    elapsed_s: float
    result: DetectorResult

    def __str__(self) -> str:
        return (
            f"{self.name}: {self.sections_scored:,} sections scored, "
            f"{self.sections_flagged:,} flagged "
            f"({self.rows_scored:,} rows) [{self.elapsed_s:.1f}s]"
        )


# --------------------------------------------------------------------------
# Writing scores
# --------------------------------------------------------------------------


@dataclass
class ScoreWriter:
    """The only way a detector writes to ``analysis.section_scores``.

    Owns the detector column, the batching and the JSON encoding, so no
    detector has to hand-write its own name into SQL (and so no detector can
    write another's rows).
    """

    db: Database
    detector: str
    batch_rows: int = DEFAULT_BATCH_ROWS
    rows_written: int = 0
    _pending: list[pl.DataFrame] = field(default_factory=list, repr=False)
    _pending_rows: int = 0

    # -- server-side -------------------------------------------------------

    def insert_select(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """``INSERT ... SELECT`` straight from a detector's SQL.

        ``sql`` must SELECT exactly :data:`SCORE_COLUMNS` in that order --
        ``section_id, race, round, score, detail`` -- and nothing else; the
        detector name is bound here, not written by the caller.  Returns the
        number of rows inserted.
        """
        statement = (
            f"INSERT INTO {SCORES_TABLE} (detector, {', '.join(SCORE_COLUMNS)}) "
            f"SELECT %s, sub.* FROM ({sql}) sub"
        )
        n = self.db.execute(statement, (self.detector, *(params or ())))
        self.rows_written += n
        return n

    # -- python-side -------------------------------------------------------

    def add(
        self,
        section_id: int,
        race: str,
        round: int,
        score: float,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        """Buffer one score row.  Fine for tens of thousands; use
        :meth:`add_frame` for millions."""
        self.add_frame(
            pl.DataFrame(
                {
                    "section_id": [int(section_id)],
                    "race": [race],
                    "round": [int(round)],
                    "score": [float(score)],
                    "detail": [None if detail is None else json.dumps(detail)],
                },
                schema={
                    "section_id": pl.Int64,
                    "race": pl.String,
                    "round": pl.Int16,
                    "score": pl.Float64,
                    "detail": pl.String,
                },
            )
        )

    def add_frame(self, frame: pl.DataFrame) -> None:
        """Buffer a frame of score rows.

        Needs ``section_id, race, round, score``; ``detail`` is optional and
        may be a JSON string column or a struct column (encoded here).
        """
        if frame.height == 0:
            return
        self._pending.append(_normalise(frame))
        self._pending_rows += frame.height
        if self._pending_rows >= self.batch_rows:
            self.flush()

    def flush(self) -> int:
        """COPY everything buffered.  Called by ``run`` at the end anyway."""
        if not self._pending:
            return 0
        frame = pl.concat(self._pending, how="vertical")
        self._pending.clear()
        self._pending_rows = 0
        frame = frame.with_columns(pl.lit(self.detector).alias("detector")).select(
            ["detector", *SCORE_COLUMNS]
        )
        n = self.db.copy_frame(SCORES_TABLE, frame)
        self.rows_written += n
        return n


def _normalise(frame: pl.DataFrame) -> pl.DataFrame:
    missing = [c for c in SCORE_COLUMNS[:-1] if c not in frame.columns]
    if missing:
        raise ValueError(f"score frame is missing {missing}; needs {SCORE_COLUMNS}")
    if "detail" not in frame.columns:
        frame = frame.with_columns(pl.lit(None, dtype=pl.String).alias("detail"))
    detail = frame.schema["detail"]
    if isinstance(detail, pl.Struct):
        frame = frame.with_columns(pl.col("detail").struct.json_encode())
    elif detail != pl.String and detail != pl.Null:
        raise TypeError(f"detail must be JSON text or a struct, got {detail}")
    return frame.select(
        pl.col("section_id").cast(pl.Int64),
        pl.col("race").cast(pl.String),
        pl.col("round").cast(pl.Int16),
        pl.col("score").cast(pl.Float64),
        pl.col("detail").cast(pl.String),
    )


# --------------------------------------------------------------------------
# The detector itself
# --------------------------------------------------------------------------


@runtime_checkable
class Detector(Protocol):
    """What ``detectors.all()`` holds and ``03_analyze.py`` runs."""

    name: str

    def run(self, db: Database) -> DetectorResult: ...


class BaseDetector:
    """Implements :class:`Detector`; subclasses implement :meth:`compute`.

    Class attributes a subclass may set:

    ``name``            the detector id, unique, stable -- it is a primary key
                        in three tables and an id in the exported shards.
    ``flag_threshold``  score at or above which a section counts as *flagged*
                        in ``detector_summary``.  1.0 by default (Tier 1: one
                        violated rule).  A probabilistic detector should set
                        the score matching its own alarm level, e.g. 4.0 for
                        p < 1e-4.
    """

    name: ClassVar[str] = ""
    flag_threshold: ClassVar[float] = 1.0
    batch_rows: ClassVar[int] = DEFAULT_BATCH_ROWS

    #: Summary keys the framework fills in; a detector must not use them.
    RESERVED_SUMMARY_KEYS: ClassVar[tuple[str, ...]] = (
        "rows_scored",
        "rows_flagged",
        "elapsed_s",
    )

    def params(self) -> dict[str, Any]:
        """Tunables, persisted to ``detector_summary.params``.

        Override to record anything a reader would need to reproduce the run.
        """
        return {}

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        """Score every section.  Subclass responsibility."""
        raise NotImplementedError

    # -- the part nobody overrides ----------------------------------------

    def run(self, db: Database) -> DetectorResult:
        if not self.name:
            raise ValueError(f"{type(self).__name__} has no name")
        ensure_analysis_schema(db)
        started = time.perf_counter()
        writer = ScoreWriter(db, self.name, batch_rows=self.batch_rows)
        with db.transaction():
            db.execute(f"DELETE FROM {SCORES_TABLE} WHERE detector = %s", (self.name,))
            db.execute(
                f"DELETE FROM {STATE_PAYLOADS_TABLE} WHERE detector = %s", (self.name,)
            )
            result = self.compute(db, writer) or DetectorResult({})
            writer.flush()
            elapsed = time.perf_counter() - started
            counts = self._counts(db)
            self._write_summary(db, result, counts, elapsed)
            self._write_state_payloads(db, result)
        return result

    def last_run(self, db: Database) -> DetectorRun:
        """Bookkeeping for the run just finished (read back from the summary)."""
        row = db.fetch(
            f"SELECT sections_scored, sections_flagged, summary FROM {SUMMARY_TABLE} "
            "WHERE detector = %s",
            (self.name,),
        )
        if not row:
            return DetectorRun(self.name, 0, 0, 0, 0, 0.0, DetectorResult({}))
        sections_scored, sections_flagged, summary = row[0]
        summary = dict(summary or {})
        return DetectorRun(
            name=self.name,
            rows_scored=int(summary.get("rows_scored", 0)),
            rows_flagged=int(summary.get("rows_flagged", 0)),
            sections_scored=int(sections_scored or 0),
            sections_flagged=int(sections_flagged or 0),
            elapsed_s=float(summary.get("elapsed_s", 0.0)),
            result=DetectorResult(summary),
        )

    # -- internals ---------------------------------------------------------

    def _counts(self, db: Database) -> tuple[int, int, int, int]:
        rows, flagged, sections, flagged_sections = db.fetch(
            f"""
            SELECT count(*),
                   count(*) FILTER (WHERE score >= %s),
                   count(DISTINCT section_id),
                   count(DISTINCT section_id) FILTER (WHERE score >= %s)
            FROM {SCORES_TABLE} WHERE detector = %s
            """,
            (self.flag_threshold, self.flag_threshold, self.name),
        )[0]
        return int(rows), int(flagged), int(sections), int(flagged_sections)

    def _write_summary(
        self,
        db: Database,
        result: DetectorResult,
        counts: tuple[int, int, int, int],
        elapsed: float,
    ) -> None:
        rows, rows_flagged, sections, sections_flagged = counts
        clash = set(result.summary) & set(self.RESERVED_SUMMARY_KEYS)
        if clash:
            raise ValueError(
                f"{self.name}: summary keys {sorted(clash)} are reserved by the framework"
            )
        summary = {
            **result.summary,
            "rows_scored": rows,
            "rows_flagged": rows_flagged,
            "elapsed_s": round(elapsed, 3),
        }
        params = {"flag_threshold": self.flag_threshold, **self.params()}
        db.execute(
            f"""
            INSERT INTO {SUMMARY_TABLE}
                (detector, sections_scored, sections_flagged, params, summary, at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (detector) DO UPDATE SET
                sections_scored = EXCLUDED.sections_scored,
                sections_flagged = EXCLUDED.sections_flagged,
                params = EXCLUDED.params,
                summary = EXCLUDED.summary,
                at = EXCLUDED.at
            """,
            (self.name, sections, sections_flagged, json.dumps(params),
             json.dumps(summary, default=str)),
        )

    def _write_state_payloads(self, db: Database, result: DetectorResult) -> None:
        for uf, payload in (result.state_payloads or {}).items():
            db.execute(
                f"INSERT INTO {STATE_PAYLOADS_TABLE} (detector, uf, payload) "
                "VALUES (%s, %s, %s)",
                (self.name, uf, json.dumps(payload, default=str)),
            )


# --------------------------------------------------------------------------
# Read side (acceptance tests, 03_analyze.py, the exporter)
# --------------------------------------------------------------------------


def read_scores(db: Database, detector: str) -> pl.DataFrame:
    """One detector's section scores, in section order.

    ``detail`` comes back as a polars **struct**, which is what makes
    ``scores["detail"].struct.field("baseline")`` work.  The price is a
    requirement on detectors: **every row of one detector must carry the same
    detail keys.**  Polars infers the struct schema from the first rows and
    silently drops keys that appear only later, so a detector that emits
    ``{"a": 1}`` for some sections and ``{"b": 2}`` for others loses data
    here.  Emit the full key set every time, with nulls where a field does not
    apply (Tier 1 does exactly that: ``rules`` is ``[]``, never absent).
    """
    ensure_analysis_schema(db)
    return db.fetch_frame(
        f"SELECT section_id, race, round, score, detail FROM {SCORES_TABLE} "
        "WHERE detector = %s ORDER BY section_id, race, round",
        (detector,),
    )


def read_summary(db: Database, detector: str | None = None) -> pl.DataFrame:
    """``analysis.detector_summary``, optionally for one detector.

    ``params`` and ``summary`` come back as JSON **text**, not structs, and
    the caller decodes with :func:`json.loads`.  Detectors deliberately have
    nothing in common in these two columns, so a struct column would take its
    schema from whichever detector sorted first and quietly drop every other
    detector's keys -- which is exactly what a summary must not do.
    """
    ensure_analysis_schema(db)
    where = "WHERE detector = %s" if detector else ""
    return db.fetch_frame(
        f"SELECT detector, sections_scored, sections_flagged, "
        f"params::TEXT AS params, summary::TEXT AS summary, at "
        f"FROM {SUMMARY_TABLE} {where} ORDER BY detector",
        (detector,) if detector else None,
    )


def summary_of(db: Database, detector: str) -> dict[str, Any]:
    """One detector's stored summary, decoded.  ``{}`` if it never ran."""
    row = db.fetch(
        f"SELECT summary FROM {SUMMARY_TABLE} WHERE detector = %s", (detector,)
    )
    return dict(row[0][0] or {}) if row else {}


def read_state_payloads(db: Database, detector: str) -> pl.DataFrame:
    """One detector's per-UF payloads, in UF order.

    ``payload`` is JSON text, for the same reason ``read_summary`` returns
    text: payloads are per-detector shapes with nothing in common.
    """
    ensure_analysis_schema(db)
    return db.fetch_frame(
        f"SELECT uf, payload::TEXT AS payload FROM {STATE_PAYLOADS_TABLE} "
        "WHERE detector = %s ORDER BY uf",
        (detector,),
    )
