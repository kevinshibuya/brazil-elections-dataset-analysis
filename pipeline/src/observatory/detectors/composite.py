"""Tier 3 -- the composite score, and where the high sections cluster.

THE QUESTION THIS ANSWERS
    Every detector before this one asks its own question on its own scale: a
    count of violated rules, a ``-log10 p``, a robust ``|z| / 2``.  Those
    numbers are not comparable, and a reader who wants "which sections look
    worst overall" cannot get there by adding them up.

    So each contributor's score is first replaced by its **rank** inside that
    detector's own population -- a percentile, which is unit-free by
    construction -- and the composite is the mean of the percentiles a section
    actually has.  A section that no detector could examine gets no composite
    at all rather than a flattering zero.

MEAN-RANK, AND WHY THE TIE RULE IS THE WHOLE DESIGN
    Ties here are not an edge case, they are the bulk of the data: Tier 1
    scores 0 for every section that adds up, which nationally is ~99.9 % of
    them.  Under a *minimum* rank every one of those sections would sit at
    percentile ~0; under an *ordinal* rank they would be spread from 0 to 0.999
    in warehouse scan order, which is not a statistic at all.  The mean rank
    puts the whole tied block at the middle of the range it occupies, which is
    the only answer that does not depend on how the ties were listed::

        percentile_i = mean_rank(score_i) / n         in (0, 1]

    The population is the contributor's own scored sections **in that round**:
    round 1 offers six races and round 2 offers one, so a round-2 section
    ranked against round-1 sections would be ranked against a different
    question.

THE CONTRIBUTORS, AND WHY THESE
    Only detectors that produce a per-section score can contribute.  The
    distribution-level detectors (``fingerprint``, ``digits``, ``urna_model``)
    describe a *population* -- a state's turnout/share cloud, a stratum's digit
    histogram, a fleet's vote share -- and have no per-section number to rank,
    so they are excluded here and reported on their own pages.

        t1_arithmetic         max over the section's races in the round
        t1_presence           the round's cross-race row
        extreme_dominance     PRES, the round
        turnout               PRES, the round
        split_ticket          its round-1 score, in BOTH rounds

    ``split_ticket`` is measured against federal deputies, who are elected in
    round 1 only; the split-ticket habit of a section does not stop existing
    three weeks later, so its single score contributes to both composite rounds
    (see ``split_ticket.params()['round_1_feeds_both_composite_rounds']``).

    A contributor that has not run is simply absent from the mean.  That is a
    hard requirement, not a convenience: this detector runs last, and a partial
    analysis must produce a partial composite rather than an exception.

FEWER THAN TWO CONTRIBUTORS -> NO ROW
    A "composite" of one detector is that detector, wearing a name that claims
    otherwise.  Below :data:`MIN_CONTRIBUTORS` nothing is written -- which is
    also what lets ``analysis.section_scores.score`` stay NOT NULL, since there
    is no such thing as a composite with no value.

THE HIGH SET IS A TOP-K, NOT A PERCENTILE CUT
    The national high set is exactly the top ``ceil(0.01 * N)`` sections by
    composite **per round**, ties broken by ``(composite desc, mun_code asc,
    zone asc, section asc)``.  A ``percentile >= 99`` cut does NOT select 1 %
    here: the composite inherits the tie blocks of its contributors, and a
    single tied block straddling the cut either takes far more than 1 % or
    (under mean-rank) none of it.  ``detail.high`` is therefore the
    authoritative membership flag and ``detail.composite_percentile`` is
    informational -- they are allowed to disagree, and the top-K rule wins.

CLUSTERING: THE NULL IS HYPERGEOMETRIC, NOT BINOMIAL
    Having fixed K highs among N sections, ask whether a zone holds more of
    them than chance allows.  The K is *fixed by construction* -- we chose it --
    so the highs are drawn WITHOUT replacement and a zone of ``n_z`` sections
    holding ``x`` of them has

        p_z = P(X >= x),   X ~ Hypergeometric(N, K, n_z)

    A Binomial(n_z, 0.01) null would treat each section as an independent coin
    flip, which over-disperses the count (it allows more than K highs
    nationally) and therefore *understates* how surprising a hot zone is.  With
    the numbers this repo actually runs the difference is large, not cosmetic.

    Every zone is tested, then Benjamini-Hochberg at q = 0.05 across the whole
    family -- one family per (level, round), because the two rounds have
    different high sets and are different experiments.  The same computation
    runs at municipality level.  A permutation refinement (which would also
    absorb the fact that zones differ in size in ways the hypergeometric does
    not model) is out of scope for v1.

THE FAR TAIL IS ARITHMETIC, AND ARITHMETIC CAN LIE (carried from Task 7)
    ``extreme_dominance`` found ``scipy``'s ``betabinom.sf`` returning 3.6e-14
    where the truth was 7.0e-41, because ``rv_discrete`` computes ``sf`` as
    ``1 - cdf`` and that subtraction cancels catastrophically in the tail.  The
    planted hot zone here (x=10 of K=20 highs in a 40-section zone out of 2 000)
    is exactly that kind of tail, so ``hypergeom.sf`` is not trusted blind:

      * ``hypergeom`` DOES have a dedicated ``_sf``, and it was measured against
        exact rational arithmetic (``math.comb`` + ``fractions.Fraction``) at
        p = 4.9e-13, 3.5e-37, 1.3e-44, 1.1e-87 and 9.4e-302 -- agreeing to
        ~1e-15 relative in every case.  So it is used for the common path.
      * Below :data:`CANCELLATION_SAFE_P` the answer is recomputed as a
        log-space sum of PMF terms (:func:`_log_survival`), which cannot cancel
        because every term is positive, and THAT value is the one returned.
      * The two are compared and the worst relative disagreement travels in
        ``detector_summary.summary['tail_check']``, so a future scipy that
        regresses shows up in the output instead of in the findings.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

import numpy as np
import polars as pl
from scipy.special import logsumexp
from scipy.stats import hypergeom

from ..db import Database
from ..export.shards import COMPOSITE_DETECTOR, score_key
from .base import (
    CROSS_RACE,
    SCORES_TABLE,
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    ensure_analysis_schema,
    summary_of,
)

__all__ = [
    "BH_Q",
    "CONTRIBUTORS",
    "HIGH_FRACTION",
    "MIN_CONTRIBUTORS",
    "Composite",
    "Contributor",
    "benjamini_hochberg",
    "read_clusters",
    "read_scores",
    "survival",
]

#: The detector name.  It lives in ``export.shards`` because the exporter's
#: frozen ``score_detail`` rule is written in terms of it and the dependency
#: may only point one way: ``detectors`` imports ``export.shards``, never the
#: reverse.
DETECTOR = COMPOSITE_DETECTOR

#: ``composite`` carries no tier prefix, so the shard key is the name itself.
SCORE_KEY = score_key(DETECTOR)

#: The rounds a composite row may be written for.
ROUNDS: tuple[int, ...] = (1, 2)

#: Below this, a "composite" would be one detector under an assumed name.
MIN_CONTRIBUTORS = 2

#: Share of sections in the national high set, per round.  ``ceil``, so a small
#: warehouse still has one.
HIGH_FRACTION = 0.01

#: Benjamini-Hochberg false-discovery rate for the clustering test.
BH_Q = 0.05

#: Below this p, ``hypergeom.sf`` is re-derived without subtraction.  1e-8 is
#: far above anything a 1 - cdf could plausibly damage and far below any p that
#: BH could confuse, so the switch never changes a verdict -- it only removes
#: the possibility that a future scipy would.
CANCELLATION_SAFE_P = 1e-8

#: Clustering levels, in report order.  ``zone`` first: it is the finest unit
#: the electoral administration actually manages (one *cartório eleitoral*, one
#: set of mesários, one custody chain), and therefore the one where a real
#: concentration would have a mechanism.
LEVELS: tuple[str, ...] = ("zone", "municipality")

#: What the summary's ``worst`` list holds.
WORST_LIMIT = 20


# --------------------------------------------------------------------------
# Contributors
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Contributor:
    """One per-section detector feeding the composite.

    ``race`` is the race whose row is read (``None`` = every race the section
    reports, collapsed with ``max``).  ``source_round`` pins the round the
    score is READ from when it differs from the round it contributes TO --
    only ``split_ticket`` does that.
    """

    detector: str
    race: str | None = None
    source_round: int | None = None
    why: str = ""

    @property
    def key(self) -> str:
        """The name this contributor appears under in ``detail.percentiles``."""
        return score_key(self.detector)


#: The composite's inputs.  Distribution-level detectors are deliberately
#: absent -- they have no per-section number to rank (see the module docstring).
CONTRIBUTORS: tuple[Contributor, ...] = (
    Contributor(
        "t1_arithmetic",
        race=None,
        why="max over the section's races in the round: one broken race is a broken section",
    ),
    Contributor(
        "t1_presence",
        race=CROSS_RACE,
        why="already cross-race, already per round",
    ),
    Contributor(
        "t2_extreme_dominance",
        race="PRES",
        why="presidential only, per round -- the v1 scope of the beta-binomial",
    ),
    Contributor(
        "t2_turnout",
        race="PRES",
        why="the one race every section reports, so turnout is comparable everywhere",
    ),
    Contributor(
        "t2_split_ticket",
        race=CROSS_RACE,
        source_round=1,
        why="deputies are elected in round 1; the habit it measures spans both rounds",
    ),
)

#: One contributor's ``(section, round) -> score``.  ``max`` collapses the
#: several races a section may report; for a single-race contributor it is the
#: identity.  The race predicate is written so a NULL race means "every race"
#: without a second query shape.
_CONTRIBUTION = f"""
    SELECT section_id, round::INT AS round, MAX(score) AS score
    FROM {SCORES_TABLE}
    WHERE detector = %s AND (%s::TEXT IS NULL OR race = %s::TEXT)
    GROUP BY 1, 2
