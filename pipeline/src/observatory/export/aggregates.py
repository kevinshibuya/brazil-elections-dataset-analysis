"""Eager-loaded aggregate files — everything the dashboard needs *before* it
knows which municipality the visitor cares about.

    from observatory.export import aggregates

    report = aggregates.write_all(db, out_dir)

WHAT LANDS WHERE
    ``summary.json``              national totals per race id, turnout, and one
                                  headline entry per detector
    ``states/{uf}.json``          the same shape restricted to one UF, plus the
                                  detectors' own per-state payloads
    ``anomalies/{key}.json``      the national top-500 sections by score for one
                                  detector, each row carrying enough context to
                                  render a ranking table without a shard fetch
    ``anomalies/clustering.json`` the zones and municipalities where the
                                  composite's high sections concentrate beyond
                                  chance, with the null model that says so
    ``municipalities-index.json`` columnar arrays for the map + search box
    ``annulled-candidacies.json`` amendment A4: votes cast at section level that
                                  TSE excluded from its official totals

    Every file carries ``"v": 1`` — the SAME version stamp as the shard schema
    (Task 8) and the manifest (Task 10).  They move together.

TWO RULES THAT ARE NOT NEGOTIABLE HERE

    1.  **Detector-agnostic, with exactly one named exception.**  Score files,
        per-detector headline counts and per-state payloads are all driven by
        what is *in* ``analysis.*``, keyed by
        :func:`observatory.export.shards.score_key`.  Tasks 15/16/17 add
        detectors and state payloads; none of them needed an edit here.  The
        exception is the **composite** (Task 18): two of this module's products
        — ``anomalies/clustering.json`` and the index's ``flag_density`` — are
        statements about the composite's high set specifically, so they name it
        (once, through :data:`shards.COMPOSITE_DETECTOR`).  A warehouse where
        the composite has not run produces an empty clustering document and a
        null ``flag_density`` column, never a zero: "not computed" and "checked,
        nothing found" must not render the same.

    2.  **Annulled candidacies are never dropped.**  They are the one category
        the reconciliation gate deliberately does not fail on (A4), which makes
        them the one category that could silently disappear between Postgres
        and the site.  They get their own file, and the legenda mirror image is
        carried alongside so the totals in the file add up to the category's
        total in ``reconciliation_report``.

CENTROIDS AND THE CODE-SPACE TRAP
    TSE and IBGE number municipalities in DIFFERENT code spaces — Boa Vista is
    TSE ``03018`` and IBGE ``1400100``.  Coordinates therefore arrive through
    two committed fixtures: ``mun_crosswalk_tse_ibge.csv`` (verified row by row
    against IBGE's own catalogue) and ``mun_centroids.csv`` (computed from the
    IBGE municipal mesh).  A municipality that does not resolve gets ``null``
    coordinates and is reported by :class:`ExportReport` — never a guess.  ZZ
    (exterior) has no geometry at all and is null by design: searchable, never
    mapped.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import settings
from ..db import Database
from ..detectors import base as detector_base
from . import shards
from .shards import race_id, score_key

__all__ = [
    "AGGREGATE_VERSION",
    "ANOMALY_TOP_N",
    "CENTROIDS_FIXTURE",
    "CLUSTERING_FILE",
    "CROSSWALK_FIXTURE",
    "ExportReport",
    "HEADLINE_CANDIDATES",
    "centroid_lookup",
    "write_all",
]

#: Schema version, shared with the shard schema and the manifest.
AGGREGATE_VERSION = shards.SHARD_VERSION

#: How many sections one ``anomalies/{key}.json`` carries.  A ranking page
#: shows tens; 500 leaves room to filter client-side without ever making the
#: eager payload proportional to the country.
ANOMALY_TOP_N = 500

#: The clustering document, written alongside the per-detector anomaly files.
#: It is NOT ``{score_key}.json`` for any detector -- it answers a different
#: question (where the high sections are, not which they are), and no detector
#: may take the name.
CLUSTERING_FILE = "clustering.json"

#: How many candidates of the headline race the municipality index carries a
#: share for.  Four covers a first round's real contenders; the file stays
#: O(municipalities), not O(municipalities x ballot).
HEADLINE_CANDIDATES = 4

#: Shares and turnout are display values.  Six decimals is far finer than any
#: chart or sort can distinguish, and it keeps 5 570 rows from being mostly
#: float noise.
SHARE_DECIMALS = 6

CROSSWALK_FIXTURE = "mun_crosswalk_tse_ibge.csv"
CENTROIDS_FIXTURE = "mun_centroids.csv"

#: UFs with no geometry: the exterior "state" is a bucket of embassies and
#: consulates, not a place on the map.
UNMAPPED_UFS: frozenset[str] = frozenset({"ZZ"})


@dataclass
class ExportReport:
    """What :func:`write_all` wrote, for the caller to print or assert on."""

    paths: list[Path] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    races: dict[str, int] = field(default_factory=dict)
    detectors: dict[str, int] = field(default_factory=dict)
    ufs: list[str] = field(default_factory=list)
    municipalities: int = 0
    #: Municipalities that should have coordinates and did not resolve.  A
    #: non-empty list is a fixture defect, not a rendering detail.
    unmapped: list[str] = field(default_factory=list)
    annulled_votes: int = 0
    annulled_candidates: int = 0
    #: Surviving clusters per level, or ``{}`` when the composite has not run.
    clusters: dict[str, int] = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(self.sizes.values())


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def encode_bytes(doc: dict[str, Any]) -> bytes:
    """``doc`` as the exact bytes that land on disk.

    Compact and deterministic, for the same reason shards are: Task 10 hashes
    these files, so an unchanged warehouse must produce unchanged bytes.  That
    is also why nothing here stamps a wall clock — the only timestamps in the
    output come from the warehouse itself.
    """
    return json.dumps(
        doc,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON-serialisable: {value!r}")


def write_all(db: Database, out_dir: Path | str) -> ExportReport:
    """Write every aggregate file under ``out_dir``.

    National by construction: ``summary.json`` and ``municipalities-index.json``
    cover whatever the warehouse holds, and one ``states/{uf}.json`` is written
    per UF present.  Exporting a single state is therefore not a mode — it is
    just what a single-state warehouse produces.
    """
    detector_base.ensure_analysis_schema(db)
    out = Path(out_dir)
    report = ExportReport()

    totals = _race_totals(db)
    summaries = _detector_summaries(db)
    ufs = sorted({uf for uf, _ in totals})
    report.ufs = ufs

    annulled = _annulled_doc(db)
    _write(out, "summary.json", _summary_doc(db, totals, summaries, ufs, annulled),
           report)

    payloads = _state_payloads(db)
    per_uf_detectors = _detector_counts_by_uf(db, summaries)
    _write_dir(
        out, "states",
        {
            f"{uf}.json": _state_doc(uf, totals, per_uf_detectors, payloads)
            for uf in ufs
        },
        report,
    )

    clustering = _clustering_doc(db)
    _write_dir(
        out, "anomalies",
        {**_anomaly_docs(db, summaries), CLUSTERING_FILE: clustering},
        report,
    )
    report.clusters = {
        level: len(rows) for level, rows in clustering["levels"].items()
    }

    index = _municipality_index(db, totals)
    _write(out, "municipalities-index.json", index.doc, report)
    report.municipalities = len(index.doc["code"])
    report.unmapped = index.unmapped

    _write(out, "annulled-candidacies.json", annulled, report)
    report.annulled_votes = annulled["total_votes"]
    report.annulled_candidates = annulled["total_candidates"]

    report.races = {rid: entry["total_votes"] for rid, entry in _national(totals).items()}
    report.detectors = {
        key: entry["sections_scored"] or 0
        for key, entry in _detector_headlines(summaries).items()
    }
    return report


def _write(root: Path, relpath: str, doc: dict[str, Any], report: ExportReport) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = encode_bytes(doc)
    path.write_bytes(blob)
    report.paths.append(path)
    report.sizes[relpath] = len(blob)


def _write_dir(
    root: Path, subdir: str, docs: dict[str, dict[str, Any]], report: ExportReport
) -> None:
    """Write ``docs`` into ``root/subdir`` and remove files it no longer covers.

    A detector that stops producing scores (or a UF that leaves the warehouse)
    must not leave last run's file behind: Task 10 hashes what is on disk, and
    a stale file is worse than a missing one because it still renders.
    """
    directory = root / subdir
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in sorted(docs.items()):
        _write(root, f"{subdir}/{name}", doc, report)
    for stale in sorted(directory.glob("*.json")):
        if stale.name not in docs:
            stale.unlink()


# --------------------------------------------------------------------------
# Race totals -- the spine of summary.json and states/{uf}.json
# --------------------------------------------------------------------------
#
# Keyed by (uf, race id) throughout; the national view is the per-UF view
# summed, so the two can never disagree about what a race total is.

_SUM_FIELDS = (
    "total_votes", "legenda", "sections",
    "aptos", "comparecimento", "abstentions", "blank", "null_votes",
)


def _race_totals(db: Database) -> dict[tuple[str, str], dict[str, Any]]:
    totals: dict[tuple[str, str], dict[str, Any]] = {}

    def entry(uf: str, race: str, rnd: int) -> dict[str, Any]:
        key = (uf, race_id(race, rnd))
        row = totals.get(key)
        if row is None:
            row = totals[key] = {"race": race, "round": int(rnd), "candidates": 0}
            row.update({name: 0 for name in _SUM_FIELDS})
        return row

    for uf, race, rnd, kind, votes, candidates in db.fetch(
        """
        SELECT s.uf, v.race, v.round, v.votavel_kind,
               SUM(v.votes)::BIGINT,
               COUNT(DISTINCT v.candidate_id)::BIGINT
        FROM fact_votes v
        JOIN dim_section s ON s.id = v.section_id
        WHERE v.votavel_kind IN ('candidate', 'legenda')
        GROUP BY 1, 2, 3, 4
        """
    ):
        row = entry(uf, race, rnd)
        if kind == "candidate":
            row["total_votes"] = int(votes or 0)
            row["candidates"] = int(candidates or 0)
        else:
            row["legenda"] = int(votes or 0)

    for uf, race, rnd, sections, aptos, comp, abst, blank, nulls in db.fetch(
        """
        SELECT s.uf, f.race, f.round, COUNT(*)::BIGINT,
               SUM(f.aptos)::BIGINT, SUM(f.comparecimento)::BIGINT,
               SUM(f.abstentions)::BIGINT, SUM(f.blank)::BIGINT,
               SUM(f.null_votes)::BIGINT
        FROM fact_section_stats f
        JOIN dim_section s ON s.id = f.section_id
        GROUP BY 1, 2, 3
        """
    ):
        row = entry(uf, race, rnd)
        row["sections"] = int(sections or 0)
        row["aptos"] = int(aptos or 0)
        row["comparecimento"] = int(comp or 0)
        row["abstentions"] = int(abst or 0)
        row["blank"] = int(blank or 0)
        row["null_votes"] = int(nulls or 0)

    for row in totals.values():
        row["turnout"] = _ratio(row["comparecimento"], row["aptos"])
    return totals


def _ratio(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, SHARE_DECIMALS)


def _race_sort(item: tuple[str, Any]) -> tuple[int, str, int]:
    rid = item[0]
    race, _, rnd = rid.rpartition("_")
    rank = (shards.RACE_ORDER.index(race)
            if race in shards.RACE_ORDER else len(shards.RACE_ORDER))
    return (rank, race, int(rnd))


def _for_uf(
    totals: dict[tuple[str, str], dict[str, Any]], uf: str
) -> dict[str, dict[str, Any]]:
    return dict(sorted(
        ((rid, dict(row)) for (row_uf, rid), row in totals.items() if row_uf == uf),
        key=_race_sort,
    ))


def _national(
    totals: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for (_uf, rid), row in totals.items():
        target = merged.get(rid)
        if target is None:
            target = merged[rid] = {
                "race": row["race"], "round": row["round"], "candidates": 0
            }
            target.update({name: 0 for name in _SUM_FIELDS})
        for name in _SUM_FIELDS:
            target[name] += row[name]
        # Candidacies are per-UF for every race but the presidency; the honest
        # national number is the max, not the sum of state rosters.
        target["candidates"] = max(target["candidates"], row["candidates"])
    for row in merged.values():
        row["turnout"] = _ratio(row["comparecimento"], row["aptos"])
    return dict(sorted(merged.items(), key=_race_sort))


# --------------------------------------------------------------------------
# Detectors -- headline counts, per-UF counts, state payloads
# --------------------------------------------------------------------------


def _detector_summaries(db: Database) -> dict[str, dict[str, Any]]:
    """``analysis.detector_summary`` as ``{detector: row}``.  No decoding of
    ``summary`` — the headline numbers are columns, and the per-detector blob
    is a detector's private shape that belongs on its own page, not here."""
    return {
        detector: {
            "sections_scored": None if scored is None else int(scored),
            "sections_flagged": None if flagged is None else int(flagged),
            "params": params or {},
            "at": at,
        }
        for detector, scored, flagged, params, at in db.fetch(
            "SELECT detector, sections_scored, sections_flagged, params, at "
            "FROM analysis.detector_summary ORDER BY detector"
        )
    }


