"""Tier 2 -- digit tests: the second digit against Benford, the last against chance.

THE QUESTION
    A vote count is not a measurement, it is a tally, and the digits of a tally
    made by machine have no reason to prefer one shape over another.  Two
    classical screens exploit that:

      * **second-digit Benford (2BL)**.  Counts that span several orders of
        magnitude have a log-uniform significand, so the SECOND significant
        digit follows

            P(d) = sum_{f=1..9} log10(1 + 1/(10 f + d))

        a gently decreasing law from 0.1197 at d = 0 to 0.0850 at d = 9.
        Hand-composed numbers do not reproduce it.
      * **last-digit uniformity**.  The final digit of a real tally carries no
        information at all and must be uniform over 0..9.  Invented or rounded
        numbers pile up on 0 and 5 -- the single most robust digit signature
        there is, because unlike Benford it needs no assumption about the
        distribution of the counts themselves.

    Both are chi-square goodness-of-fit tests on 10 bins, 9 degrees of freedom.

WHAT IS COUNTED
    Section-level nominal (``votavel_kind = 'candidate'``) PRESIDENTIAL vote
    counts, BOTH rounds, for the TOP TWO candidates of each round nationally.
    The top two are where essentially all the mass is, and restricting to them
    keeps the tails (a candidate with 3 votes a section) out of a test whose
    whole premise is that the counts are big enough to have digits.

    Second digit: counts >= 10 (a one-digit count has no second digit).
    Last digit:   counts >= 100.  A two-digit count's last digit is also its
    second, so pooling them would test the same digit twice under two
    different null hypotheses.

STRATA
    State x municipality-size bucket, size measured in SECTIONS::

        small  < 20 sections
        mid    20..99
        large  >= 100

    Digit laws are properties of a POPULATION of counts, not of a section, and
    the population that matters is one whose scale is homogeneous: pooling a
    20-section hamlet with the capital mixes counts of 30 with counts of 800
    and manufactures a Benford-looking spread out of nothing.  The stratum is
    therefore the smallest unit that is both homogeneous and big enough to
    test.

SPARSE STRATA ARE SKIPPED, NOT FUDGED (binding)
    A chi-square is computed only when the stratum has at least
    :data:`MIN_STRATUM_N` eligible counts AND every expected bin count is at
    least :data:`MIN_EXPECTED`.  Otherwise the stratum is emitted as
    ``{test, n, skipped: true, reason}`` -- with NO ``p`` key at all.  Not
    ``None``, not NaN: a p-value that does not exist must be absent, because a
    downstream reader that sees ``p = NaN`` will sooner or later compare it to
    a threshold and get ``False``, which is indistinguishable from "tested,
    clean".  (NaN cannot survive the JSON round trip through
    ``analysis.state_payloads`` either.)

NO PER-SECTION SCORES
    This is a screening statistic over a population.  One section's vote count
    has one second digit; there is nothing to test and nothing honest to write
    into ``section_scores``.  ``sections_scored`` is 0 and the composite
    ignores this detector.

WHAT THE NUMBERS DO NOT MEAN
    Deckert, Myagkov & Ordeshook (2011) and Mebane's replies are the standing
    caveat: 2BL is not a fraud test, and both false positives (heterogeneous
    turnout produces deviations on its own) and false negatives (fraud that
    preserves digit structure) are documented.  That prose is Task 22's; this
    slice exports numbers only.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

import polars as pl
from scipy.stats import chi2

from ..db import Database
from ..export.shards import score_key
from .base import (
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    read_scores as _read_scores,
    read_state_payloads,
)

__all__ = [
    "BENFORD_SECOND",
    "Digits",
    "UNIFORM",
    "bucket_of",
    "digit_test",
    "read_scores",
    "read_strata",
]

DETECTOR = "t2_digits"

#: The shard/aggregate key this detector's output appears under.
SCORE_KEY = score_key(DETECTOR)

#: The one race with a single national top two.
PRES = "PRES"

#: How many candidates per round the tests are run over.
TOP_N = 2

#: Benford's second-digit law, ``P(d)`` for ``d = 0..9``.
BENFORD_SECOND: tuple[float, ...] = tuple(
    sum(math.log10(1.0 + 1.0 / (10 * first + d)) for first in range(1, 10))
    for d in range(10)
)

#: The last digit of a real tally is pure chance.
UNIFORM: tuple[float, ...] = tuple([0.1] * 10)

#: ``test name -> (smallest eligible count, expected frequencies)``.
TESTS: dict[str, tuple[int, tuple[float, ...]]] = {
    "second_digit": (10, BENFORD_SECOND),
    "last_digit": (100, UNIFORM),
}

#: Eligible counts a stratum needs before a chi-square means anything.
MIN_STRATUM_N = 100

#: Smallest expected bin count a chi-square approximation tolerates.
MIN_EXPECTED = 5.0

#: Municipality-size buckets, in sections: ``(name, lower, upper)``, upper
#: exclusive and ``None`` for unbounded.
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("small", 0, 20),
    ("mid", 20, 100),
    ("large", 100, None),
)

#: Degrees of freedom: 10 bins, no parameter fitted from the data.
DOF = 9

#: p at or below which a stratum is called out in the summary.
ALARM_P = 1e-4


# --------------------------------------------------------------------------
# Reading the warehouse
# --------------------------------------------------------------------------

#: Total nominal presidential vote per (round, candidate), NATIONALLY -- the
#: top two of each round are picked from this and from nothing else.
_NATIONAL = f"""
    SELECT f.round::INT AS round,
           c.number::INT AS cand,
           MIN(c.name) AS name,
           SUM(f.votes)::BIGINT AS votes
    FROM fact_votes f
    JOIN dim_candidate c ON c.id = f.candidate_id
    WHERE f.race = '{PRES}' AND f.votavel_kind = 'candidate'
    GROUP BY 1, 2
