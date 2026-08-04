"""Tier 2 -- old urnas against new ones, WITHIN municipalities.

THE CLAIM BEING TESTED
    After the 2022 runoff it was widely argued that voting machines of the
    pre-2020 generation returned a systematically higher Bolsonaro share than
    the 2020 model, and that the gap was evidence of manipulation.  The naive
    national comparison does show a large gap.  It is also almost entirely a
    GEOGRAPHY artifact: TSE distributes machines by region and replacement
    cycle, so the old fleet is concentrated where Bolsonaro was strong anyway.

    The honest version of the question compares the two fleets INSIDE the same
    municipality, where the electorate is held (approximately) fixed:

        delta_m = share(pre2020 sections of m) - share(ue2020 sections of m)

    over municipalities that ran BOTH generations.  This detector reports the
    naive gap and the within-municipality one side by side, which is the
    contrast the dashboard draws.  It does not adjudicate the claim; it
    measures how much of the raw gap survives conditioning on municipality.

WHAT A SHARE IS (binding)
    VOTE-WEIGHTED, never a mean of per-section shares: votes are summed within
    a class first and divided once.  A mean of section shares would weight a
    50-voter rural section like a 400-voter urban one and would answer a
    different question from the one the controversy asked.

    The numerator is the subject candidate's nominal (``votavel_kind =
    'candidate'``) presidential votes; the denominator is ALL nominal
    presidential votes of the same sections -- blank and null are not valid and
    there is no presidential legenda.  ZERO-VOTE ROWS DO NOT EXIST in TSE's
    publication, so the numerator is a LEFT JOIN coalesced to 0 while the
    denominator comes from the section's own rows.

ROUND 2, AND ROUND 2's MODEL (binding)
    The statistic is about the runoff, so the class of a section is the model
    it reported IN ROUND 2.  That is not a formality: 5,341 sections report a
    different ``CD_MODELO_URNA`` in round 2 than in round 1 and 2,080 of them
    change class (amendment A6), so classifying on round 1 would put real
    sections in the wrong fleet.  The value is read from
    ``fact_section_stats`` at (section, PRES, 2).

THE CLUSTER-ROBUST CI
    Each municipality is one cluster and contributes exactly one delta_m, so
    the sections inside it are not treated as independent observations.  With
    weights ``w_m``::

        mean = sum(w_m delta_m) / sum(w_m)
        SE   = sqrt(sum(w_m^2 (delta_m - mean)^2)) / sum(w_m)
        CI   = mean +/- 1.96 SE

    which is the usual sandwich form for a weighted mean of cluster-level
    quantities: no distributional assumption inside a municipality, and a
    single municipality with a huge electorate cannot pretend to be a thousand
    independent readings.

UNKNOWN MODELS ARE REPORTED, NOT ABSORBED
    ``-1`` (and NULL) mean TSE published no model for that section.  Those
    sections belong to NEITHER class -- putting them in one would invent data --
    but they are counted in ``model_sections`` so a reader can see how much of
    the country the comparison could not speak about.

NO PER-SECTION SCORES
    Nothing here is a statement about one section: a single section's model is
    a fact about logistics, not an anomaly.  ``sections_scored`` is 0 and the
    composite (Task 18) has nothing of ours to combine.  The output is the
    summary plus per-state payloads.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from ..db import Database
from ..export.shards import score_key
from .base import (
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    read_scores as _read_scores,
)

__all__ = [
    "MODEL_CLASS",
    "PRE2020",
    "UE2020",
    "UNKNOWN",
    "UrnaModel",
    "classify",
    "read_scores",
]

DETECTOR = "t2_urna_model"

#: The shard/aggregate key this detector's output appears under.
SCORE_KEY = score_key(DETECTOR)

#: The one race the controversy was about, and the only one with a runoff.
PRES = "PRES"

#: The runoff.  Binding: both the votes and the model are read at this round.
ROUND = 2

#: The two fleets.
PRE2020 = "pre2020"
UE2020 = "ue2020"
#: Neither fleet: TSE published no model.  The wire value is ``-1``, which
#: ``parse.NA_STRINGS`` already turns into NULL, so what reaches the warehouse
#: is a null -- both spellings map here.
UNKNOWN = "unknown"

#: TSE ``CD_MODELO_URNA`` -> fleet.  The code is the urna GENERATION year, as
#: published in ``detalhe_votacao_secao`` (and catalogued in TSE's
#: ``modelourna_numerointerno``): UE2009/2010/2011/2013/2015 are the pre-2020
#: generations, UE2020 is the current one.  Verified against the 2022 archives
#: (amendment A6): after the A3 election filter these six codes plus ``-1`` are
#: the ONLY values that occur nationally, and every (mun, zone, section, round)
#: key carries exactly one of them.
MODEL_CLASS: dict[str, str] = {
    "2009": PRE2020,
    "2010": PRE2020,
    "2011": PRE2020,
    "2013": PRE2020,
    "2015": PRE2020,
    "2020": UE2020,
    # Belt and braces: ``parse`` already nulls the ``-1`` sentinel, so this
    # entry only fires if a loader ever stops doing that.
    "-1": UNKNOWN,
}

#: Jair Messias Bolsonaro's ballot number in 2022 -- the subject whose share
#: the controversy is about.  A NUMBER, not a name: names are free text in the
#: warehouse (and accented), numbers are the ballot's own identifier.  When the
#: warehouse holds no round-2 votes for him at all (a fixture, a partial load),
#: the subject falls back to the round's national leader so the detector still
#: describes the data in front of it rather than silently reporting nothing.
SUBJECT_NUMBER = 22

#: Normal-approximation two-sided 95 % multiplier.
Z95 = 1.96


# --------------------------------------------------------------------------
# Reading the warehouse
# --------------------------------------------------------------------------

#: Nominal presidential vote per candidate number in the runoff, NATIONALLY.
#: Used only to resolve the subject (see :data:`SUBJECT_NUMBER`).
_NATIONAL = f"""
    SELECT c.number::INT AS cand,
           MIN(c.name) AS name,
           SUM(f.votes)::BIGINT AS votes
    FROM fact_votes f
    JOIN dim_candidate c ON c.id = f.candidate_id
    WHERE f.race = '{PRES}' AND f.round = {ROUND}
      AND f.votavel_kind = 'candidate'
    GROUP BY 1
