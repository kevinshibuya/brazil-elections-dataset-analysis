"""Tier 2 -- split-ticket divergence: the president against the deputies.

THE QUESTION
    A voter who picks a presidential candidate usually, but not always, picks
    a federal deputy from that candidate's coalition.  How often they *don't*
    is a stable local habit: it depends on which deputies are actually on the
    ballot near you, on how strong a regional alliance is, on whether the big
    local machine happens to sit in the other camp.  So the raw gap

        d_i = share_pres(c, i) - share_dep(coalition(c), i)

    says almost nothing on its own.  In Maranhão d is large and negative for
    half the state -- and that is a real political fact about Maranhão, not an
    anomaly in any section of it.

    What *is* a question worth asking is whether ONE SECTION diverges from the
    habit of the municipality it sits in.  So the reference distribution is the
    municipality's own ``{d_j}``, and the statistic is a robust z against it::

        z_i = (d_i - median_m(d)) / (1.4826 * MAD_m(d))

    A regional alliance moves every section in the municipality together: the
    median moves with them and no section deviates.  That is the whole design,
    and it is what the first acceptance test pins down.

    Median and MAD are already resistant, so leave-one-out is not needed here
    (unlike the beta-binomial detector, whose estimator is a mean): a single
    planted section cannot drag a 50-section median far enough to hide itself.

WHAT THE TWO SHARES ARE MADE OF
    ``share_pres(c, i)``  candidate c's votes over ALL presidential nominal
                          (``votavel_kind = 'candidate'``) votes in section i,
                          round 1.
    ``share_dep(C, i)``   votes for the coalition's parties over ALL federal-
                          deputy votes in section i -- nominal AND ``legenda``,
                          because a legenda vote is the purest statement of
                          party preference there is, and excluding it would
                          measure a different thing in every section depending
                          on how many people used it.

    ZERO-VOTE ROWS DO NOT EXIST, and the denominator is where that bites.  A
    coalition party with no row in a section got zero deputy votes there -- but
    the *denominator* is every deputy vote cast in that section, so it is
    summed over the section's rows as they come, never over the coalition's
    rows.  Only the NUMERATOR is densified (absent party -> 0).  Summing a
    sparse numerator against a sparse denominator would make every section look
    like a 100 % coalition section.

BASELINE, AND WHAT HAPPENS WHEN IT DEGENERATES
    The municipality, when it has at least 10 sections AND a non-zero MAD;
    otherwise the UF, with ``detail["baseline"] = "state"``.  A zero MAD is not
    a small scale, it is *no* scale -- more than half the municipality's
    sections sharing one value to the last vote -- and dividing by it would
    turn a one-vote difference into an infinite z.  If the state's MAD is zero
    too (only ever a fixture, or a UF with a handful of sections), ``z = 0``:
    there is no dispersion to be an outlier in.  ``detail["mad"]`` is always
    stored, so a reader can see which of those happened.

SCORE SCALE
    ``score = |z| / 2``, so z = 6 -> 3, deliberately the same order as the
    ``-log10 p`` detectors (p = 1e-3 -> 3) and therefore comparable inside the
    composite.  Both signs count: voting for a president while abandoning his
    deputies, and the reverse, are the same class of finding.

RACE AND ROUND
    Written as ``(race = 'ALL', round = 1)`` -- it is a cross-race statistic
    and federal deputies are elected in round 1 only.  Task 18 feeds the
    round-1 score into BOTH composite rounds; the split-ticket habit of a
    section does not stop existing three weeks later.

ANNULLED CANDIDACIES COUNT
    Same choice as Tier 1 (A4) and extreme dominance: the shares are what the
    urna reported, not what survived judicial validation.  An annulled deputy
    candidacy still carries a ``party_number`` on its fact row, so its votes
    count for its party in both numerator and denominator.

THE COALITION LISTS ARE A REVIEWED CONSTANT
    They live in :mod:`observatory.coalitions` and are NOT derived from
    ``dim_candidate.coalition`` at run time.  The detector does read that
    column and compares -- the result is in ``detector_summary.summary``
    under ``coalition_check`` -- but a disagreement is reported, never
    resolved by preferring one source.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from .. import coalitions
from ..db import Database
from ..export.shards import score_key
from .base import (
    CROSS_RACE,
    SCORES_TABLE,
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    read_scores as _read_scores,
)

__all__ = ["SplitTicket", "read_scores", "score_frame"]

DETECTOR = "t2_split_ticket"

#: The shard/aggregate key this detector's scores appear under.  Derived, not
#: written down twice: ``score_key`` owns the "tier 1 keeps its prefix, every
#: other tier drops it" rule, so ``t2_split_ticket`` -> ``split_ticket``
#: without an entry in its table.
SCORE_KEY = score_key(DETECTOR)

PRES = "PRES"
DEPFED = "DEPFED"

#: Federal deputies are elected in one round; the presidential round compared
#: against them is therefore round 1.
ROUND = 1

#: Presidential candidates scored: the top N of round 1 by total nominal vote.
TOP_N = 2

#: Sections a municipality needs before it may be its own reference.
MIN_MUNICIPALITY_SECTIONS = 10

#: Deputy votes that count: nominal and legenda, never blank/null.
_DEP_KINDS = ("candidate", "legenda")

#: MAD -> standard-deviation scale, for a normal.
MAD_TO_SIGMA = 1.4826


# --------------------------------------------------------------------------
# Reading the warehouse -- one UF at a time
# --------------------------------------------------------------------------

#: The top-2 selection is NATIONAL, so this one query is not per-UF.
_PRES_NATIONAL = f"""
    SELECT c.number::INT AS cand, MIN(c.name) AS name,
           MIN(c.coalition) AS tse_coalition,
           SUM(f.votes)::BIGINT AS votes
    FROM fact_votes f
    JOIN dim_candidate c ON c.id = f.candidate_id
    WHERE f.race = '{PRES}' AND f.round = {ROUND} AND f.votavel_kind = 'candidate'
    GROUP BY 1