"""

#: How many sections each municipality of one UF has -- the size the bucket is
#: measured in.  Sections, not voters: it is the count of tallies the
#: municipality produces that decides whether its numbers are big.
_MUN_SIZES = """
    SELECT mun_code, count(*)::BIGINT AS sections
    FROM dim_section WHERE uf = %s GROUP BY 1
"""


def _observations_sql(pairs: Sequence[tuple[int, int]]) -> str:
    """One UF's top-two presidential counts, per (round, candidate) pair.

    ``pairs`` is interpolated as literals rather than bound: they are ints read
    back out of the warehouse a moment ago, and a row-wise ``IN`` over a
    parameter list has no portable psycopg spelling.
    """
    wanted = ", ".join(f"({int(rnd)}, {int(cand)})" for rnd, cand in pairs)
    return f"""
        SELECT s.mun_code,
               f.round::INT AS round,
               f.votes::BIGINT AS votes
        FROM fact_votes f
        JOIN dim_section s ON s.id = f.section_id
        JOIN dim_candidate c ON c.id = f.candidate_id
        WHERE f.race = '{PRES}' AND f.votavel_kind = 'candidate'
          AND s.uf = %s AND f.votes IS NOT NULL
          AND (f.round, c.number) IN ({wanted})
    """


# --------------------------------------------------------------------------
# The maths -- pure, no database
# --------------------------------------------------------------------------


def bucket_of(sections: int) -> str:
    """A municipality's size bucket, from how many sections it has."""
    for name, low, high in BUCKETS:
        if sections >= low and (high is None or sections < high):
            return name
    return BUCKETS[-1][0]                     # unreachable: the last is open


def _digit(value: int, test: str) -> int:
    """The digit ``test`` looks at.  ``value`` is already eligible for it."""
    if test == "second_digit":
        return int(str(int(value))[1])
    return int(value) % 10