"""

#: One row per section that reported the runoff: where it is, which fleet it
#: ran, how many nominal presidential votes it cast and how many of them were
#: the subject's.
#:
#: ``valid`` is a LEFT JOIN coalesced to 0 as well as ``subject``: a section
#: with a stats row but no vote rows exists (an annulled or uninstalled
#: section), and it must be COUNTED in ``model_sections`` even though it
#: contributes no votes to a share.
_OBSERVATIONS = f"""
    SELECT s.uf,
           s.mun_code,
           st.urna_model,
           COALESCE(v.valid, 0)::BIGINT   AS valid,
           COALESCE(w.subject, 0)::BIGINT AS subject
    FROM fact_section_stats st
    JOIN dim_section s ON s.id = st.section_id
    LEFT JOIN (
        SELECT f.section_id, SUM(f.votes)::BIGINT AS valid
        FROM fact_votes f
        WHERE f.race = '{PRES}' AND f.round = {ROUND}
          AND f.votavel_kind = 'candidate'
        GROUP BY 1
    ) v ON v.section_id = st.section_id
    LEFT JOIN (
        SELECT f.section_id, SUM(f.votes)::BIGINT AS subject
        FROM fact_votes f
        JOIN dim_candidate c ON c.id = f.candidate_id
        WHERE f.race = '{PRES}' AND f.round = {ROUND}
          AND f.votavel_kind = 'candidate' AND c.number = %s
        GROUP BY 1
    ) w ON w.section_id = st.section_id
    WHERE st.race = '{PRES}' AND st.round = {ROUND}
