"""Tier 2 -- extreme dominance: "300 x 0" done right.

THE QUESTION
    A section where one presidential candidate takes every single vote is not
    by itself evidence of anything: with 300 voters in a municipality that
    votes 90 % one way, a shutout is unremarkable.  The question that *can* be
    answered is *how improbable this section's result is given how the rest of
    its own municipality voted* -- and the honest answer has to allow for the
    fact that sections inside a municipality genuinely differ.  A plain
    binomial does not allow for that and would flag half the country.  So the
    baseline is a **beta-binomial**: a binomial whose success probability is
    itself drawn from a Beta, with an intraclass correlation ``rho`` fitted
    from the municipality's own section-to-section spread.

    Score = ``-log10`` of the smaller tail, so a section that is extreme in
    *either* direction (a shutout for, or a shutout against) scores high.  Both
    matter: "everyone voted for X here" and "nobody voted for X here" are the
    same class of finding.

THE BASELINE IS LEAVE-ONE-OUT, AND THAT IS THE WHOLE POINT
    A 300x0 section in a five-section municipality drags the municipal average
    towards itself; measured against a baseline it helped build, it looks
    ordinary.  So section *i*'s baseline is fitted over the municipality's
    *other* sections.  That is done by SUBTRACTING section *i* from the
    municipality's sufficient statistics, not by refitting per section: the
    ANOVA estimator below depends on the data only through
    ``(J, sum n, sum n^2, sum k, sum k^2/n)``, every one of which is additive,
    so leave-one-out is five subtractions and the whole country is one
    vectorised pass.

    Municipalities with fewer than 5 *other* sections cannot support an
    estimate of overdispersion, so those sections fall back to their UF's
    baseline -- also leave-one-out, for the same reason and at the same price.
    ``detail["baseline"]`` says which was used, always.

WHAT IS SCORED AND WHAT IS NOT
    Presidential races, both rounds, and only candidates with >= 1 % of the
    national nominal vote in that round.  Below that, ``k`` is so small that
    every tail is dominated by rounding and the estimator has nothing to fit.

    A section with ``n_i = 0`` nominal votes has no denominator and is NOT
    scored -- the one deliberate exception to the framework's "clean sections
    score 0", because there is no question to ask, not a negative answer.  A
    section whose UF cannot supply a baseline either (fewer than 5 other
    sections in the whole state, i.e. only ever a fixture) is likewise skipped
    and counted in the summary.

ZERO-VOTE ROWS DO NOT EXIST, WHICH IS EXACTLY WHERE THIS GOES WRONG IF RUSHED
    A candidate with no row in a section got zero votes there -- and a section
    where the candidate got zero is precisely the observation this detector
    exists to notice.  Both ``n_i`` (summed over ALL candidate rows in the
    section) and ``k_i`` (0 when absent) are therefore built from a DENSE
    (section x selected candidate) grid, never from the sparse rows as they
    come out of the warehouse.

ANNULLED CANDIDACIES COUNT TOWARDS n_i
    Same choice, and same reasoning, as Tier 1 (amendment A4): ``n_i`` is what
    the urna reported as nominal votes, not what survived judicial validation.
    They are only ever in the denominator here -- an annulled candidacy has
    nowhere near 1 % nationally, so it is never a scored candidate.

WHY sf() IS NOT USED FOR THE UPPER TAIL
    ``scipy``'s ``betabinom`` has no closed-form CDF; ``rv_discrete`` sums the
    PMF for ``cdf`` and then computes ``sf = 1 - cdf``.  In the far tail that
    subtraction is catastrophic cancellation: for n=300, p=0.55, k=300 it
    returns 5.2e-10 where the true tail is 1.3e-78.  Beta-binomial is symmetric
    under ``(k, a, b) -> (n - k, b, a)``, so ``P(X >= k)`` is computed as a CDF
    of the mirrored distribution.  Same quantity, ~290 orders of magnitude
    better.  See :func:`tail_probs`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import betabinom

from ..db import Database
from .base import (
    P_FLOOR,
    SCORES_TABLE,
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    read_scores as _read_scores,
)

__all__ = ["ExtremeDominance", "read_scores", "tail_probs"]

DETECTOR = "t2_extreme_dominance"

#: Only presidential races, both rounds (the plan's v1 scope).
RACE = "PRES"

#: A candidate is scored in a round when it took at least this share of the
#: national nominal vote in that round.
MIN_NATIONAL_SHARE = 0.01

#: Sections needed in the baseline group AFTER leaving the scored one out.
#: Below 5 the ANOVA numerator is one or two degrees of freedom of noise.
MIN_BASELINE_SECTIONS = 5

#: Intraclass-correlation clamp.  The floor keeps the Beta parameters finite
#: (rho -> 0 is the binomial limit, where a and b diverge); the ceiling keeps
#: a degenerate municipality from producing a distribution so flat that no
#: section could ever be extreme.
RHO_MIN = 1e-6
RHO_MAX = 0.99

_LN10 = math.log(10.0)


# --------------------------------------------------------------------------
# The maths
# --------------------------------------------------------------------------


def tail_probs(k: int, n: int, p: float, rho: float) -> tuple[float, float]:
    """``(P(X >= k), P(X <= k))`` under ``BetaBinom(n, p, rho)``.

    The shipped scoring shape, scalar form -- kept because it is the readable
    statement of what :func:`_tails` does to a whole state at once, and because
    it is what a reader checking the module against the plan will look for.

    ``P(X >= k)`` is evaluated as ``P(n - X <= n - k)`` under the mirrored
    ``(b, a)`` distribution rather than as ``sf``; see the module docstring.
    """
    a = p * (1 - rho) / rho
    b = (1 - p) * (1 - rho) / rho
    p_hi = float(betabinom.cdf(n - k, n, b, a))  # == P(X >= k), stable
    p_lo = float(betabinom.cdf(k, n, a, b))  # == P(X <= k)
    return p_hi, p_lo


def _tails(frame: pl.DataFrame) -> pl.DataFrame:
    """Add ``p_hi``, ``p_lo``, ``score`` and ``rank`` to a scored frame.

    ``rank`` exists because ``score`` saturates: every tail below 1e-16 scores
    16.0, and a section can easily produce two saturated candidates (the
    shut-out winner and the shut-out loser).  Ranking on the saturated score
    would make "which candidate does ``detail`` describe" a coin flip.  ``rank``
    is the same quantity WITHOUT the floor, extended below double-precision
    underflow by the log-PMF at ``k`` (which bounds both tails from below, so
    the extension is monotone across the boundary).  It orders; ``score``
    is what gets stored.
    """
    n = frame["n"].to_numpy().astype(np.float64)
    k = frame["k"].to_numpy().astype(np.float64)
    p = frame["p_hat"].to_numpy()
    rho = frame["rho_hat"].to_numpy()

    a = p * (1.0 - rho) / rho
    b = (1.0 - p) * (1.0 - rho) / rho
    p_hi = np.clip(betabinom.cdf(n - k, n, b, a), 0.0, 1.0)
    p_lo = np.clip(betabinom.cdf(k, n, a, b), 0.0, 1.0)
    p_min = np.minimum(p_hi, p_lo)

    rank = np.empty_like(p_min)
    alive = p_min > 0.0
    rank[alive] = -np.log10(p_min[alive])
    if not alive.all():
        # Underflowed to exactly 0 -- the true tail is below ~1e-323, so the
        # log-PMF (never underflows: betaln is evaluated in log space) is both
        # a valid lower bound on -log10(p) and >= every value the live branch
        # can produce.
        dead = ~alive
        rank[dead] = -betabinom.logpmf(k[dead], n[dead], a[dead], b[dead]) / _LN10

    return frame.with_columns(
        pl.Series("p_hi", p_hi),
        pl.Series("p_lo", p_lo),
        pl.Series("score", np.minimum(rank, -math.log10(P_FLOOR))),
        pl.Series("rank", rank),
    )


def _loo(prefix: str) -> list[pl.Expr]:
    """Leave-one-out ``(usable?, p_hat, rho_hat)`` from a group's sufficient
    statistics, by subtracting the scored section from each of them.

    ``J = count, S1 = sum n_j, S2 = sum n_j^2, K = sum k_j, Q = sum k_j^2/n_j``
    (``Q`` is ``sum n_j s_j^2``).  The ANOVA method-of-moments estimator
    (Ridout-Demetrio-Firth) needs nothing else, because

        sum n_j (s_j - p)^2 == Q - 2 p K + p^2 S1 == Q - p K   (as p = K / S1)
    """
    J = pl.col(f"{prefix}_J") - 1
    S1 = pl.col(f"{prefix}_S1") - pl.col("n")
    S2 = pl.col(f"{prefix}_S2") - pl.col("n2")
    K = pl.col(f"{prefix}_K") - pl.col("k")
    Q = pl.col(f"{prefix}_Q") - pl.col("q")

    usable = (J >= MIN_BASELINE_SECTIONS) & (S1 > 0)
    S1_safe = pl.when(S1 > 0).then(S1).otherwise(1.0)
    p = K / S1_safe
    pq = p * (1 - p)

    num = Q - p * K - (J - 1) * pq
    den = pq * (S1 - S2 / S1_safe - (J - 1))
    rho = (
        pl.when((den > 0) & (p > 0) & (p < 1))
        .then((num / den).clip(RHO_MIN, RHO_MAX))
        .otherwise(RHO_MIN)
    )
    # "Half a vote": p_hat = 0 or 1 has no valid Beta parameterisation, and the
    # baseline having seen zero of something is not proof it cannot happen.
    half = 0.5 / S1_safe
    return [
        usable.alias(f"{prefix}_ok"),
        p.clip(half, 1 - half).alias(f"{prefix}_p"),
        rho.alias(f"{prefix}_rho"),
    ]


_SUFFICIENT = [
    pl.len().cast(pl.Float64).alias("J"),
    pl.col("n").sum().alias("S1"),
    pl.col("n2").sum().alias("S2"),
    pl.col("k").sum().alias("K"),
    pl.col("q").sum().alias("Q"),
]


def _stats(frame: pl.DataFrame, keys: list[str], prefix: str) -> pl.DataFrame:
    return frame.group_by(keys).agg(
        [expr.alias(f"{prefix}_{expr.meta.output_name()}") for expr in _SUFFICIENT]
    )


def score_frame(dense: pl.DataFrame) -> pl.DataFrame:
    """One UF's dense ``(section, round, candidate)`` grid -> its score rows.

    ``dense`` carries ``section_id, round, mun_code, cand, n, k``.  Everything
    from here to the returned frame is pure: no database, no state.
    """
    dense = dense.with_columns(
        pl.col("n").cast(pl.Float64), pl.col("k").cast(pl.Float64)
    ).with_columns(
        (pl.col("n") * pl.col("n")).alias("n2"),
        (pl.col("k") * pl.col("k") / pl.col("n")).alias("q"),
    )

    mun_keys = ["mun_code", "round", "cand"]
    state_keys = ["round", "cand"]
    dense = dense.join(_stats(dense, mun_keys, "mun"), on=mun_keys, how="left").join(
        _stats(dense, state_keys, "st"), on=state_keys, how="left"
    )

    dense = dense.with_columns(*_loo("mun"), *_loo("st")).filter(
        pl.col("mun_ok") | pl.col("st_ok")
    )
    if dense.height == 0:
        return dense.head(0)

    use_mun = pl.col("mun_ok")
    dense = dense.with_columns(
        pl.when(use_mun).then(pl.col("mun_p")).otherwise(pl.col("st_p")).alias("p_hat"),
        pl.when(use_mun)
        .then(pl.col("mun_rho"))
        .otherwise(pl.col("st_rho"))
        .alias("rho_hat"),
        pl.when(use_mun)
        .then(pl.lit("municipality"))
        .otherwise(pl.lit("state"))
        .alias("baseline"),
    )
    return _tails(dense)


def section_scores(scored: pl.DataFrame) -> pl.DataFrame:
    """Collapse per-candidate rows to ONE row per ``(section, race, round)``.

    The composite (Task 18) reads a section's score, and the primary key
    ``(detector, section_id, race, round)`` allows exactly one -- so the
    section's score is the maximum over its scored candidates and ``detail``
    describes the candidate that produced it.  Ties on the saturated score are
    broken by ``rank`` (the unfloored tail), then by observed share, then by
    candidate number, so the choice is deterministic.
    """
    best = scored.sort(
        ["rank", "score", "k", "cand"], descending=[True, True, True, False]
    ).unique(subset=["section_id", "round"], keep="first", maintain_order=True)
    return best.select(
        pl.col("section_id").cast(pl.Int64),
        pl.lit(RACE).alias("race"),
        pl.col("round").cast(pl.Int16),
        pl.col("score"),
        pl.struct(
            pl.col("cand").cast(pl.Int64),
            pl.col("k").cast(pl.Int64),
            pl.col("n").cast(pl.Int64),
            pl.col("p_hat").round(9),
            pl.col("rho_hat").round(9),
            pl.col("p_hi"),
            pl.col("p_lo"),
            pl.col("baseline"),
        ).alias("detail"),
    )


# --------------------------------------------------------------------------
# Reading the warehouse
# --------------------------------------------------------------------------

#: n_i -- nominal votes in the section, summed over EVERY candidate votável
#: present (annulled candidacies included; blank/null/legenda excluded).
#: ``HAVING`` drops the sections with no denominator.
_SECTIONS = f"""
    SELECT f.section_id, f.round::INT AS round, d.uf, d.mun_code,
           SUM(f.votes)::BIGINT AS n
    FROM fact_votes f
    JOIN dim_section d ON d.id = f.section_id
    WHERE f.race = '{RACE}' AND f.votavel_kind = 'candidate'
    GROUP BY 1, 2, 3, 4
    HAVING SUM(f.votes) > 0
