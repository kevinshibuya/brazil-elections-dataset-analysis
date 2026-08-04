"""Tier 2 -- turnout outliers: the section that voted unlike its neighbours.

THE QUESTION
    Turnout is the least political number the urna publishes::

        t_i = comparecimento_i / aptos_i

    and it is also the most LOCAL one.  A rural zone where half the electorate
    moved away years ago runs at 0.60; a downtown zone of civil servants runs
    at 0.88.  Neither is a finding.  What is worth a question is a section that
    departs from the turnout of the *municipality it sits in* -- the other
    sections drawn from the same register, on the same day, under the same
    weather.

    So the statistic is a robust z against the municipality's own ``{t_j}``::

        z_i = (t_i - median_m(t)) / (1.4826 * MAD_m(t))

    Median and MAD rather than mean and sd, for the usual reason: the thing
    being looked for would otherwise inflate the very scale it is measured
    against.  A single 0.35 section cannot drag a 60-section median.

BOTH SIGNS COUNT
    A section far ABOVE its municipality is the classic ballot-stuffing shape
    (more people voted than plausibly turned up); a section far BELOW it is the
    classic failed-urna / suppressed-section shape.  They are the same class of
    finding seen from two sides, so ``score = |z| / 2`` and no sign is
    privileged.

THE DENOMINATOR IS CORRECT AS PUBLISHED (amendment A1)
    An earlier draft excluded sections that ABSORB other sections
    (``agregadas``), on the theory that the host's ``aptos`` would understate
    the electorate actually voting there.  It does not, and there is no column
    to filter on anyway: ``read_detail.agg_secoes`` is NULL for every row of
    the section-level file, because TSE does not publish a section-level
    aggregation count.  What TSE *does* do is fold the absorbed sections'
    electorate into the host section's ``aptos``, so numerator and denominator
    already describe the same set of voters.  There is nothing to exclude and
    no filter here.

BASELINE, AND WHAT HAPPENS WHEN IT DEGENERATES
    The municipality, when it has at least 10 sections AND a non-zero MAD;
    otherwise the UF, with ``detail["baseline"] = "state"``.  A zero MAD is not
    a small scale but *no* scale -- more than half the municipality's sections
    sharing one turnout to the last voter, which happens in the small ones and
    in fixtures -- and dividing by it would turn a one-voter difference into an
    infinite z.  If the state's MAD is zero too, ``z = 0``: there is no
    dispersion to be an outlier in.  ``detail["mad"]`` always records which of
    those happened.

RACE AND ROUND
    Presidential only, and per round.  Every section reports a presidential
    result, so PRES is the one race whose turnout is comparable everywhere in
    the country; and the two rounds are three weeks and one weather forecast
    apart, so they get their own baselines rather than being pooled.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from ..db import Database
from ..export.shards import score_key
from .base import (
    SCORES_TABLE,
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    read_scores as _read_scores,
)

__all__ = ["Turnout", "read_scores", "score_frame", "section_scores"]

DETECTOR = "t2_turnout"

#: The shard/aggregate key this detector's scores appear under.  Derived, not
#: written down twice -- ``t2_turnout`` -> ``turnout``.
SCORE_KEY = score_key(DETECTOR)

#: The one race whose turnout every section in Brazil reports.
PRES = "PRES"

#: Sections a municipality needs before it may be its own reference.
MIN_MUNICIPALITY_SECTIONS = 10

#: MAD -> standard-deviation scale, for a normal.
MAD_TO_SIGMA = 1.4826


# --------------------------------------------------------------------------
# Reading the warehouse -- one UF at a time
# --------------------------------------------------------------------------

#: A section's turnout inputs.  ``aptos > 0`` is a denominator condition, not a
#: quality filter: a section with no registered voters has no turnout, so there
#: is no question to answer and it is left unscored rather than scored 0.
_SECTIONS = f"""
    SELECT f.section_id,
           s.mun_code,
           f.round::INT AS round,
           f.aptos::DOUBLE PRECISION AS aptos,
           f.comparecimento::DOUBLE PRECISION AS comparecimento
    FROM fact_section_stats f
    JOIN dim_section s ON s.id = f.section_id
    WHERE f.race = '{PRES}' AND s.uf = %s
      AND f.aptos IS NOT NULL AND f.aptos > 0
      AND f.comparecimento IS NOT NULL