"""

#: Denominator of share_pres: every presidential nominal vote in the section.
_PRES_SECTIONS = f"""
    SELECT f.section_id, s.mun_code, SUM(f.votes)::BIGINT AS pres_total
    FROM fact_votes f
    JOIN dim_section s ON s.id = f.section_id
    WHERE f.race = '{PRES}' AND f.round = {ROUND} AND f.votavel_kind = 'candidate'
      AND s.uf = %s
    GROUP BY 1, 2
    HAVING SUM(f.votes) > 0
"""

#: Numerator of share_pres, for the selected candidates.  Sparse.
_PRES_CANDIDATE = f"""
    SELECT f.section_id, c.number::INT AS cand, SUM(f.votes)::BIGINT AS pres_votes
    FROM fact_votes f
    JOIN dim_candidate c ON c.id = f.candidate_id
    JOIN dim_section s ON s.id = f.section_id
    WHERE f.race = '{PRES}' AND f.round = {ROUND} AND f.votavel_kind = 'candidate'
      AND s.uf = %s AND c.number = ANY(%s)
    GROUP BY 1, 2
"""

#: Denominator of share_dep: EVERY federal-deputy vote in the section, over the
#: section's own rows -- which is why sparsity cannot shrink it.
_DEP_SECTIONS = f"""
    SELECT f.section_id, SUM(f.votes)::BIGINT AS dep_total
    FROM fact_votes f
    JOIN dim_section s ON s.id = f.section_id
    WHERE f.race = '{DEPFED}' AND f.round = {ROUND}
      AND f.votavel_kind = ANY(%s) AND s.uf = %s
    GROUP BY 1
    HAVING SUM(f.votes) > 0
"""

#: Numerator of share_dep, per party.  Sparse; densified to 0 in polars.
_DEP_PARTY = f"""
    SELECT f.section_id, f.party_number::INT AS party, SUM(f.votes)::BIGINT AS dep_votes
    FROM fact_votes f
    JOIN dim_section s ON s.id = f.section_id
    WHERE f.race = '{DEPFED}' AND f.round = {ROUND}
      AND f.votavel_kind = ANY(%s) AND s.uf = %s
      AND f.party_number = ANY(%s)
    GROUP BY 1, 2