def digit_test(
    values: Iterable[int], test: str, *, uf: str, bucket: str
) -> dict[str, Any]:
    """One stratum x one test -> the dict that lands in the payload.

    Eligible counts are selected here rather than by the caller, so "which
    counts does this test look at" lives in exactly one place.  Returns either
    a tested stratum (``chi2``, ``p``, ``digit_freqs``, ``expected_freqs``) or
    a skipped one (``skipped``, ``reason``) -- and a skipped one carries **no**
    ``p`` key.
    """
    floor, expected = TESTS[test]
    eligible = [int(v) for v in values if int(v) >= floor]
    n = len(eligible)
    stratum: dict[str, Any] = {"uf": uf, "bucket": bucket, "test": test, "n": n}

    if n < MIN_STRATUM_N:
        return {
            **stratum,
            "skipped": True,
            "reason": f"only {n} eligible counts, needs {MIN_STRATUM_N}",
        }
    smallest = min(p * n for p in expected)
    if smallest < MIN_EXPECTED:
        return {
            **stratum,
            "skipped": True,
            "reason": (
                f"smallest expected bin {smallest:.2f} < {MIN_EXPECTED:g} "
                f"at n = {n}"
            ),
        }

    seen = Counter(_digit(v, test) for v in eligible)
    observed = [seen.get(d, 0) for d in range(10)]
    statistic = sum(
        (o - n * e) ** 2 / (n * e) for o, e in zip(observed, expected, strict=True)
    )
    return {
        **stratum,
        "skipped": False,
        "chi2": round(float(statistic), 6),
        "p": _p_value(statistic),
        "dof": DOF,
        "digit_counts": observed,
        "digit_freqs": [round(o / n, 6) for o in observed],
        "expected_freqs": [round(float(e), 6) for e in expected],
    }


def _p_value(statistic: float) -> float:
    """``chi2.sf`` made safe to store and to compare.

    Clamped into [0, 1] and NaN-proofed: an underflowed or non-finite tail must
    come back as a real number, because this value is JSON-serialised and then
    compared against a threshold.
    """
    p = float(chi2.sf(float(statistic), DOF))
    if not math.isfinite(p):
        return 0.0 if statistic > 0 else 1.0
    return min(1.0, max(0.0, p))


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


class Digits(BaseDetector):
    """Second-digit Benford and last-digit uniformity, per state x size bucket.

    Writes NO section scores (see the module docstring): the output is
    ``state_payloads`` plus a national summary.
    """

    name = DETECTOR

    def params(self) -> dict[str, Any]:
        return {
            "statistic": (
                "chi-square goodness of fit on 10 digit bins, 9 dof: the second "
                "digit against Benford's second-digit law, the last digit "
                "against uniform(0-9)"
            ),
            "population": (
                f"section-level nominal {PRES} vote counts, both rounds, for the "
                f"top {TOP_N} candidates of each round nationally"
            ),
            "second_digit_floor": TESTS["second_digit"][0],
            "last_digit_floor": TESTS["last_digit"][0],
            "strata": "state x municipality-size bucket, size measured in sections",
            "buckets": {name: f">= {low}" + ("" if high is None else f", < {high}")
                        for name, low, high in BUCKETS},
            "eligibility": (
                f"a chi-square is computed only with >= {MIN_STRATUM_N} eligible "
                f"counts AND every expected bin >= {MIN_EXPECTED:g}; otherwise the "
                "stratum is emitted skipped, with no p at all -- never NaN"
            ),
            "section_scores": (
                "none -- one count has one digit, so there is nothing to test "
                "per section; the composite ignores this detector"
            ),
            "caveat": (
                "2BL is a screen, not a fraud test (Deckert, Myagkov & Ordeshook "
                "2011; Mebane's replies); the prose belongs to the dashboard"
            ),
        }

    # -- compute -----------------------------------------------------------

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        pairs = self._top_candidates(db)
        payloads: dict[str, dict[str, Any]] = {}
        pooled: dict[str, list[int]] = {test: [] for test in TESTS}

        for (uf,) in db.fetch("SELECT DISTINCT uf FROM dim_section ORDER BY uf"):
            by_bucket = self._counts_by_bucket(db, uf, pairs)
            if by_bucket is None:
                continue
            strata = [
                digit_test(values, test, uf=uf, bucket=bucket)
                for bucket, values in by_bucket.items()
                for test in TESTS
            ]
            if not strata:
                continue
            for values in by_bucket.values():
                for test in TESTS:
                    pooled[test].extend(values)
            payloads[uf] = {
                "uf": uf,
                "strata": strata,
                "n_counts": sum(len(v) for v in by_bucket.values()),
                "buckets": sorted(by_bucket),
            }

        national = [
            digit_test(values, test, uf="BR", bucket="all")
            for test, values in pooled.items()
        ]
        summary = {
            "score_key": SCORE_KEY,
            "rounds": {str(rnd): sorted(c for r, c in pairs if r == rnd)
                       for rnd in sorted({r for r, _c in pairs})},
            "national": national,
            "strata_tested": sum(
                1 for p in payloads.values() for s in p["strata"] if not s["skipped"]
            ),
            "strata_skipped": sum(
                1 for p in payloads.values() for s in p["strata"] if s["skipped"]
            ),
            "alarm_p": ALARM_P,
            "worst_strata": _worst_strata(payloads),
        }
        return DetectorResult(summary, state_payloads=payloads)

    # -- inputs ------------------------------------------------------------

    @staticmethod
    def _top_candidates(db: Database) -> list[tuple[int, int]]:
        """``[(round, candidate number), ...]`` -- the top two of each round.

        Decided NATIONALLY: a per-state top two would make the strata
        incomparable, and the digits of a fourth-placed candidate's counts are
        a different population entirely.
        """
        by_round: dict[int, list[tuple[int, int]]] = {}
        for rnd, cand, _name, votes in db.fetch(_NATIONAL):
            by_round.setdefault(int(rnd), []).append((int(votes), int(cand)))
        pairs: list[tuple[int, int]] = []
        for rnd, rows in sorted(by_round.items()):
            # Ties break on the lower candidate number, so the pick is stable.
            rows.sort(key=lambda row: (-row[0], row[1]))
            pairs += [(rnd, cand) for _v, cand in rows[:TOP_N]]
        return pairs

    @staticmethod
    def _counts_by_bucket(
        db: Database, uf: str, pairs: Sequence[tuple[int, int]]
    ) -> dict[str, list[int]] | None:
        """One UF's counts, grouped by its municipalities' size bucket.

        Every bucket the UF's municipalities occupy gets an entry, even an
        empty one: a stratum with nothing eligible in it is a stratum that was
        LOOKED at and skipped, and that has to be visible.  ``None`` means the
        UF has no sections at all.
        """
        sizes = db.fetch(_MUN_SIZES, (uf,))
        if not sizes:
            return None
        bucket_by_mun = {str(mun): bucket_of(int(n)) for mun, n in sizes}
        by_bucket: dict[str, list[int]] = {
            name: [] for name, _l, _h in BUCKETS if name in set(bucket_by_mun.values())
        }
        if not pairs:
            return by_bucket
        rows = db.fetch_frame(_observations_sql(pairs), (uf,))
        for mun, _rnd, votes in rows.select("mun_code", "round", "votes").iter_rows():
            bucket = bucket_by_mun.get(str(mun))
            if bucket is not None:
                by_bucket[bucket].append(int(votes))
        return by_bucket