"""


# --------------------------------------------------------------------------
# The maths -- pure, no database
# --------------------------------------------------------------------------


def _mad(frame: pl.DataFrame, keys: list[str], prefix: str) -> pl.DataFrame:
    """``(n, median, MAD)`` of ``t`` within each group, as three columns."""
    median = frame.group_by(keys).agg(
        pl.len().alias(f"{prefix}_n"), pl.col("t").median().alias(f"{prefix}_median")
    )
    deviation = (
        frame.join(median, on=keys, how="left")
        .with_columns((pl.col("t") - pl.col(f"{prefix}_median")).abs().alias("_ad"))
        .group_by(keys)
        .agg(pl.col("_ad").median().alias(f"{prefix}_mad"))
    )
    return median.join(deviation, on=keys, how="left")


def score_frame(obs: pl.DataFrame) -> pl.DataFrame:
    """One UF's ``(section, round, t)`` observations -> its scored frame.

    ``obs`` carries ``section_id, mun_code, round, t``.  Pure, so the statistic
    is checkable without a warehouse.
    """
    if obs.height == 0:
        return obs.head(0)

    mun_keys = ["mun_code", "round"]
    obs = obs.join(_mad(obs, mun_keys, "mun"), on=mun_keys, how="left").join(
        _mad(obs, ["round"], "st"), on=["round"], how="left"
    )

    use_mun = (pl.col("mun_n") >= MIN_MUNICIPALITY_SECTIONS) & (pl.col("mun_mad") > 0)
    obs = obs.with_columns(
        pl.when(use_mun)
        .then(pl.col("mun_median"))
        .otherwise(pl.col("st_median"))
        .alias("median"),
        pl.when(use_mun).then(pl.col("mun_mad")).otherwise(pl.col("st_mad")).alias("mad"),
        pl.when(use_mun)
        .then(pl.lit("municipality"))
        .otherwise(pl.lit("state"))
        .alias("baseline"),
    )
    scale = MAD_TO_SIGMA * pl.col("mad")
    return obs.with_columns(
        pl.when(scale > 0)
        .then((pl.col("t") - pl.col("median")) / scale)
        .otherwise(0.0)
        .alias("z")
    ).with_columns((pl.col("z").abs() / 2.0).alias("score"))


def section_scores(scored: pl.DataFrame) -> pl.DataFrame:
    """The scored frame as score rows: one per ``(section, PRES, round)``.

    ``detail`` carries the SAME five keys on every row -- ``read_scores``
    returns it as a polars struct and infers the schema from the leading rows,
    so a key that appears only sometimes is a key that is silently lost.
    """
    return scored.select(
        pl.col("section_id").cast(pl.Int64),
        pl.lit(PRES).alias("race"),
        pl.col("round").cast(pl.Int16),
        pl.col("score"),
        pl.struct(
            pl.col("t").round(9),
            pl.col("median").round(9),
            pl.col("mad").round(9),
            pl.col("z").round(9),
            pl.col("baseline"),
        ).alias("detail"),
    )


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


class Turnout(BaseDetector):
    """Does this section's turnout depart from its municipality's?

    Scores ``(race='PRES', round=r)``, one row per section that publishes a
    presidential ``aptos``/``comparecimento`` pair.
    """

    name = DETECTOR
    #: score 2.0 == |z| >= 4: four robust sigma from the municipality's turnout.
    flag_threshold = 2.0

    def params(self) -> dict[str, Any]:
        return {
            "statistic": (
                "t = comparecimento / aptos; robust z against the municipality's "
                "own {t_j}, per round"
            ),
            "race": f"{PRES} only -- the one race every section reports",
            "rounds": "scored separately; three weeks apart is a different day",
            "baseline": (
                f"the municipality when it has >= {MIN_MUNICIPALITY_SECTIONS} "
                "sections and MAD > 0, otherwise the UF"
            ),
            "mad_zero": (
                "a municipal MAD of 0 falls back to the state distribution; if that "
                "is 0 too, z = 0 (no dispersion to be an outlier in), and detail.mad "
                "records which happened"
            ),
            "scale": f"MAD x {MAD_TO_SIGMA} (normal-consistent)",
            "score": "|z| / 2, so z = 6 -> 3 -- the same order as the -log10 p detectors",
            "signs": "both -- above is the stuffing shape, below the failed-urna shape",
            "aggregated_sections": (
                "no exclusion (amendment A1): TSE folds an absorbed section's "
                "electorate into the host's aptos, so the denominator already "
                "matches the numerator, and no section-level aggregation column "
                "is published to filter on"
            ),
            "sections_without_aptos": "NOT scored -- no denominator, so no question",
            "leave_one_out": "not used -- median and MAD are already resistant",
        }

    # -- compute -----------------------------------------------------------

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        # One UF at a time: neither baseline ever crosses a state line, so this
        # is exact, and peak memory is bounded by the largest UF rather than by
        # Brazil's 472 k sections.
        scored = 0
        for (uf,) in db.fetch("SELECT DISTINCT uf FROM dim_section ORDER BY uf"):
            obs = self._observations(db, uf)
            if obs.height == 0:
                continue
            scored += obs.height
            scores.add_frame(section_scores(score_frame(obs)))

        scores.flush()
        return DetectorResult(self._summary(db, scored))

    @staticmethod
    def _observations(db: Database, uf: str) -> pl.DataFrame:
        rows = db.fetch_frame(_SECTIONS, (uf,))
        if rows.height == 0:
            return rows
        return rows.with_columns(
            (pl.col("comparecimento") / pl.col("aptos")).alias("t")
        ).select("section_id", "mun_code", "round", "t")

    # -- summary -----------------------------------------------------------

    def _summary(self, db: Database, scored: int) -> dict[str, Any]:
        """Read back what was just written (inside ``run``'s transaction).

        The two numbers that tell a reader whether a run is a finding or an
        artefact are the baseline mix and how often MAD came out at zero --
        a collapsed scale makes every z a fiction.
        """
        baselines = {
            str(b): int(n)
            for b, n in db.fetch(
                f"SELECT detail ->> 'baseline', count(*) FROM {SCORES_TABLE} "
                f"WHERE detector = %s GROUP BY 1 ORDER BY 1",
                (self.name,),
            )
        }
        stats = db.fetch(
            f"""
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY score),
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY score),
                   max(score),
                   percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY (detail ->> 't')::DOUBLE PRECISION),
                   percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY (detail ->> 'mad')::DOUBLE PRECISION),
                   count(*) FILTER (WHERE (detail ->> 'mad')::DOUBLE PRECISION = 0),
                   count(*) FILTER (WHERE (detail ->> 'z')::DOUBLE PRECISION > 0),
                   count(*) FILTER (WHERE (detail ->> 'z')::DOUBLE PRECISION < 0)
            FROM {SCORES_TABLE} WHERE detector = %s
            """,
            (self.name,),
        )[0]
        return {
            "score_key": SCORE_KEY,
            "section_rounds_observed": scored,
            "baseline_mix": baselines,
            "score": {"p50": _f(stats[0]), "p95": _f(stats[1]), "max": _f(stats[2])},
            "turnout_p50": _f(stats[3]),
            "mad_p50": _f(stats[4]),
            "sections_with_zero_mad": int(stats[5] or 0),
            "above_baseline": int(stats[6] or 0),
            "below_baseline": int(stats[7] or 0),
            "worst": _worst_sections(db, self.name),
        }


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _worst_sections(db: Database, detector: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.fetch(
        f"""
        SELECT s.section_id, d.uf, d.mun_code, m.name, d.zone, d.section,
               s.race, s.round, s.score, s.detail
        FROM {SCORES_TABLE} s
        LEFT JOIN dim_section d ON d.id = s.section_id
        LEFT JOIN dim_municipality m ON m.code = d.mun_code
        WHERE s.detector = %s
        ORDER BY s.score DESC, s.section_id LIMIT %s
        """,
        (detector, limit),
    )
    return [
        {
            "section_id": int(r[0]),
            "uf": r[1],
            "mun_code": r[2],
            "mun_name": r[3],
            "zone": r[4],
            "section": r[5],
            "race": r[6],
            "round": int(r[7]),
            "score": round(float(r[8]), 4),
            "detail": r[9],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


def read_scores(db: Database, detector: str = DETECTOR) -> pl.DataFrame:
    """This detector's section scores, ``detail`` as a polars struct.

    Accepts either the warehouse name (``t2_turnout``) or the shard score key
    (``turnout``): the exporter and the dashboard speak the key, the analysis
    layer speaks the detector, and refusing one of them would only move the
    mapping into every caller.
    """
    return _read_scores(db, DETECTOR if detector in (DETECTOR, SCORE_KEY) else detector)
