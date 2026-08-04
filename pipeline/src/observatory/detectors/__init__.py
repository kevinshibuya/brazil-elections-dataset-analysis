"""The detector registry.

    from observatory import detectors
    detectors.all()                    # every detector, in run order
    detectors.run(db, only=["t1_arithmetic"])

REGISTRATION IS EXPLICIT
    :data:`REGISTRY` is a hand-written tuple, not a scan of the package.  A
    detector that nobody added does not silently start scoring the country,
    the order the dashboard shows them in is the order written here, and the
    diff of a slice that adds a detector says so in one line.  Every detector
    slice therefore lists THIS FILE among the files it touches.

    To add one: write ``detectors/<name>.py`` with a :class:`~.base.BaseDetector`
    subclass, import it below, and append an instance to :data:`REGISTRY`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from ..db import Database
from . import extreme_dominance, fingerprint, split_ticket, tier1, turnout
from .base import (
    CROSS_RACE,
    BaseDetector,
    Detector,
    DetectorResult,
    DetectorRun,
    ScoreWriter,
    ensure_analysis_schema,
    read_scores,
    read_state_payloads,
    read_summary,
    score_from_p,
    summary_of,
)

__all__ = [
    "CROSS_RACE",
    "BaseDetector",
    "Detector",
    "DetectorResult",
    "DetectorRun",
    "ScoreWriter",
    "REGISTRY",
    "all",
    "ensure_analysis_schema",
    "get",
    "names",
    "read_scores",
    "read_state_payloads",
    "read_summary",
    "run",
    "score_from_p",
    "select",
    "summary_of",
]

#: Every detector, in the order a full analysis run executes them.  Tier 1
#: first: if a section's numbers do not add up, that is the finding, and the
#: statistical tiers are commentary on top of it.
REGISTRY: tuple[Detector, ...] = (
    tier1.Arithmetic(),
    tier1.CrossRacePresence(),
    extreme_dominance.ExtremeDominance(),
    split_ticket.SplitTicket(),
    turnout.Turnout(),
    fingerprint.Fingerprint(),
)


def all() -> tuple[Detector, ...]:  # noqa: A001 - the contracted name
    """Every registered detector, in run order."""
    return REGISTRY


def names() -> tuple[str, ...]:
    return tuple(d.name for d in REGISTRY)


def get(name: str) -> Detector:
    for detector in REGISTRY:
        if detector.name == name:
            return detector
    raise KeyError(f"unknown detector {name!r}; registered: {', '.join(names())}")


def select(only: Iterable[str] | None = None) -> tuple[Detector, ...]:
    """The detectors ``only`` names, in REGISTRY order.  ``None`` means all.

    Raises :class:`KeyError` on an unknown name rather than quietly running
    nothing -- a typo in ``--only`` must not look like a clean run.
    """
    if only is None:
        return REGISTRY
    wanted = list(only)
    for name in wanted:
        get(name)
    return tuple(d for d in REGISTRY if d.name in set(wanted))


def run(
    db: Database,
    *,
    only: Iterable[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, DetectorRun]:
    """Run detectors and return one :class:`DetectorRun` each, by name.

    Each detector is atomic on its own (see :meth:`BaseDetector.run`); the run
    as a whole is not, deliberately.  One failing detector must not discard
    the scores of the ones that already succeeded -- they are independent
    measurements, not a single transaction.
    """
    ensure_analysis_schema(db)
    out: dict[str, DetectorRun] = {}
    for detector in select(only):
        started = time.perf_counter()
        if log:
            log(f"  {detector.name} ...")
        detector.run(db)
        record = (
            detector.last_run(db)
            if isinstance(detector, BaseDetector)
            else DetectorRun(detector.name, 0, 0, 0, 0,
                             time.perf_counter() - started, DetectorResult({}))
        )
        out[detector.name] = record
        if log:
            log(f"  {record}")
    return out