def _worst_strata(
    payloads: dict[str, dict[str, Any]], limit: int = 20
) -> list[dict[str, Any]]:
    """The tested strata with the smallest p, most improbable first."""
    rows = [
        {"uf": s["uf"], "bucket": s["bucket"], "test": s["test"],
         "n": s["n"], "chi2": s["chi2"], "p": s["p"]}
        for payload in payloads.values()
        for s in payload["strata"]
        if not s["skipped"]
    ]
    rows.sort(key=lambda r: (float(r["p"]), r["uf"], r["bucket"], r["test"]))
    return rows[:limit]


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


def read_strata(db: Database, detector: str = DETECTOR) -> list[dict[str, Any]]:
    """Every state's strata, flattened -- one dict per (state, bucket, test).

    The national row is deliberately NOT in here: it lives in the summary, and
    a reader iterating "the strata" must get strata, not a mixture of strata
    and a total.
    """
    name = DETECTOR if detector in (DETECTOR, SCORE_KEY) else detector
    out: list[dict[str, Any]] = []
    for _uf, payload in read_state_payloads(db, name).iter_rows():
        out.extend(json.loads(payload).get("strata", []))
    return out


def read_scores(db: Database, detector: str = DETECTOR) -> pl.DataFrame:
    """This detector's section scores -- always EMPTY, and deliberately so.

    Kept because every detector module exposes it: callers should not have to
    special-case a screening-level detector.  Accepts either the warehouse name
    (``t2_digits``) or the shard score key (``digits``).
    """
    return _read_scores(db, DETECTOR if detector in (DETECTOR, SCORE_KEY) else detector)