"""


# --------------------------------------------------------------------------
# The maths -- pure, no database
# --------------------------------------------------------------------------


def classify(code: str | None) -> str:
    """One ``CD_MODELO_URNA`` -> ``pre2020`` / ``ue2020`` / ``unknown``.

    Anything unmapped is ``unknown`` rather than an error: a code TSE has not
    published before must not stop a national run, and calling it neither
    fleet is the only honest thing to do with it.  :func:`_class_expr` is the
    columnar twin of this function and reads the same table.
    """
    if code is None:
        return UNKNOWN
    return MODEL_CLASS.get(str(code).strip(), UNKNOWN)


def _class_expr() -> pl.Expr:
    """:func:`classify`, vectorised -- 472 k sections is a column, not a loop."""
    return (
        pl.col("urna_model")
        .cast(pl.String)
        .str.strip_chars()
        .fill_null("")
        .replace_strict(MODEL_CLASS, default=UNKNOWN, return_dtype=pl.String)
        .alias("model")
    )


@dataclass(frozen=True)
class Cluster:
    """One municipality's contribution: ``delta_m`` and its weight ``w_m``."""

    uf: str
    mun_code: str
    pre_valid: int
    pre_subject: int
    ue_valid: int
    ue_subject: int

    @property
    def mixed(self) -> bool:
        """Did it run BOTH fleets, with votes in each?

        Votes, not sections: a fleet that cast nothing has no share, and a
        municipality whose ue2020 sections were all annulled is not a
        comparison, it is a single-fleet municipality with a footnote.
        """
        return self.pre_valid > 0 and self.ue_valid > 0

    @property
    def delta(self) -> float:
        return self.pre_subject / self.pre_valid - self.ue_subject / self.ue_valid

    @property
    def weight(self) -> float:
        """Nominal votes the comparison is drawn over, both fleets together."""
        return float(self.pre_valid + self.ue_valid)


def weighted_gap(clusters: Sequence[Cluster]) -> dict[str, Any]:
    """Vote-weighted mean of ``delta_m`` plus its cluster-robust 95 % CI.

    Municipalities that ran one fleet only never reach here -- they have no
    ``delta_m`` -- so ``n_municipalities`` counts MIXED municipalities and
    nothing else.  With none of them the answer is 0 with a zero-width
    interval: no data is not the same as no effect, and the accompanying
    ``n_municipalities = 0`` is what says which of the two happened.
    """
    total = sum(c.weight for c in clusters)
    if not clusters or total <= 0:
        return {
            "within_mun_gap": 0.0,
            "ci95": [0.0, 0.0],
            "se": 0.0,
            "n_municipalities": len(clusters),
        }
    mean = sum(c.weight * c.delta for c in clusters) / total
    variance = sum((c.weight * (c.delta - mean)) ** 2 for c in clusters)
    se = math.sqrt(variance) / total
    return {
        "within_mun_gap": _num(mean),
        "ci95": [_num(mean - Z95 * se), _num(mean + Z95 * se)],
        "se": _num(se),
        "n_municipalities": len(clusters),
    }


def raw_gap(pooled: dict[str, tuple[int, int]]) -> float:
    """The NAIVE gap: pooled ``pre2020`` share minus pooled ``ue2020`` share.

    ``pooled`` maps class -> ``(subject votes, valid votes)`` over ALL sections
    of that class, ignoring municipality entirely.  This is the number the
    controversy quoted and the one the within-municipality figure is contrasted
    against.  0.0 when either fleet cast no votes: there is no gap between a
    thing and nothing.
    """
    pre_subject, pre_valid = pooled.get(PRE2020, (0, 0))
    ue_subject, ue_valid = pooled.get(UE2020, (0, 0))
    if pre_valid <= 0 or ue_valid <= 0:
        return 0.0
    return _num(pre_subject / pre_valid - ue_subject / ue_valid)