"""

_GEOGRAPHY = "SELECT id AS section_id, uf, mun_code, zone, section FROM dim_section"


# --------------------------------------------------------------------------
# The maths -- pure, no database
# --------------------------------------------------------------------------


def mean_rank_percentile(scores: pl.Expr, group: str | Sequence[str]) -> pl.Expr:
    """``mean_rank / n`` within ``group`` -- the composite's only rescaling.

    ``method="average"`` is the binding choice (see the module docstring): a
    tied block lands on the midpoint of the ranks it occupies, so the value a
    section gets cannot depend on the order the warehouse returned it in.
    """
    return scores.rank(method="average").over(group) / pl.len().over(group)


class Tail(NamedTuple):
    """A hypergeometric upper tail, and how it was arrived at."""

    p: float
    sf: float           # what scipy's hypergeom.sf said, for the record
    method: str         # "sf" | "logsumexp" | "exact"


def survival(x: int, N: int, K: int, n: int) -> Tail:
    """``P(X >= x)`` for ``X ~ Hypergeometric(N, K, n)``.

    The common path is ``scipy.stats.hypergeom.sf(x - 1, ...)``; in the far
    tail the same quantity is re-derived as a log-space sum of PMF terms, which
    has no subtraction in it and therefore nothing to cancel.  See the module
    docstring for what that guards against and what it was measured on.
    """
    x, N, K, n = int(x), int(N), int(K), int(n)
    lo, hi = max(0, n - (N - K)), min(K, n)
    if x <= lo:
        return Tail(1.0, 1.0, "exact")          # every draw already reaches x
    if x > hi:
        return Tail(0.0, 0.0, "exact")          # unreachable
    sf = float(hypergeom.sf(x - 1, N, K, n))
    if sf > CANCELLATION_SAFE_P:
        return Tail(sf, sf, "sf")
    return Tail(_log_survival(x, hi, N, K, n), sf, "logsumexp")


def _log_survival(x: int, hi: int, N: int, K: int, n: int) -> float:
    """``sum_{i=x}^{hi} pmf(i)``, summed in log space.  All terms positive."""
    terms = np.arange(x, hi + 1)
    return float(np.exp(logsumexp(hypergeom.logpmf(terms, N, K, n))))


def benjamini_hochberg(
    pvalues: Sequence[float], q: float = BH_Q
) -> tuple[list[int], float]:
    """Indices surviving BH at ``q``, and the p-value cutoff that produced them.

    The LARGEST rank satisfying ``p_(i) <= i q / m`` wins, and everything below
    it survives with it -- the step-up rule.  Taking the first failure instead
    is the classic BH bug and would lose a cluster sitting behind a tie.
    """
    m = len(pvalues)
    if not m:
        return [], 0.0
    order = sorted(range(m), key=lambda i: (pvalues[i], i))
    cut = 0
    for rank, index in enumerate(order, 1):
        if pvalues[index] <= rank * q / m:
            cut = rank
    return order[:cut], (pvalues[order[cut - 1]] if cut else 0.0)


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


@dataclass
class _Round:
    """One round's composite population, once the high set is known."""

    round: int
    sections: int
    high: int
    min_high_score: float | None
    high_rows: set[int]