"""

#: The national ranked list per round -- what the >= 1 % filter is applied to.
_NATIONAL = f"""
    SELECT f.round::INT AS round, c.number::INT AS cand, MIN(c.name) AS name,
           SUM(f.votes)::BIGINT AS votes
    FROM fact_votes f
    JOIN dim_candidate c ON c.id = f.candidate_id
    WHERE f.race = '{RACE}' AND f.votavel_kind = 'candidate'
    GROUP BY 1, 2
"""

#: k_i, for the selected candidates only.  Sparse by nature -- densified in
#: :meth:`ExtremeDominance._dense`.
_SECTION_CANDIDATE = f"""
    SELECT f.section_id, f.round::INT AS round, d.uf, c.number::INT AS cand,
           SUM(f.votes)::BIGINT AS k
    FROM fact_votes f
    JOIN dim_candidate c ON c.id = f.candidate_id
    JOIN dim_section d ON d.id = f.section_id
    WHERE f.race = '{RACE}' AND f.votavel_kind = 'candidate'
      AND c.number = ANY(%s)
    GROUP BY 1, 2, 3, 4
"""


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


class ExtremeDominance(BaseDetector):
    """How improbable is this section, given how the rest of its municipality
    voted?  Beta-binomial, leave-one-out, both tails.

    Scores ``(race='PRES', round)`` rows, one per section per round, carrying
    the maximum over the round's scored candidates.
    """

    name = DETECTOR
    #: p < 1e-4.  A section at or above this is what the summary counts.
    flag_threshold = 4.0

    def params(self) -> dict[str, Any]:
        return {
            "race": RACE,
            "rounds": "every round present for the race",
            "candidates": f"national nominal share >= {MIN_NATIONAL_SHARE:.0%} in that round",
            "model": "beta-binomial; rho by ANOVA method of moments (Ridout-Demetrio-Firth)",
            "baseline": "leave-one-out, by subtraction of the section's sufficient statistics",
            "baseline_scope": (
                f"the municipality when >= {MIN_BASELINE_SECTIONS} other sections "
                "remain, otherwise the UF (leave-one-out there too)"
            ),
            "min_baseline_sections": MIN_BASELINE_SECTIONS,
            "rho_clamp": [RHO_MIN, RHO_MAX],
            "p_hat_clamp": "half a vote: [0.5/S1, 1 - 0.5/S1]",
            "score": "-log10(max(min(p_hi, p_lo), 1e-16)), so both tails count",
            "section_score": "max over the round's scored candidates; detail is that candidate",
            "sections_with_no_nominal_votes": (
                "NOT scored -- no denominator, so no question; the deliberate "
                "exception to 'clean sections score 0'"
            ),
            "sections_with_no_computable_baseline": "NOT scored, counted in the summary",
            "annulled_candidacies": (
                "counted in n_i -- the urna's nominal total, as in Tier 1 (A4); "
                "never a scored candidate (far below the national-share floor)"
            ),
            "zero_vote_rows": "densified: an absent (section, candidate) pair is k = 0",
        }

    # -- compute -----------------------------------------------------------

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        picks, national = self._selected_candidates(db)
        if picks.height == 0:
            return DetectorResult({"candidates": national, "note": "no presidential votes"})

        sections = db.fetch_frame(_SECTIONS)
        votes = db.fetch_frame(
            _SECTION_CANDIDATE, (sorted({int(c) for c in picks["cand"]}),)
        )

        offered = 0
        skipped = 0
        # One UF at a time: municipality and state baselines never cross a
        # state line, so this is exact, and it bounds peak memory by the
        # largest UF rather than by Brazil (which is what makes Task 23's
        # national run fit).
        for uf in sections["uf"].unique(maintain_order=True).to_list():
            dense = self._dense(
                sections.filter(pl.col("uf") == uf),
                votes.filter(pl.col("uf") == uf),
                picks,
            )
            offered += dense.height
            scored = score_frame(dense)
            skipped += dense.height - scored.height
            if scored.height:
                scores.add_frame(section_scores(scored))

        scores.flush()
        return DetectorResult(self._summary(db, national, offered, skipped))

    @staticmethod
    def _dense(
        sections: pl.DataFrame, votes: pl.DataFrame, picks: pl.DataFrame
    ) -> pl.DataFrame:
        """Every ``(section, round)`` x every candidate selected in that round.

        The join on ``round`` alone is the cross product inside a round, which
        is what turns TSE's sparse rows into the ``k = 0`` observations this
        detector is about.
        """
        return (
            sections.select("section_id", "round", "mun_code", "n")
            .join(picks, on="round", how="inner")
            .join(
                votes.select("section_id", "round", "cand", "k"),
                on=["section_id", "round", "cand"],
                how="left",
            )
            .with_columns(pl.col("k").fill_null(0))
        )

    @staticmethod
    def _selected_candidates(db: Database) -> tuple[pl.DataFrame, dict[str, Any]]:
        """The >= 1 % candidates per round, plus the full ranked list for the
        summary (so a reader can see who was left out, and by how little)."""
        national = db.fetch_frame(_NATIONAL)
        if national.height == 0:
            return national, {}
        national = national.with_columns(
            (pl.col("votes") / pl.col("votes").sum().over("round")).alias("share")
        ).sort(["round", "votes"], descending=[False, True])
        picks = national.filter(pl.col("share") >= MIN_NATIONAL_SHARE)
        ranked: dict[str, Any] = {}
        for row in national.iter_rows(named=True):
            ranked.setdefault(str(row["round"]), []).append(
                {
                    "number": int(row["cand"]),
                    "name": row["name"],
                    "votes": int(row["votes"]),
                    "share": round(float(row["share"]), 6),
                    "scored": bool(row["share"] >= MIN_NATIONAL_SHARE),
                }
            )
        return picks.select("round", "cand"), ranked

    # -- summary -----------------------------------------------------------

    def _summary(
        self,
        db: Database,
        national: dict[str, Any],
        offered: int,
        skipped: int,
    ) -> dict[str, Any]:
        """Read back what was just written (still inside ``run``'s transaction).

        Everything here is a statement about the detector's own output, which
        is the only way to judge whether a run is a finding or an artefact:
        how many sections cleared the alarm level, what the baseline mix was,
        and -- the one that catches a collapsed estimator -- how often ``rho``
        landed on its floor.
        """
        by_round = {
            str(r): {"scored": int(s), "flagged": int(f)}
            for r, s, f in db.fetch(
                f"""
                SELECT round, count(*), count(*) FILTER (WHERE score >= %s)
                FROM {SCORES_TABLE} WHERE detector = %s GROUP BY 1 ORDER BY 1
                """,
                (self.flag_threshold, self.name),
            )
        }
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
                       ORDER BY (detail ->> 'rho_hat')::DOUBLE PRECISION),
                   percentile_cont(0.95) WITHIN GROUP (
                       ORDER BY (detail ->> 'rho_hat')::DOUBLE PRECISION),
                   count(*) FILTER (
                       WHERE (detail ->> 'rho_hat')::DOUBLE PRECISION <= %s),
                   count(*)
            FROM {SCORES_TABLE} WHERE detector = %s
            """,
            (RHO_MIN, self.name),
        )[0]
        rows = int(stats[6] or 0)
        return {
            "candidates": national,
            "national_share_floor": MIN_NATIONAL_SHARE,
            "by_round": by_round,
            "min_tail_below_1e_4": sum(v["flagged"] for v in by_round.values()),
            "baseline_mix": baselines,
            "candidate_observations": offered,
            "observations_without_a_baseline": skipped,
            "score": {
                "p50": _f(stats[0]),
                "p95": _f(stats[1]),
                "max": _f(stats[2]),
            },
            "rho_hat": {
                "p50": _f(stats[3]),
                "p95": _f(stats[4]),
                "at_floor": int(stats[5] or 0),
                "at_floor_share": round((int(stats[5] or 0) / rows), 6) if rows else None,
            },
            "worst": _worst_sections(db, self.name),
        }


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _worst_sections(db: Database, detector: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.fetch(
        f"""
        SELECT s.section_id, d.uf, d.mun_code, m.name, d.zone, d.section,
               s.round, s.score, s.detail
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
            "round": int(r[6]),
            "score": round(float(r[7]), 4),
            "detail": r[8],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


def read_scores(db: Database, detector: str = DETECTOR) -> pl.DataFrame:
    """This detector's section scores, ``detail`` as a polars struct."""
    return _read_scores(db, detector)