def _num(value: float) -> float:
    """A float safe to store: finite, and not carrying float noise into JSON."""
    value = float(value)
    return round(value, 9) if math.isfinite(value) else 0.0


def _share(subject: int, valid: int) -> float | None:
    return _num(subject / valid) if valid > 0 else None


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


class UrnaModel(BaseDetector):
    """Pre-2020 urnas against 2020 ones, within municipalities, PRES round 2.

    Writes NO section scores (see the module docstring): the output is the
    summary plus one payload per UF.
    """

    name = DETECTOR

    def params(self) -> dict[str, Any]:
        return {
            "statistic": (
                "delta_m = share(pre2020 sections) - share(ue2020 sections) of "
                f"the subject candidate, over {PRES} round {ROUND}, for every "
                "municipality running BOTH fleets; reported as the vote-weighted "
                "mean of delta_m with a cluster-robust 95 % CI"
            ),
            "share": (
                "VOTE-WEIGHTED: votes are summed within a class and divided "
                "once, never a mean of per-section shares"
            ),
            "denominator": (
                f"all nominal (candidate) {PRES} round-{ROUND} votes of the same "
                "sections; the subject's absent row counts as 0 in the numerator"
            ),
            "raw_gap": (
                "the naive NATIONAL gap -- pooled pre2020 share minus pooled "
                "ue2020 share over ALL sections of each class, mixed and "
                "single-fleet municipalities alike, ignoring geography entirely"
            ),
            "within_mun_gap": (
                "vote-weighted mean of delta_m over MIXED municipalities only, "
                "weight w_m = nominal votes of both fleets in that municipality"
            ),
            "ci95": (
                "cluster-robust, one cluster per municipality: "
                "SE = sqrt(sum(w_m^2 (delta_m - mean)^2)) / sum(w_m), "
                f"CI = mean +/- {Z95} SE"
            ),
            "model_source": (
                "fact_section_stats.urna_model (TSE CD_MODELO_URNA), read at "
                f"({PRES}, round {ROUND}) -- the model is NOT stable across "
                "rounds, so the runoff's own value is the one that classifies"
            ),
            "model_classes": dict(MODEL_CLASS),
            "unknown": (
                "-1 / NULL sections belong to neither fleet and enter no share; "
                "they are counted in model_sections so the coverage is visible"
            ),
            "subject": (
                f"candidate number {SUBJECT_NUMBER} (Bolsonaro, 2022) when he has "
                f"round-{ROUND} votes, else the round's national leader"
            ),
            "section_scores": (
                "none -- a section's urna model is a fact about logistics, not "
                "an anomaly; the composite ignores this detector"
            ),
            "caveat": (
                "a surviving within-municipality gap is not evidence of "
                "manipulation on its own: fleets are not randomised inside a "
                "municipality either (older machines sit in older, poorer "
                "neighbourhoods).  The prose belongs to the dashboard"
            ),
        }

    # -- compute -----------------------------------------------------------

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        subject = self._subject(db)
        if subject is None:
            return DetectorResult(_empty_summary())
        number, subject_name = subject

        frame = self._observations(db, number)
        clusters = _clusters(frame)
        by_uf: dict[str, list[Cluster]] = {}
        for cluster in clusters:
            by_uf.setdefault(cluster.uf, []).append(cluster)

        national = _report(clusters, _pooled(frame), _model_sections(frame))
        payloads = {
            uf: {
                "uf": uf,
                **_report(
                    group,
                    _pooled(frame, uf=uf),
                    _model_sections(frame, uf=uf),
                ),
            }
            for uf, group in sorted(_group_by_uf(frame, by_uf).items())
        }
        summary = {
            "score_key": SCORE_KEY,
            "basis": {
                "race": PRES,
                "round": ROUND,
                "subject_number": number,
                "subject_name": subject_name,
                "share": "vote-weighted",
                "model_round": ROUND,
            },
            **national,
            "states": {
                uf: {
                    "raw_gap": p["raw_gap"],
                    "within_mun_gap": p["within_mun_gap"],
                    "ci95": p["ci95"],
                    "n_municipalities": p["n_municipalities"],
                }
                for uf, p in payloads.items()
            },
            "widest_states": _widest_states(payloads),
        }
        return DetectorResult(summary, state_payloads=payloads)

    # -- inputs ------------------------------------------------------------

    @staticmethod
    def _subject(db: Database) -> tuple[int, str | None] | None:
        """``(candidate number, name)`` whose share the statistic is about."""
        rows = [
            (int(cand), name, int(votes or 0)) for cand, name, votes in db.fetch(_NATIONAL)
        ]
        if not rows:
            return None
        for cand, name, _votes in rows:
            if cand == SUBJECT_NUMBER:
                return cand, name
        # Ties break on the lower candidate number, so the pick is stable.
        cand, name, _votes = max(rows, key=lambda row: (row[2], -row[0]))
        return cand, name

    @staticmethod
    def _observations(db: Database, number: int) -> pl.DataFrame:
        frame = db.fetch_frame(_OBSERVATIONS, (number,))
        if frame.height == 0:
            return pl.DataFrame(
                schema={
                    "uf": pl.String, "mun_code": pl.String, "model": pl.String,
                    "valid": pl.Int64, "subject": pl.Int64,
                }
            )
        return frame.with_columns(
            _class_expr(),
            pl.col("valid").cast(pl.Int64),
            pl.col("subject").cast(pl.Int64),
        )