"""


# --------------------------------------------------------------------------
# The maths -- pure, no database
# --------------------------------------------------------------------------


def _mad(frame: pl.DataFrame, keys: list[str], prefix: str) -> pl.DataFrame:
    """``(n, median, MAD)`` of ``d`` within each group, as three columns."""
    median = frame.group_by(keys).agg(
        pl.len().alias(f"{prefix}_n"), pl.col("d").median().alias(f"{prefix}_median")
    )
    deviation = (
        frame.join(median, on=keys, how="left")
        .with_columns((pl.col("d") - pl.col(f"{prefix}_median")).abs().alias("_ad"))
        .group_by(keys)
        .agg(pl.col("_ad").median().alias(f"{prefix}_mad"))
    )
    return median.join(deviation, on=keys, how="left")


def score_frame(obs: pl.DataFrame) -> pl.DataFrame:
    """One UF's ``(section, candidate)`` observations -> its section scores.

    ``obs`` carries ``section_id, mun_code, cand, d``.  Everything from here to
    the returned frame is pure, which is what makes the statistic checkable
    without a warehouse.
    """
    if obs.height == 0:
        return obs.head(0)

    mun_keys = ["mun_code", "cand"]
    obs = obs.join(_mad(obs, mun_keys, "mun"), on=mun_keys, how="left").join(
        _mad(obs, ["cand"], "st"), on=["cand"], how="left"
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
        .then((pl.col("d") - pl.col("median")) / scale)
        .otherwise(0.0)
        .alias("z")
    ).with_columns((pl.col("z").abs() / 2.0).alias("score"))


def section_scores(scored: pl.DataFrame) -> pl.DataFrame:
    """Collapse to ONE row per section: the candidate with the largest ``|z|``.

    The primary key allows exactly one ``(detector, section, race, round)`` row
    and the composite reads a single number per section, so the section's score
    is the maximum over the scored candidates and ``detail`` describes the one
    that produced it.  Ties (the two-candidate fixtures produce exact ones by
    symmetry) break on the lower candidate number, so the choice is stable
    across runs.
    """
    best = scored.sort(["score", "cand"], descending=[True, False]).unique(
        subset=["section_id"], keep="first", maintain_order=True
    )
    return best.select(
        pl.col("section_id").cast(pl.Int64),
        pl.lit(CROSS_RACE).alias("race"),
        pl.lit(ROUND, dtype=pl.Int16).alias("round"),
        pl.col("score"),
        pl.struct(
            pl.col("cand").cast(pl.Int64),
            pl.col("d").round(9),
            pl.col("median").round(9),
            pl.col("mad").round(9),
            pl.col("z").round(9),
            pl.col("baseline"),
        ).alias("detail"),
    )


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


class SplitTicket(BaseDetector):
    """Does this section split its ticket unlike the rest of its municipality?

    Scores ``(race='ALL', round=1)``, one row per section that reports both a
    presidential and a federal-deputy result.
    """

    name = DETECTOR
    #: score 2.0 == |z| >= 4: four robust sigma from the municipality's habit.
    flag_threshold = 2.0

    def params(self) -> dict[str, Any]:
        return {
            "statistic": (
                "d = share_pres(c) - share_dep(coalition(c)); robust z against "
                "the municipality's own {d_j}"
            ),
            "candidates": f"top {TOP_N} presidential candidates of round {ROUND}, by total nominal vote",
            "share_pres": "candidate votes / all PRES nominal votes in the section",
            "share_dep": (
                "coalition-party votes / all DEPFED votes in the section, "
                "'candidate' and 'legenda' kinds both counted"
            ),
            "sparsity": (
                "numerator densified (absent party = 0); the denominator is the "
                "section's own total, so sparsity cannot shrink it"
            ),
            "coalitions": "reviewed constants in observatory.coalitions, NOT derived from dim_candidate",
            "baseline": f"the municipality when it has >= {MIN_MUNICIPALITY_SECTIONS} sections and MAD > 0, otherwise the UF",
            "leave_one_out": "not used -- median and MAD are already resistant",
            "mad_zero": (
                "a municipal MAD of 0 falls back to the state distribution; if that "
                "is 0 too, z = 0 (no dispersion to be an outlier in), and detail.mad "
                "records which happened"
            ),
            "scale": f"MAD x {MAD_TO_SIGMA} (normal-consistent)",
            "score": "|z| / 2, so z = 6 -> 3 -- the same order as the -log10 p detectors",
            "section_score": "max over the scored candidates; detail is that candidate",
            "written_as": f"race = '{CROSS_RACE}', round = {ROUND}",
            "round_1_feeds_both_composite_rounds": True,
            "annulled_candidacies": (
                "counted, in both numerator and denominator -- the urna's totals, "
                "as in Tier 1 (A4) and extreme dominance"
            ),
            "sections_missing_a_race": "NOT scored -- no denominator, so no question",
        }

    # -- compute -----------------------------------------------------------

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        picks = self._selected_candidates(db)
        scorable = [p for p in picks if p["coalition_parties"]]
        if not scorable:
            return DetectorResult(
                {"candidates": picks, "note": "no presidential candidate with a reviewed coalition"}
            )

        numbers = sorted(int(p["number"]) for p in scorable)
        parties = sorted({party for p in scorable for party in p["coalition_parties"]})

        offered = 0
        missing_a_race = 0
        # One UF at a time: neither baseline ever crosses a state line, so this
        # is exact, and it bounds peak memory by the largest UF rather than by
        # Brazil (Task 23 runs 472 k sections x ~30 deputy parties).
        for uf in db.fetch("SELECT DISTINCT uf FROM dim_section ORDER BY uf"):
            obs, dropped = self._observations(db, uf[0], numbers, parties, scorable)
            offered += obs.height
            missing_a_race += dropped
            if obs.height:
                scores.add_frame(section_scores(score_frame(obs)))

        scores.flush()
        return DetectorResult(self._summary(db, picks, offered, missing_a_race))

    @staticmethod
    def _observations(
        db: Database,
        uf: str,
        numbers: list[int],
        parties: list[int],
        scorable: list[dict[str, Any]],
    ) -> tuple[pl.DataFrame, int]:
        """One UF's ``(section, candidate)`` rows, plus the sections skipped.

        A section reaches the statistic only if it reports BOTH a presidential
        and a federal-deputy result; without one of them there is no gap to
        measure, which is a missing question rather than a clean answer.
        """
        pres = db.fetch_frame(_PRES_SECTIONS, (uf,))
        dep = db.fetch_frame(_DEP_SECTIONS, (list(_DEP_KINDS), uf))
        if pres.height == 0 or dep.height == 0:
            return pl.DataFrame(), pres.height

        base = pres.join(dep, on="section_id", how="inner")
        dropped = pres.height - base.height
        if base.height == 0:
            return pl.DataFrame(), dropped

        pres_votes = db.fetch_frame(_PRES_CANDIDATE, (uf, numbers))
        dep_votes = db.fetch_frame(_DEP_PARTY, (list(_DEP_KINDS), uf, parties))

        frames = []
        for pick in scorable:
            cand = int(pick["number"])
            coalition = list(pick["coalition_parties"])
            mine = pres_votes.filter(pl.col("cand") == cand).select(
                "section_id", "pres_votes"
            )
            theirs = (
                dep_votes.filter(pl.col("party").is_in(coalition))
                .group_by("section_id")
                .agg(pl.col("dep_votes").sum())
            )
            frames.append(
                base.join(mine, on="section_id", how="left")
                .join(theirs, on="section_id", how="left")
                .with_columns(
                    pl.col("pres_votes").fill_null(0),
                    pl.col("dep_votes").fill_null(0),
                )
                .with_columns(
                    pl.lit(cand, dtype=pl.Int64).alias("cand"),
                    (
                        pl.col("pres_votes") / pl.col("pres_total")
                        - pl.col("dep_votes") / pl.col("dep_total")
                    ).alias("d"),
                )
                .select("section_id", "mun_code", "cand", "d")
            )
        return pl.concat(frames, how="vertical"), dropped

    # -- candidate selection + the coalition cross-check --------------------

    @staticmethod
    def _selected_candidates(db: Database) -> list[dict[str, Any]]:
        """The top-``TOP_N`` round-1 presidential candidates, each carrying its
        reviewed coalition AND what ``dim_candidate.coalition`` says about it.

        The two are compared and the verdict travels in the summary.  They are
        never merged: the constants are the reviewed contract, TSE's string is
        the evidence, and if they disagree that is a finding for a human.
        """
        national = db.fetch_frame(_PRES_NATIONAL)
        if national.height == 0:
            return []
        total = int(national["votes"].sum()) or 1
        national = national.sort("votes", descending=True).head(TOP_N)

        picks: list[dict[str, Any]] = []
        for row in national.iter_rows(named=True):
            number = int(row["cand"])
            constant = coalitions.coalition_parties(number)
            from_tse, unrecognised = coalitions.parse_coalition(row["tse_coalition"])
            picks.append(
                {
                    "number": number,
                    "name": row["name"],
                    "votes": int(row["votes"]),
                    "share": round(int(row["votes"]) / total, 6),
                    "coalition_name": coalitions.coalition_name(number),
                    "coalition_parties": list(constant),
                    "coalition_siglas": coalitions.siglas_for(constant),
                    "coalition_check": {
                        "tse_coalition": row["tse_coalition"],
                        "tse_parties": sorted(from_tse),
                        "agrees": bool(from_tse) and from_tse == set(constant),
                        "in_constant_only": sorted(set(constant) - from_tse),
                        "in_tse_only": sorted(from_tse - set(constant)),
                        "unrecognised_siglas": unrecognised,
                    },
                }
            )
        return picks

    # -- summary -----------------------------------------------------------

    def _summary(
        self,
        db: Database,
        picks: list[dict[str, Any]],
        offered: int,
        missing_a_race: int,
    ) -> dict[str, Any]:
        """Read back what was just written (inside ``run``'s transaction).

        The numbers here are the ones that tell a reader whether a run is a
        finding or an artefact: the baseline mix, the shape of ``d`` itself,
        and -- the one that catches a collapsed statistic -- how often MAD came
        out at zero and therefore how often z is a scale-free fiction.
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
                       ORDER BY (detail ->> 'd')::DOUBLE PRECISION),
                   percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY (detail ->> 'mad')::DOUBLE PRECISION),
                   count(*) FILTER (WHERE (detail ->> 'mad')::DOUBLE PRECISION = 0),
                   count(*)
            FROM {SCORES_TABLE} WHERE detector = %s
            """,
            (self.name,),
        )[0]
        return {
            "candidates": picks,
            "score_key": SCORE_KEY,
            "candidate_observations": offered,
            "sections_missing_a_race": missing_a_race,
            "baseline_mix": baselines,
            "score": {"p50": _f(stats[0]), "p95": _f(stats[1]), "max": _f(stats[2])},
            "d_p50": _f(stats[3]),
            "mad_p50": _f(stats[4]),
            "sections_with_zero_mad": int(stats[5] or 0),
            "municipal_baselines": _baseline_shape(db, self.name),
            "worst": _worst_sections(db, self.name),
        }


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _baseline_shape(db: Database, detector: str) -> dict[str, Any]:
    """Quantiles of the per-municipality median/MAD of ``d``, per candidate.

    Per-municipality, not per-section: this is the sanity check on the
    reference distribution itself.  Quantiles rather than the list, because
    nationally the list is 5 570 municipalities x 2 candidates and a summary
    that big stops being read.
    """
    rows = db.fetch(
        f"""
        WITH g AS (
            SELECT DISTINCT ON (d.mun_code, (s.detail ->> 'cand'))
                   d.mun_code,
                   (s.detail ->> 'cand')::INT AS cand,
                   (s.detail ->> 'median')::DOUBLE PRECISION AS median,
                   (s.detail ->> 'mad')::DOUBLE PRECISION AS mad
            FROM {SCORES_TABLE} s
            JOIN dim_section d ON d.id = s.section_id
            WHERE s.detector = %s AND s.detail ->> 'baseline' = 'municipality'
        )
        SELECT cand, count(*), count(*) FILTER (WHERE mad = 0),
               min(median), percentile_cont(0.5) WITHIN GROUP (ORDER BY median),
               max(median),
               min(mad), percentile_cont(0.5) WITHIN GROUP (ORDER BY mad), max(mad)
        FROM g GROUP BY 1 ORDER BY 1
        """,
        (detector,),
    )
    return {
        str(r[0]): {
            "municipalities": int(r[1]),
            "with_zero_mad": int(r[2]),
            "median_d": {"min": _f(r[3]), "p50": _f(r[4]), "max": _f(r[5])},
            "mad": {"min": _f(r[6]), "p50": _f(r[7]), "max": _f(r[8])},
        }
        for r in rows
    }


def _worst_sections(db: Database, detector: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.fetch(
        f"""
        SELECT s.section_id, d.uf, d.mun_code, m.name, d.zone, d.section,
               s.score, s.detail
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
            "score": round(float(r[6]), 4),
            "detail": r[7],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


def read_scores(db: Database, detector: str = DETECTOR) -> pl.DataFrame:
    """This detector's section scores, ``detail`` as a polars struct.

    Accepts either the warehouse name (``t2_split_ticket``) or the shard score
    key (``split_ticket``).  The two names are one rename apart and callers
    legitimately hold both -- the exporter and the dashboard speak the key, the
    analysis layer speaks the detector -- so refusing one of them would only
    move the mapping into every caller.
    """
    return _read_scores(db, DETECTOR if detector in (DETECTOR, SCORE_KEY) else detector)