class Composite(BaseDetector):
    """The per-section composite, plus the clustering test on its high set.

    Scores ``(race='ALL', round=r)`` for r in 1, 2 -- one row per section with
    at least :data:`MIN_CONTRIBUTORS` contributors in that round.
    """

    name = DETECTOR

    #: Overwritten by :meth:`compute` with the weakest score in the national
    #: high set, so "flagged" means "at least as anomalous as the weakest
    #: section the clustering test treated as high".  The framework reads it
    #: after ``compute`` returns (for ``_counts`` and ``params``), which is what
    #: makes a run-time value legitimate here; it is reset every run so a run
    #: that writes nothing cannot inherit the previous one's cut.
    flag_threshold = 1.0

    def params(self) -> dict[str, Any]:
        return {
            "grain": f"one row per (section, race='{CROSS_RACE}', round), rounds {list(ROUNDS)}",
            "contributors": {
                c.key: {
                    "detector": c.detector,
                    "race": c.race or "every race the section reports (max)",
                    "source_round": c.source_round or "the composite's own round",
                    "why": c.why,
                }
                for c in CONTRIBUTORS
            },
            "excluded": (
                "fingerprint, digits, urna_model -- distribution-level detectors "
                "with no per-section score to rank"
            ),
            "percentile": (
                "mean-rank percentile (rank method 'average') / n, within the "
                "contributor's own scored sections FOR THAT ROUND"
            ),
            "tie_handling": (
                "mean rank, binding: tier-1 zeros are ~99.9 % of the country and "
                "a min-rank or ordinal rank would answer a different question"
            ),
            "composite": "mean of the available percentiles",
            "min_contributors": MIN_CONTRIBUTORS,
            "fewer_contributors": "NO ROW written -- a composite of one is not a composite",
            "missing_detector": "tolerated: it drops out of the mean, it does not stop the run",
            "high_set": (
                f"top ceil({HIGH_FRACTION} * N) by composite PER ROUND, ties broken by "
                "(composite desc, mun_code asc, zone asc, section asc); a raw "
                "percentile >= 99 cut does not select 1 % under ties"
            ),
            "null_model": (
                "hypergeometric: p_z = P(X >= x | N, K, n_z), fixed-K draws WITHOUT "
                "replacement. A Binomial(n_z, 0.01) null is wrong here -- it allows "
                "more than K highs nationally and so understates a hot zone"
            ),
            "multiple_testing": (
                f"Benjamini-Hochberg at q = {BH_Q}, one family per (level, round)"
            ),
            "levels": list(LEVELS),
            "tail_arithmetic": (
                f"hypergeom.sf above p = {CANCELLATION_SAFE_P:g}; below it, a "
                "log-space sum of PMF terms (no subtraction, so no cancellation). "
                "Both are computed and their disagreement is reported in "
                "summary.tail_check"
            ),
            "permutation_refinement": "out of scope for v1",
        }

    # -- compute -----------------------------------------------------------

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        self.flag_threshold = type(self).flag_threshold      # reset, every run
        contributions, present, absent = self._contributions(db)
        if contributions is None:
            return DetectorResult(self._empty_summary(present, absent))

        frame, percentiles = self._composite(db, contributions)
        if frame.height == 0:
            return DetectorResult(self._empty_summary(present, absent))

        rounds = self._high_sets(frame)
        frame = self._with_percentile(frame, rounds)
        scores.add_frame(self._score_rows(frame, percentiles))
        scores.flush()

        clusters, tests, tail_check = self._clusters(db, frame, rounds)
        cuts = [r.min_high_score for r in rounds if r.min_high_score is not None]
        if cuts:
            self.flag_threshold = min(cuts)

        # A scored section with no ``dim_section`` row has a composite but no
        # place, so it is scored and then left out of the clustering test.
        # Reported rather than assumed away: nationally it should be 0, and a
        # non-zero would mean the warehouse's dimension is incomplete.
        ungrouped = int(frame.select(pl.col("mun_code").is_null().sum()).item())

        return DetectorResult(
            self._summary(db, present, absent, rounds, clusters, tests,
                          tail_check, ungrouped)
        )

    # -- step 1: every contributor's percentile ----------------------------

    def _contributions(
        self, db: Database
    ) -> tuple[pl.DataFrame | None, list[str], list[str]]:
        """``(section_id, round, key, pct)`` for every contributor that ran.

        Each contributor is ranked inside ITS OWN population before anything is
        combined, which is the whole point: the frames that meet here carry
        percentiles, never scores.
        """
        frames: list[pl.DataFrame] = []
        present: list[str] = []
        absent: list[str] = []
        for contributor in CONTRIBUTORS:
            rows = db.fetch_frame(
                _CONTRIBUTION,
                (contributor.detector, contributor.race, contributor.race),
            )
            if rows.height == 0:
                absent.append(contributor.key)
                continue
            present.append(contributor.key)
            frames.append(self._percentiles(rows, contributor))

        if not frames:
            return None, present, absent
        return pl.concat(frames, how="vertical"), present, absent

    @staticmethod
    def _percentiles(rows: pl.DataFrame, contributor: Contributor) -> pl.DataFrame:
        """One contributor's rows -> ``(section_id, round, key, pct)``.

        ``round`` on the way out is the round the percentile CONTRIBUTES to; the
        ranking population is always the round it was READ from.
        """
        ranked = rows.with_columns(
            mean_rank_percentile(pl.col("score"), "round").alias("pct")
        )
        if contributor.source_round is None:
            ranked = ranked.with_columns(pl.col("round").cast(pl.Int32))
        else:
            source = ranked.filter(pl.col("round") == contributor.source_round)
            ranked = pl.concat(
                [
                    source.with_columns(pl.lit(target, dtype=pl.Int32).alias("round"))
                    for target in ROUNDS
                ],
                how="vertical",
            )
        return ranked.select(
            pl.col("section_id").cast(pl.Int64),
            pl.col("round").cast(pl.Int32),
            pl.lit(contributor.key).alias("key"),
            pl.col("pct").cast(pl.Float64),
        )

    # -- step 2: the composite itself --------------------------------------

    def _composite(
        self, db: Database, contributions: pl.DataFrame
    ) -> tuple[pl.DataFrame, list[dict[str, float]]]:
        """The scored frame, plus each row's percentile map (aligned by index).

        The mean is taken in Python, over the percentile values in the same
        order they are written into ``detail`` -- so ``score`` is bit-for-bit
        ``sum(detail['percentiles'].values()) / detail['n_detectors']`` rather
        than merely equal to it up to summation order.
        """
        grouped = (
            contributions.group_by(["section_id", "round"])
            .agg(pl.col("key"), pl.col("pct"))
            .filter(pl.col("key").list.len() >= MIN_CONTRIBUTORS)
            .join(db.fetch_frame(_GEOGRAPHY), on="section_id", how="left")
        )
        if grouped.height == 0:
            return grouped.head(0), []

        percentiles: list[dict[str, float]] = []
        scores: list[float] = []
        for keys, values in grouped.select("key", "pct").iter_rows():
            ordered = dict(sorted(zip(keys, values, strict=True)))
            percentiles.append(ordered)
            scores.append(sum(ordered.values()) / len(ordered))

        # ``row`` is the index into ``percentiles``; everything downstream --
        # the high set, the clustering groups -- refers to a row by it, so the
        # two lists can never drift apart under a sort.
        return (
            grouped.drop("key", "pct")
            .with_columns(pl.Series("score", scores, dtype=pl.Float64))
            .with_row_index("row"),
            percentiles,
        )

    # -- step 3: the national high set, per round --------------------------

    @staticmethod
    def _high_sets(frame: pl.DataFrame) -> list[_Round]:
        """Top ``ceil(0.01 N)`` per round, by the plan's exact ordering."""
        out: list[_Round] = []
        for (rnd,) in frame.select("round").unique().sort("round").iter_rows():
            sub = frame.filter(pl.col("round") == rnd).sort(
                ["score", "mun_code", "zone", "section"],
                descending=[True, False, False, False],
                nulls_last=True,
            )
            n = sub.height
            k = min(n, math.ceil(HIGH_FRACTION * n))
            head = sub.head(k)
            out.append(
                _Round(
                    round=int(rnd),
                    sections=n,
                    high=k,
                    min_high_score=(float(head["score"][-1]) if k else None),
                    high_rows=set(head["row"].to_list()),
                )
            )
        return out

    @staticmethod
    def _with_percentile(frame: pl.DataFrame, rounds: list[_Round]) -> pl.DataFrame:
        """Attach the composite's own mean-rank percentile and ``high``."""
        high = set().union(*(r.high_rows for r in rounds)) if rounds else set()
        return frame.with_columns(
            mean_rank_percentile(pl.col("score"), "round").alias("composite_percentile"),
            pl.col("row").is_in(list(high)).alias("high"),
        )

    # -- step 4: the score rows --------------------------------------------

    @staticmethod
    def _score_rows(
        frame: pl.DataFrame,
        percentiles: list[dict[str, float]],
    ) -> pl.DataFrame:
        """The frame ``ScoreWriter`` wants, ``detail`` already JSON.

        ``detail`` carries the SAME four keys on every row.  ``percentiles`` is
        the one nested object whose key set legitimately varies (a section is
        not obliged to have been examined by everything), which is exactly why
        :func:`read_scores` below decodes it in Python instead of letting polars
        infer one struct schema from the leading rows and drop the rest.
        """
        details = [
            json.dumps(
                {
                    "percentiles": percentiles[row],
                    "n_detectors": len(percentiles[row]),
                    "composite_percentile": pct,
                    "high": bool(high),
                },
                allow_nan=False,
            )
            for row, pct, high in frame.select(
                "row", "composite_percentile", "high"
            ).iter_rows()
        ]
        return frame.select(
            pl.col("section_id").cast(pl.Int64),
            pl.lit(CROSS_RACE).alias("race"),
            pl.col("round").cast(pl.Int16),
            pl.col("score").cast(pl.Float64),
            pl.Series("detail", details, dtype=pl.String),
        )

    # -- step 5: clustering -------------------------------------------------

    def _clusters(
        self, db: Database, frame: pl.DataFrame, rounds: list[_Round]
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
        """Surviving zones and municipalities, per level, plus the bookkeeping.

        Every group is tested, including the ones holding no highs at all
        (``p = 1``): they are part of the family the false-discovery rate is
        controlled over, and quietly dropping them would inflate every
        surviving q.
        """
        clusters: dict[str, list[dict[str, Any]]] = {level: [] for level in LEVELS}
        tests: dict[str, Any] = {}
        worst_diff = 0.0
        checked = 0

        names = _municipality_names(db)
        for level in LEVELS:
            # ``uf`` is in both key sets.  At municipality level it is
            # functionally determined by ``mun_code``, so grouping by it changes
            # no group -- it only puts the state on the row, which a reader
            # looking at "zone 1" or "03018" needs and cannot derive.
            keys = ["uf", "zone"] if level == "zone" else ["uf", "mun_code"]
            for spec in rounds:
                sub = frame.filter(
                    (pl.col("round") == spec.round)
                    & pl.all_horizontal(pl.col(k).is_not_null() for k in keys)
                )
                groups = (
                    sub.with_columns(
                        pl.col("row").is_in(list(spec.high_rows)).alias("_high")
                    )
                    .group_by(keys)
                    .agg(
                        pl.len().alias("sections"),
                        pl.col("_high").sum().alias("observed_high"),
                        pl.col("mun_code").unique().sort().alias("municipalities"),
                    )
                    .sort(keys)
                )
                if groups.height == 0:
                    continue

                rows = groups.to_dicts()
                tails = [
                    survival(
                        int(row["observed_high"]), spec.sections, spec.high,
                        int(row["sections"]),
                    )
                    for row in rows
                ]
                for tail in tails:
                    if tail.method != "logsumexp":
                        continue
                    checked += 1
                    if tail.sf > 0 or tail.p > 0:
                        worst_diff = max(worst_diff, _relative(tail.p, tail.sf))

                surviving, cutoff = benjamini_hochberg([t.p for t in tails], BH_Q)
                tests[f"{level}_round{spec.round}"] = {
                    "groups_tested": len(rows),
                    "surviving": len(surviving),
                    "p_cutoff": cutoff,
                    "sections": spec.sections,
                    "high": spec.high,
                }
                clusters[level].extend(
                    self._cluster(level, spec, rows[i], tails[i], len(rows),
                                  cutoff, names)
                    for i in surviving
                )

            clusters[level].sort(key=lambda c: (c["p_value"], c["round"], c["id"]))

        return clusters, tests, {"recomputed_in_log_space": checked,
                                 "max_relative_disagreement": worst_diff}

    @staticmethod
    def _cluster(
        level: str,
        spec: _Round,
        group: dict[str, Any],
        tail: Tail,
        family: int,
        cutoff: float,
        names: dict[str, str],
    ) -> dict[str, Any]:
        """One surviving cluster, carrying enough context to render it.

        A zone is not contained in a municipality -- a zone can span several,
        and a big municipality can hold several zones -- so a zone-level cluster
        names every municipality it touches rather than pretending to have one.
        """
        codes = [str(code) for code in (group.get("municipalities") or [])]
        zone = group.get("zone")
        expected = spec.high * int(group["sections"]) / spec.sections
        return {
            "level": level,
            "id": (
                f"{group.get('uf')}-{int(zone)}" if level == "zone"
                else str(group.get("mun_code"))
            ),
            "round": spec.round,
            "uf": group.get("uf"),
            "zone": None if zone is None else int(zone),
            "mun_code": group.get("mun_code"),
            "mun_name": names.get(str(group.get("mun_code"))),
            "municipalities": [
                {"code": code, "name": names.get(code)} for code in codes
            ],
            "sections": int(group["sections"]),
            "observed_high": int(group["observed_high"]),
            "expected_high": round(expected, 6),
            "lift": round(int(group["observed_high"]) / expected, 4) if expected else None,
            "p_value": tail.p,
            "p_method": tail.method,
            "bh_cutoff": cutoff,
            "family_size": family,
            "national": {"sections": spec.sections, "high": spec.high},
        }

    # -- summaries ----------------------------------------------------------

    def _empty_summary(self, present: list[str], absent: list[str]) -> dict[str, Any]:
        return {
            "score_key": SCORE_KEY,
            "contributors_present": present,
            "contributors_absent": absent,
            "min_contributors": MIN_CONTRIBUTORS,
            "scored_without_geography": 0,
            "note": (
                f"fewer than {MIN_CONTRIBUTORS} contributors scored any section; "
                "no composite row is written"
            ),
            "rounds": {},
            "clusters": {level: [] for level in LEVELS},
            "clustering": self._clustering_params(),
            "tail_check": {"recomputed_in_log_space": 0,
                           "max_relative_disagreement": 0.0},
            "worst": [],
        }

    @staticmethod
    def _clustering_params() -> dict[str, Any]:
        return {
            "q": BH_Q,
            "levels": list(LEVELS),
            "null_model": "hypergeometric",
            "null_model_detail": (
                "P(X >= x | N sections, K highs, n_g in the group) -- fixed-K draws "
                "without replacement, NOT Binomial(0.01)"
            ),
            "family": "one Benjamini-Hochberg family per (level, round)",
        }

    def _summary(
        self,
        db: Database,
        present: list[str],
        absent: list[str],
        rounds: list[_Round],
        clusters: dict[str, list[dict[str, Any]]],
        tests: dict[str, Any],
        tail_check: dict[str, Any],
        ungrouped: int,
    ) -> dict[str, Any]:
        return {
            "score_key": SCORE_KEY,
            "contributors_present": present,
            "contributors_absent": absent,
            "min_contributors": MIN_CONTRIBUTORS,
            "scored_without_geography": ungrouped,
            "rounds": {
                str(spec.round): {
                    "sections": spec.sections,
                    "high": spec.high,
                    "high_fraction": HIGH_FRACTION,
                    "min_high_score": spec.min_high_score,
                }
                for spec in rounds
            },
            "contributor_counts": _contributor_counts(db, self.name),
            "score": _score_shape(db, self.name),
            "clusters": clusters,
            "clustering": {**self._clustering_params(), "tests": tests},
            "tail_check": tail_check,
            "worst": _worst_sections(db, self.name),
        }


def _municipality_names(db: Database) -> dict[str, str]:
    """``{code: name}``.  A cluster reported as "03018" is not a finding a
    reader can act on; 5 570 rows is a rounding error next to the score set."""
    return {
        str(code): name
        for code, name in db.fetch("SELECT code, name FROM dim_municipality")
    }


def _relative(value: float, other: float) -> float:
    scale = max(abs(value), abs(other))
    return 0.0 if scale == 0 else abs(value - other) / scale


def _contributor_counts(db: Database, detector: str) -> dict[str, int]:
    """How many composite rows each contributor actually reached.

    The one number that says whether a composite is a composite: a contributor
    present in 3 % of rows is not contributing, it is adding noise to 3 % of
    them.
    """
    return {
        str(key): int(n)
        for key, n in db.fetch(
            f"""
            SELECT k.key, count(*)
            FROM {SCORES_TABLE} s,
                 LATERAL jsonb_object_keys(s.detail -> 'percentiles') AS k(key)
            WHERE s.detector = %s
            GROUP BY 1 ORDER BY 1
            """,
            (detector,),
        )
    }


def _score_shape(db: Database, detector: str) -> dict[str, Any]:
    row = db.fetch(
        f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY score),
               percentile_cont(0.95) WITHIN GROUP (ORDER BY score),
               percentile_cont(0.99) WITHIN GROUP (ORDER BY score),
               max(score),
               avg((detail ->> 'n_detectors')::DOUBLE PRECISION)
        FROM {SCORES_TABLE} WHERE detector = %s
        """,
        (detector,),
    )[0]
    return {
        "p50": _f(row[0]),
        "p95": _f(row[1]),
        "p99": _f(row[2]),
        "max": _f(row[3]),
        "mean_contributors": _f(row[4]),
    }


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _worst_sections(
    db: Database, detector: str, limit: int = WORST_LIMIT
) -> list[dict[str, Any]]:
    rows = db.fetch(
        f"""
        SELECT s.section_id, d.uf, d.mun_code, m.name, d.zone, d.section,
               s.round, s.score, s.detail
        FROM {SCORES_TABLE} s
        LEFT JOIN dim_section d      ON d.id = s.section_id
        LEFT JOIN dim_municipality m ON m.code = d.mun_code
        WHERE s.detector = %s
        ORDER BY s.score DESC, s.section_id, s.round LIMIT %s
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
            "score": round(float(r[7]), 6),
            "detail": r[8],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


#: ``detail`` as TEXT, deliberately: see :func:`read_scores`.
_READ_SCORES = f"""
    SELECT section_id, race, round, score, detail::TEXT AS detail
    FROM {SCORES_TABLE}
    WHERE detector = %s
    ORDER BY section_id, race, round
"""


def read_scores(db: Database, detector: str = DETECTOR) -> pl.DataFrame:
    """This detector's section scores, ``detail`` as real Python dicts.

    Accepts either the warehouse name or the shard score key -- here they are
    the same string, but the signature matches every other detector's so a
    caller holding one of the two never has to know which.

    ``detail`` is deliberately NOT the polars struct the framework's
    :func:`~observatory.detectors.base.read_scores` returns, and this is the one
    detector for which that matters.  Its ``percentiles`` object has a per-row
    key set -- a section examined by four detectors and one examined by two are
    both legitimate -- while polars infers ONE struct schema from the leading
    rows.  Measured on the acceptance fixture, that inference turned a
    two-contributor row's ``percentiles`` into ``{"turnout": None, ...}`` with
    three members against ``n_detectors == 2``, and a row with a key the leading
    rows lacked would have lost it outright.  So the JSON is fetched as TEXT and
    decoded here, into an object column that keeps every row's own key set.
    """
    ensure_analysis_schema(db)
    name = DETECTOR if detector in (DETECTOR, SCORE_KEY) else detector
    frame = db.fetch_frame(_READ_SCORES, (name,))
    if frame.height == 0:
        return frame
    return frame.drop("detail").with_columns(
        pl.Series(
            "detail",
            [None if d is None else json.loads(d) for d in frame["detail"]],
            dtype=pl.Object,
        )
    )


def read_clusters(db: Database, level: str = "zone") -> list[dict[str, Any]]:
    """The clusters that survived Benjamini-Hochberg, at ``level``.

    ``zone`` by default and by contract: a municipality is an administrative
    container, an electoral zone is where ballots are actually managed, so the
    zone list is the one a reader means by "did the high sections cluster".
    Municipality-level results are a separate answer to a separate question and
    are never mixed into this one -- pass ``level="municipality"`` for them.

    ``[]`` when the detector never ran, when nothing survived, or when ``level``
    is not one this detector computes.
    """
    clusters = (summary_of(db, DETECTOR).get("clusters") or {}).get(level) or []
    return list(clusters)