# --------------------------------------------------------------------------
# Assembling the answer
# --------------------------------------------------------------------------


def _clusters(frame: pl.DataFrame) -> list[Cluster]:
    """One :class:`Cluster` per MIXED municipality, in a stable order."""
    if frame.height == 0:
        return []
    grouped = (
        frame.filter(pl.col("model") != UNKNOWN)
        .group_by(["uf", "mun_code", "model"])
        .agg(pl.col("valid").sum(), pl.col("subject").sum())
    )
    totals: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for uf, mun, model, valid, subject in grouped.select(
        "uf", "mun_code", "model", "valid", "subject"
    ).iter_rows():
        totals.setdefault((str(uf), str(mun)), {})[str(model)] = (
            int(valid), int(subject)
        )
    clusters = []
    for (uf, mun), by_model in totals.items():
        pre_valid, pre_subject = by_model.get(PRE2020, (0, 0))
        ue_valid, ue_subject = by_model.get(UE2020, (0, 0))
        cluster = Cluster(uf, mun, pre_valid, pre_subject, ue_valid, ue_subject)
        if cluster.mixed:
            clusters.append(cluster)
    clusters.sort(key=lambda c: (c.uf, c.mun_code))
    return clusters


def _group_by_uf(
    frame: pl.DataFrame, by_uf: dict[str, list[Cluster]]
) -> dict[str, list[Cluster]]:
    """Clusters per UF, with an entry for every UF that reported the runoff.

    A state with no mixed municipality still gets a payload: "we looked and
    there was nothing to compare" is a finding, and a missing state would read
    as a state that was never examined.
    """
    ufs = (
        {str(uf) for (uf,) in frame.select("uf").unique().iter_rows()}
        if frame.height
        else set()
    )
    return {uf: by_uf.get(uf, []) for uf in ufs}


def _pooled(frame: pl.DataFrame, *, uf: str | None = None) -> dict[str, tuple[int, int]]:
    """class -> ``(subject votes, valid votes)`` over ALL of that class."""
    if frame.height == 0:
        return {}
    scoped = frame.filter(pl.col("uf") == uf) if uf is not None else frame
    grouped = scoped.group_by("model").agg(
        pl.col("subject").sum(), pl.col("valid").sum()
    )
    return {
        str(model): (int(subject), int(valid))
        for model, subject, valid in grouped.select(
            "model", "subject", "valid"
        ).iter_rows()
    }