def _detector_headlines(
    summaries: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    return {
        score_key(detector): {
            "detector": detector,
            "sections_scored": row["sections_scored"],
            "sections_flagged": row["sections_flagged"],
            "at": row["at"],
        }
        for detector, row in sorted(summaries.items(), key=lambda kv: score_key(kv[0]))
    }


def _threshold(summaries: dict[str, dict[str, Any]], detector: str) -> float:
    params = summaries.get(detector, {}).get("params") or {}
    value = params.get("flag_threshold")
    return shards.DEFAULT_FLAG_THRESHOLD if value is None else float(value)


def _detector_counts_by_uf(
    db: Database, summaries: dict[str, dict[str, Any]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """Per-UF scored/flagged counts, derived from the scores themselves.

    ``detector_summary`` is national and has no UF breakdown, so the counts a
    state page shows are recomputed here against **the detector's own**
    ``flag_threshold`` — the number that detector declared its own alarm level
    to be.  Note this is NOT the shard writer's ``score_detail`` rule any more:
    since Task 18 that one is composite-high (top 1 % overall), which is a
    deliberately narrower set.  "Flagged by turnout" and "carries detail in the
    shard" are different questions and the state page asks the first.
    """
    rows = db.fetch(
        """
        SELECT sc.detector, s.uf,
               COUNT(*)::BIGINT                       AS rows_scored,
               COUNT(DISTINCT sc.section_id)::BIGINT  AS sections_scored,
               MAX(sc.score)                          AS max_score
        FROM analysis.section_scores sc
        JOIN dim_section s ON s.id = sc.section_id
        GROUP BY 1, 2
        """
    )
    flagged: dict[tuple[str, str], int] = {}
    for detector in {row[0] for row in rows}:
        for uf, sections in db.fetch(
            """
            SELECT s.uf, COUNT(DISTINCT sc.section_id)::BIGINT
            FROM analysis.section_scores sc
            JOIN dim_section s ON s.id = sc.section_id
            WHERE sc.detector = %s AND sc.score >= %s
            GROUP BY 1
            """,
            (detector, _threshold(summaries, detector)),
        ):
            flagged[(detector, uf)] = int(sections)

    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for detector, uf, rows_scored, sections_scored, max_score in sorted(rows):
        out[uf][score_key(detector)] = {
            "detector": detector,
            "rows_scored": int(rows_scored),
            "sections_scored": int(sections_scored),
            "sections_flagged": flagged.get((detector, uf), 0),
            "max_score": None if max_score is None else round(float(max_score), 6),
            "flag_threshold": _threshold(summaries, detector),
        }
    return out


def _state_payloads(db: Database) -> dict[str, dict[str, Any]]:
    """``analysis.state_payloads`` as ``{uf: {score key: payload}}``.

    THE generic hook.  One query, no detector named anywhere: a detector slice
    that persists a ``DetectorResult.state_payloads`` entry lands on the state
    page with no change to this module.  That property is what Tasks 15, 16 and
    17 are relying on, so keep this query detector-blind.
    """
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for detector, uf, payload in db.fetch(
        "SELECT detector, uf, payload FROM analysis.state_payloads "
        "ORDER BY uf, detector"
    ):
        out[uf][score_key(detector)] = payload
    return out


# --------------------------------------------------------------------------
# The documents
# --------------------------------------------------------------------------


def _summary_doc(
    db: Database,
    totals: dict[tuple[str, str], dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    ufs: list[str],
    annulled: dict[str, Any],
) -> dict[str, Any]:
    return {
        "v": AGGREGATE_VERSION,
        "ufs": ufs,
        "municipalities": int(db.scalar("SELECT COUNT(*) FROM dim_municipality") or 0),
        "sections": int(db.scalar("SELECT COUNT(*) FROM dim_section") or 0),
        "races": _national(totals),
        "detectors": _detector_headlines(summaries),
        "annulled": {
            "candidates": annulled["total_candidates"],
            "votes": annulled["total_votes"],
            "legenda_votes": annulled["legenda"]["votes"],
            "municipalities": annulled["municipalities"],
        },
    }


def _state_doc(
    uf: str,
    totals: dict[tuple[str, str], dict[str, Any]],
    per_uf_detectors: dict[str, dict[str, dict[str, Any]]],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    races = _for_uf(totals, uf)
    return {
        "v": AGGREGATE_VERSION,
        "uf": uf,
        "sections": max((row["sections"] for row in races.values()), default=0),
        "races": races,
        "detectors": per_uf_detectors.get(uf, {}),
        # Whatever the detectors put there, under their own score keys.
        "payloads": payloads.get(uf, {}),
    }


def _anomaly_docs(
    db: Database, summaries: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """One document per detector that produced per-section scores.

    Ranked nationally and capped at :data:`ANOMALY_TOP_N`.  The ordering is
    fully specified down to the section so that ties — Tier 1's all-zero scores
    are nothing but ties — cannot make the file depend on Postgres' scan order.
    """
    scored = {
        detector: int(count)
        for detector, count in db.fetch(
            "SELECT detector, COUNT(*)::BIGINT FROM analysis.section_scores "
            "GROUP BY 1 ORDER BY 1"
        )
    }
    rows_by_detector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (detector, mun_code, mun_name, uf, zone, section, race, rnd, score,
         detail) in db.fetch(
        """
        SELECT detector, mun_code, mun_name, uf, zone, section, race, round,
               score, detail
        FROM (
            SELECT sc.detector, s.mun_code, m.name AS mun_name, s.uf,
                   s.zone, s.section, sc.race, sc.round, sc.score, sc.detail,
                   ROW_NUMBER() OVER (
                       PARTITION BY sc.detector
                       ORDER BY sc.score DESC, s.mun_code, s.zone, s.section,
                                sc.race, sc.round
                   ) AS rank
            FROM analysis.section_scores sc
            JOIN dim_section s      ON s.id = sc.section_id
            JOIN dim_municipality m ON m.code = s.mun_code
        ) ranked
        WHERE rank <= %s
        ORDER BY detector, rank
        """,
        (ANOMALY_TOP_N,),
    ):
        rows_by_detector[detector].append({
            "mun_code": mun_code,
            "mun_name": mun_name,
            "uf": uf,
            "zone": int(zone),
            "section": int(section),
            "race": race,
            "round": int(rnd),
            "race_id": race_id(race, rnd),
            "score": round(float(score), shards.SCORE_DECIMALS),
            "detail": detail,
        })

    return {
        f"{score_key(detector)}.json": {
            "v": AGGREGATE_VERSION,
            "detector": detector,
            "key": score_key(detector),
            "top_n": ANOMALY_TOP_N,
            "rows_scored": scored.get(detector, 0),
            "flag_threshold": _threshold(summaries, detector),
            "rows": rows,
        }
        for detector, rows in rows_by_detector.items()
    }


# --------------------------------------------------------------------------
# The composite's two products (Task 18) -- the one place a detector is named
# --------------------------------------------------------------------------


def _composite_summary(db: Database) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(params, summary)`` of the composite detector, or ``None`` if it never
    ran.  ``None`` is a distinct answer from "ran and found nothing"."""
    rows = db.fetch(
        "SELECT params, summary FROM analysis.detector_summary WHERE detector = %s",
        (shards.COMPOSITE_DETECTOR,),
    )
    if not rows:
        return None
    params, summary = rows[0]
    return dict(params or {}), dict(summary or {})


def _clustering_doc(db: Database) -> dict[str, Any]:
    """``anomalies/clustering.json`` — the clusters, and the null that judged them.

    The null model travels WITH the result, not only on the methodology page: a
    p-value whose null is not stated next to it is a number, not a finding.  The
    detector already assembled every cluster with its context, so this is a
    re-serialisation and not a second implementation of the statistic.
    """
    found = _composite_summary(db)
    params, summary = found or ({}, {})
    clustering = dict(summary.get("clustering") or {})
    clusters = dict(summary.get("clusters") or {})
    levels = list(clustering.get("levels") or ("zone", "municipality"))
    return {
        "v": AGGREGATE_VERSION,
        "computed": found is not None,
        "detector": shards.COMPOSITE_DETECTOR if found else None,
        "key": score_key(shards.COMPOSITE_DETECTOR),
        "q": clustering.get("q"),
        "null_model": clustering.get("null_model"),
        "null_model_detail": clustering.get("null_model_detail"),
        "family": clustering.get("family"),
        "high_set_rule": params.get("high_set"),
        "tests": clustering.get("tests") or {},
        # Per round: how many sections were scored and how many are "high".
        "rounds": summary.get("rounds") or {},
        "levels": {level: list(clusters.get(level) or []) for level in levels},
    }


def _flag_density(db: Database) -> dict[str, int] | None:
    """``{mun_code: composite-high sections}``, or ``None`` if not computed.

    Counted over DISTINCT sections, not rows: a section that is high in both
    rounds is one flagged section in that municipality, not two.
    """
    if _composite_summary(db) is None:
        return None
    return {
        code: int(count)
        for code, count in db.fetch(
            """
            SELECT s.mun_code, COUNT(DISTINCT sc.section_id)::BIGINT
            FROM analysis.section_scores sc
            JOIN dim_section s ON s.id = sc.section_id
            WHERE sc.detector = %s
              AND COALESCE((sc.detail ->> 'high')::boolean, false)
            GROUP BY 1
            """,
            (shards.COMPOSITE_DETECTOR,),
        )
    }


@dataclass
class _Index:
    doc: dict[str, Any]
    unmapped: list[str]


def _municipality_index(
    db: Database, totals: dict[tuple[str, str], dict[str, Any]]
) -> _Index:
    """The columnar municipality index: map markers, search, headline shares.

    Columnar because every consumer wants a *column* — the choropleth wants all
    the values, the search box wants all the names — and because 5 570 parallel
    scalars gzip to a fraction of 5 570 objects repeating their keys.
    """
    municipalities = db.fetch(
        "SELECT code, name, uf FROM dim_municipality ORDER BY code"
    )
    sections = {
        code: int(count)
        for code, count in db.fetch(
            "SELECT mun_code, COUNT(*)::BIGINT FROM dim_section GROUP BY 1"
        )
    }
    coords = centroid_lookup()

    headline_race, headline_candidates = _headline_race(db, totals)
    shares = _headline_shares(db, headline_race, [n for n, _ in headline_candidates])
    density = _flag_density(db)

    codes, names, ufs, lats, lons, section_counts, headline_shares = (
        [], [], [], [], [], [], []
    )
    flag_density: list[int | None] = []
    unmapped: list[str] = []
    for code, name, uf in municipalities:
        lat, lon = coords.get(code, (None, None))
        if lat is None and uf not in UNMAPPED_UFS:
            unmapped.append(code)
        codes.append(code)
        names.append(name)
        ufs.append(uf)
        lats.append(lat)
        lons.append(lon)
        section_counts.append(sections.get(code, 0))
        headline_shares.append(shares.get(code, [None] * len(headline_candidates)))
        flag_density.append(None if density is None else density.get(code, 0))

    doc = {
        "v": AGGREGATE_VERSION,
        "code": codes,
        "name": names,
        "uf": ufs,
        "lat": lats,
        "lon": lons,
        "sections": section_counts,
        "headline": {
            "race_id": headline_race,
            "candidates": [
                {"number": number, "name": cand_name}
                for number, cand_name in headline_candidates
            ],
        },
        "headline_shares": headline_shares,
        # Composite-high sections per municipality (Task 18).  ``null``
        # everywhere when the composite has not run: a zero would read as
        # "checked, nothing found" rather than "not computed".
        "flag_density": flag_density,
    }
    return _Index(doc, unmapped)


def _headline_race(
    db: Database, totals: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str | None, list[tuple[int, str]]]:
    """The race the index carries shares for: the last presidential round."""
    candidates_by_race = [rid for rid in _national(totals) if rid.startswith("PRES_")]
    rid = (max(candidates_by_race, key=lambda r: int(r.rpartition("_")[2]))
           if candidates_by_race
           else next(iter(_national(totals)), None))
    if rid is None:
        return None, []
    race, _, rnd = rid.rpartition("_")
    rows = db.fetch(
        """
        SELECT c.number, c.name, SUM(v.votes)::BIGINT AS votes
        FROM fact_votes v
        JOIN dim_candidate c ON c.id = v.candidate_id
        WHERE v.votavel_kind = 'candidate' AND v.race = %s AND v.round = %s
        GROUP BY 1, 2
        ORDER BY votes DESC, c.number
        LIMIT %s
        """,
        (race, int(rnd), HEADLINE_CANDIDATES),
    )
    return rid, [(int(number), name) for number, name, _votes in rows]


def _headline_shares(
    db: Database, headline_race: str | None, numbers: list[int]
) -> dict[str, list[float | None]]:
    if not headline_race or not numbers:
        return {}
    race, _, rnd = headline_race.rpartition("_")
    per_candidate: dict[str, dict[int, int]] = defaultdict(dict)
    municipal_total: dict[str, int] = defaultdict(int)
    for mun_code, number, votes in db.fetch(
        """
        SELECT s.mun_code, c.number, SUM(v.votes)::BIGINT
        FROM fact_votes v
        JOIN dim_section s   ON s.id = v.section_id
        JOIN dim_candidate c ON c.id = v.candidate_id
        WHERE v.votavel_kind = 'candidate' AND v.race = %s AND v.round = %s
        GROUP BY 1, 2
        """,
        (race, int(rnd)),
    ):
        per_candidate[mun_code][int(number)] = int(votes or 0)
        municipal_total[mun_code] += int(votes or 0)

    return {
        code: [_ratio(counts.get(number, 0), municipal_total[code])
               for number in numbers]
        for code, counts in per_candidate.items()
    }


# --------------------------------------------------------------------------
# Amendment A4 -- annulled candidacies
# --------------------------------------------------------------------------


def _annulled_rows(db: Database) -> list[dict[str, Any]]:
    from .. import reconcile          # local: reconcile imports nothing from us

    frame = reconcile.annulled_candidacies(db)
    return [
        {
            "race": row["race"],
            "round": int(row["round"]),
            "race_id": race_id(row["race"], row["round"]),
            "cand_number": int(row["number"]),
            "cand_name": row["name"],
            "party_number": None if row["party_number"] is None
                            else int(row["party_number"]),
            "coalition": row["coalition"],
            "municipalities": int(row["municipalities"]),
            "votes": int(row["votes"]),
        }
        for row in frame.iter_rows(named=True)
    ]


def _annulled_legenda(db: Database) -> dict[str, Any]:
    """The legenda mirror image of A4: party-list votes TSE parked in
    ``QT_VOTOS_LEGENDA_ANULADOS`` instead of ``..._VALIDOS``.  Reported
    separately because a party is not a candidate, and dropped from neither
    place because the two together are the category's total."""
    reconcile_rows = db.fetch(
        """
        SELECT race, round, SPLIT_PART(key, ':', 2)::INTEGER AS party_number,
               COUNT(DISTINCT mun_code)::BIGINT AS municipalities,
               SUM(ours)::BIGINT                AS votes
        FROM reconciliation_report
        WHERE category = 'annulled_candidacy' AND key LIKE 'legenda:%'
        GROUP BY 1, 2, 3
        ORDER BY votes DESC, 1, 2, 3
        """
    )
    rows = [
        {
            "race": race,
            "round": int(rnd),
            "race_id": race_id(race, rnd),
            "party_number": int(party),
            "municipalities": int(municipalities),
            "votes": int(votes),
        }
        for race, rnd, party, municipalities, votes in reconcile_rows
    ]
    return {"votes": sum(row["votes"] for row in rows), "rows": rows}


def _annulled_doc(db: Database) -> dict[str, Any]:
    from .. import reconcile

    reconcile.ensure_report_table(db)
    rows = _annulled_rows(db)
    legenda = _annulled_legenda(db)
    total = sum(row["votes"] for row in rows)
    return {
        "v": AGGREGATE_VERSION,
        # Candidate votes only -- the number the headline sentence quotes.
        "total_votes": total,
        "total_candidates": len(rows),
        "municipalities": int(db.scalar(
            "SELECT COUNT(DISTINCT mun_code) FROM reconciliation_report "
            "WHERE category = 'annulled_candidacy'"
        ) or 0),
        # ... and the legenda mirror, so candidate + legenda == the category
        # total in reconciliation_report and nothing has gone missing.
        "legenda": legenda,
        "category_total_votes": total + legenda["votes"],
        "rows": rows,
    }


# --------------------------------------------------------------------------
# Centroids, through the committed TSE <-> IBGE crosswalk
# --------------------------------------------------------------------------


def _read_fixture_csv(path: Path) -> list[dict[str, str]]:
    """A committed fixture, with its ``#`` provenance header skipped.

    The headers are not decoration: they are where each file's source URL and
    verification live, and they must survive in the committed artefact rather
    than in a build script nobody re-reads.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"missing fixture {path}. The municipality index needs the "
            "committed TSE<->IBGE crosswalk and IBGE centroids."
        )
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(line for line in fh if not line.startswith("#")))


@lru_cache(maxsize=1)
def centroid_lookup() -> dict[str, tuple[float, float]]:
    """``{tse_code: (lat, lon)}`` — the ONLY place the two code spaces meet.

    Cached: 5 570 rows parsed once per process, and the fixtures are committed
    files that cannot change under a running export.
    """
    fixtures = settings.fixtures_dir
    centroids = {
        int(row["ibge_code"]): (float(row["lat"]), float(row["lon"]))
        for row in _read_fixture_csv(fixtures / CENTROIDS_FIXTURE)
    }
    out: dict[str, tuple[float, float]] = {}
    for row in _read_fixture_csv(fixtures / CROSSWALK_FIXTURE):
        point = centroids.get(int(row["ibge_code"]))
        if point is not None:
            out[row["tse_code"]] = point
    return out