def _model_sections(frame: pl.DataFrame, *, uf: str | None = None) -> dict[str, int]:
    """Sections per class, counted on ROUND 2 -- every class always present."""
    counts = {PRE2020: 0, UE2020: 0, UNKNOWN: 0}
    if frame.height == 0:
        return counts
    scoped = frame.filter(pl.col("uf") == uf) if uf is not None else frame
    for model, n in scoped.group_by("model").len().iter_rows():
        counts[str(model)] = counts.get(str(model), 0) + int(n)
    return counts


def _report(
    clusters: Sequence[Cluster],
    pooled: dict[str, tuple[int, int]],
    model_sections: dict[str, int],
) -> dict[str, Any]:
    """The block of numbers reported nationally AND per state, identically."""
    pre_subject, pre_valid = pooled.get(PRE2020, (0, 0))
    ue_subject, ue_valid = pooled.get(UE2020, (0, 0))
    gap = weighted_gap(clusters)
    return {
        "raw_gap": raw_gap(pooled),
        "within_mun_gap": gap["within_mun_gap"],
        "ci95": gap["ci95"],
        "se": gap["se"],
        "n_municipalities": gap["n_municipalities"],
        "model_sections": model_sections,
        "shares": {
            PRE2020: _share(pre_subject, pre_valid),
            UE2020: _share(ue_subject, ue_valid),
        },
        "votes": {
            PRE2020: {"subject": pre_subject, "valid": pre_valid},
            UE2020: {"subject": ue_subject, "valid": ue_valid},
        },
        "mixed_votes": int(sum(c.weight for c in clusters)),
    }


def _empty_summary() -> dict[str, Any]:
    """Every contracted key, with nothing behind them.

    A warehouse with no runoff at all (a round-1-only load) must still produce
    a readable summary: a consumer should never have to test for the ABSENCE
    of a key to find out that the answer is "no data".
    """
    return {
        "score_key": SCORE_KEY,
        "basis": {
            "race": PRES,
            "round": ROUND,
            "subject_number": None,
            "subject_name": None,
            "share": "vote-weighted",
            "model_round": ROUND,
        },
        "raw_gap": 0.0,
        "within_mun_gap": 0.0,
        "ci95": [0.0, 0.0],
        "se": 0.0,
        "n_municipalities": 0,
        "model_sections": {PRE2020: 0, UE2020: 0, UNKNOWN: 0},
        "shares": {PRE2020: None, UE2020: None},
        "votes": {
            PRE2020: {"subject": 0, "valid": 0},
            UE2020: {"subject": 0, "valid": 0},
        },
        "mixed_votes": 0,
        "states": {},
        "widest_states": [],
    }


def _widest_states(
    payloads: dict[str, dict[str, Any]], limit: int = 10
) -> list[dict[str, Any]]:
    """States whose WITHIN-municipality gap is largest, biggest first.

    Ranked on the honest number rather than the raw one: the raw gap is the
    artifact the detector exists to explain away, so ranking on it would
    promote exactly the states whose fleets are most geographically sorted.
    """
    rows = [
        {
            "uf": uf,
            "within_mun_gap": p["within_mun_gap"],
            "raw_gap": p["raw_gap"],
            "ci95": p["ci95"],
            "n_municipalities": p["n_municipalities"],
        }
        for uf, p in payloads.items()
        if p["n_municipalities"]
    ]
    rows.sort(key=lambda r: (-abs(float(r["within_mun_gap"])), r["uf"]))
    return rows[:limit]


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


def read_scores(db: Database, detector: str = DETECTOR) -> pl.DataFrame:
    """This detector's section scores -- always EMPTY, and deliberately so.

    Kept because every detector module exposes it and callers should not have
    to special-case a distribution-level one.  Accepts either the warehouse
    name (``t2_urna_model``) or the shard score key (``urna_model``).
    """
    return _read_scores(db, DETECTOR if detector in (DETECTOR, SCORE_KEY) else detector)
