"""Per-municipality columnar shards — the dashboard's unit of lazy loading.

    from observatory.export import shards

    for mun in shards.read_uf(db, "RR"):
        result = shards.write_shard(mun, out_dir)

THE SCHEMA IS FROZEN (``v = 1``)
    A TypeScript decoder (Task 11) and the export validation gate (Task 10)
    are written against these exact field names.  Renaming one here breaks
    two downstream slices silently, so the field names below are a contract,
    not an implementation detail::

        {
          "v": 1, "code": "03018", "name": "Boa Vista", "uf": "RR",
          "races": ["PRES_1", "PRES_2", "GOV_1", ...],
          "cand":  {"PRES_1": [[13, "LULA", 13], ...]},   # [number, name, party]
          "zone":    [8, 8, 9],       # parallel, one entry per section,
          "section": [1, 2, 1],       # sorted by (zone, section)
          "stats": {"PRES_1": {"aptos": [], "comp": [], "blank": [],
                               "null_votes": [], "legenda": []}},
          "votes": {"PRES_1": {"sec": [...], "cand": [...], "n": [...]}},
          "scores": {"PRES_1": {"extreme_dominance": [...]},
                     "ALL_1":  {"composite": [...], "t1_presence": [...]}},
          "score_detail": {"PRES_1": {"0": {"extreme_dominance": {...}}}}
        }

    ``score_detail`` is written for the sections in the composite high set
    (composite percentile >= 99, i.e. the top 1 % per round -- see
    :func:`read_uf`), which is what keeps it from being the biggest thing in
    the file.

    Split municipalities get an *index* at ``{code}.json.gz`` --
    ``{"v": 1, "split": true, "code", "name", "uf", "zones": [...]}`` -- and
    one regular-schema subshard per zone at ``{code}-z{zone}.json.gz``,
    carrying an extra ``"zone_only": <zone>``.

WHY COLUMNAR + SPARSE COO
    A municipality's votes are a (section x candidate) matrix that is mostly
    zero at DEPEST/DEPFED width, and TSE publishes it sparsely (no row means
    no votes).  Storing it as sorted ``(sec, cand, n)`` triplets keeps the
    shard proportional to what actually happened, and gzip eats the sorted
    ``sec`` column.  ``stats`` is the opposite shape -- every section reports
    aptos/comparecimento for every race -- so it is stored dense and aligned
    with ``zone``/``section``, indexable without a lookup.

RACE IDS ALWAYS CARRY THE ROUND
    ``PRES_1``, not ``PRES``.  The warehouse keys race and round separately;
    the shard joins them into one id (:func:`race_id`) because every consumer
    of a shard asks about a specific round.  ``ALL_1``/``ALL_2`` are reserved
    for cross-race detectors, which is how the warehouse's ``race = 'ALL'``
    convention (see ``detectors/base.py``) lands here.

BUDGET ENFORCEMENT LIVES HERE
    > 300 KiB gzipped -> re-encode as per-zone subshards behind an index.
    Any subshard still > 1 MiB -> :class:`ShardBudgetError`, a hard fail per
    spec.  Nothing is written until every piece is known to fit, so a failing
    municipality leaves no partial output behind.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from ..db import Database

__all__ = [
    "COMPOSITE_DETECTOR",
    "CROSS_RACE_IDS",
    "GZIP_LEVEL",
    "SCORE_DECIMALS",
    "SCORE_KEY_BY_DETECTOR",
    "SHARD_HARD_BYTES",
    "SHARD_TARGET_BYTES",
    "SHARD_VERSION",
    "STAT_FIELDS",
    "CandidateRow",
    "MunicipalityData",
    "ScoreRow",
    "ShardBudgetError",
    "StatsRow",
    "VoteRow",
    "WriteResult",
    "encode_bytes",
    "encode_shard",
    "encode_split_index",
    "race_id",
    "read_uf",
    "restrict_to_zone",
    "score_key",
    "shard_paths",
    "write_shard",
]

#: Schema version.  Bump only for a breaking change; the TS decoder checks it.
SHARD_VERSION = 1

#: Above this gzipped size a municipality is re-encoded as per-zone subshards.
SHARD_TARGET_BYTES = 300 * 1024

#: Above this gzipped size a *subshard* is a hard failure: splitting further
#: (by section? by race?) would fragment the drill-down page's single fetch,
#: so the spec says stop and shout instead.
SHARD_HARD_BYTES = 1024 * 1024

#: Static assets: compress hard once, serve forever.
GZIP_LEVEL = 9

#: Scores are display + ranking values, never re-derived from the shard.  Six
#: decimals is far below anything a chart or a sort can distinguish, and it
#: keeps a dense per-section float array from being the biggest thing in the
#: file (a raw double round-trips as ~18 characters).
SCORE_DECIMALS = 6

#: Race order inside ``races`` -- ballot order, so the dashboard's tab strip
#: is the order the voter met the races in.
RACE_ORDER: tuple[str, ...] = ("PRES", "GOV", "SEN", "DEPFED", "DEPEST", "DEPDIS")

#: Rounds a race id may carry.
ROUNDS: tuple[int, ...] = (1, 2)

#: The reserved keys cross-race detectors' scores live under.
CROSS_RACE_IDS: frozenset[str] = frozenset({"ALL_1", "ALL_2"})

#: ``stats`` sub-arrays, in emission order.  ``legenda`` is all-zero for the
#: majoritarian races (PRES/GOV/SEN) -- kept dense anyway so the decoder never
#: has to branch on race type.
STAT_FIELDS: tuple[str, ...] = ("aptos", "comp", "blank", "null_votes", "legenda")


# --------------------------------------------------------------------------
# Detector name -> shard score key
# --------------------------------------------------------------------------

#: THE single mapping from ``analysis.section_scores.detector`` to the key a
#: score appears under inside a shard's ``scores`` object.  Tasks 11, 12, 18
#: and 20 consume these keys, so this dict is the one place they are decided.
#:
#: The rule: **Tier 1 keeps its ``t1_`` prefix, every other tier drops its
#: own.**  Tier 1 checks are integrity assertions and their tier is part of
#: their public identity (the methodology page groups them as "Tier 1"); the
#: statistical detectors are known to a reader by their statistic, which is
#: also how the plan names their aggregate files (``anomalies/
#: extreme_dominance.json``, Task 9).  :func:`score_key` applies that rule to
#: detectors that land after this slice, so Tasks 13-18 need no edit here --
#: they only need an entry if they want a key their name does not imply.
SCORE_KEY_BY_DETECTOR: dict[str, str] = {
    "t1_arithmetic": "t1_arithmetic",
    "t1_presence": "t1_presence",
    "t2_extreme_dominance": "extreme_dominance",
}

#: The composite detector (Task 18), named HERE rather than imported from
#: ``detectors.composite`` because the dependency may only point one way:
#: every detector module imports this one, so an import back would be a cycle.
#: ``detectors.composite`` reads its own name from this constant, so the string
#: still exists in exactly one place.  It appears under the ``ALL_1``/``ALL_2``
#: score keys like every other cross-race detector, and its ``detail.high``
#: drives the ``score_detail`` rule in :func:`read_uf`.
COMPOSITE_DETECTOR = "composite"

#: The race a cross-race detector writes.  Spelled out for the same
#: no-cycle reason (``detectors.base.CROSS_RACE`` is the definition).
_CROSS_RACE = "ALL"


def score_key(detector: str) -> str:
    """Shard score key for a warehouse detector name.

    Explicit entries win; otherwise a ``t<n>_`` prefix is stripped for tiers
    2 and above and kept for tier 1.
    """
    explicit = SCORE_KEY_BY_DETECTOR.get(detector)
    if explicit is not None:
        return explicit
    if (
        len(detector) > 3
        and detector[0] == "t"
        and detector[1].isdigit()
        and detector[2] == "_"
        and detector[1] != "1"
    ):
        return detector[3:]
    return detector


class ShardBudgetError(RuntimeError):
    """A subshard exceeds the hard size budget.  Fatal, by design."""


# --------------------------------------------------------------------------
# Encoder input
# --------------------------------------------------------------------------
#
# Row-oriented and flat on purpose: it is what a SQL result set looks like,
# and it leaves every shaping decision (section ordering, densification,
# zero-dropping, COO sorting) inside `encode_shard`, where the acceptance
# tests can see it.


class CandidateRow(NamedTuple):
    """A candidate that received votes somewhere in this municipality."""

    race: str          # canonical race id, e.g. "PRES_1"
    number: int
    name: str
    party: int | None


class VoteRow(NamedTuple):
    """Nominal (candidate) votes in one section.  Zeroes are dropped."""

    race: str
    zone: int
    section: int
    number: int
    votes: int


class StatsRow(NamedTuple):
    """One section's turnout/blank/null/legenda totals for one race."""

    race: str
    zone: int
    section: int
    aptos: int
    comp: int
    blank: int
    null_votes: int
    legenda: int


class ScoreRow(NamedTuple):
    """One detector's score for one section.

    ``race`` is a race id or ``ALL_1``/``ALL_2``; ``key`` is already the shard
    score key (see :func:`score_key`).  ``detail`` is emitted into
    ``score_detail`` when it is not ``None`` -- the *policy* deciding which
    sections deserve detail lives in the reader, not the encoder.
    """

    race: str
    zone: int
    section: int
    key: str
    score: float | None
    detail: Any = None


@dataclass
class MunicipalityData:
    """Everything one municipality's shard is encoded from.

    Not frozen: tests attach expectations to an instance, and
    :func:`restrict_to_zone` builds derived copies.
    """

    code: str
    name: str
    uf: str
    sections: Sequence[tuple[int, int]] = ()          # (zone, section), any order
    candidates: Sequence[CandidateRow] = ()
    stats: Sequence[StatsRow] = ()
    votes: Sequence[VoteRow] = ()
    scores: Sequence[ScoreRow] = ()
    zone_only: int | None = None

    @property
    def zones(self) -> list[int]:
        return sorted({zone for zone, _ in self.sections})


def restrict_to_zone(mun: MunicipalityData, zone: int) -> MunicipalityData:
    """``mun`` with everything outside ``zone`` dropped, ready to subshard."""
    return MunicipalityData(
        code=mun.code,
        name=mun.name,
        uf=mun.uf,
        sections=[key for key in mun.sections if key[0] == zone],
        candidates=list(mun.candidates),
        stats=[row for row in mun.stats if row.zone == zone],
        votes=[row for row in mun.votes if row.zone == zone],
        scores=[row for row in mun.scores if row.zone == zone],
        zone_only=zone,
    )


# --------------------------------------------------------------------------
# Race ids
# --------------------------------------------------------------------------


def race_id(race: str, round: int) -> str:
    """``("PRES", 1) -> "PRES_1"``; ``("ALL", 2) -> "ALL_2"``."""
    return f"{race}_{int(round)}"


def _split_race_id(rid: str) -> tuple[str, int]:
    race, _, tail = rid.rpartition("_")
    if not race or not tail.isdigit() or int(tail) not in ROUNDS:
        raise ValueError(
            f"race id {rid!r} is not canonical: expected <RACE>_<round> with "
            f"round in {ROUNDS}"
        )
    return race, int(tail)


def _race_sort_key(rid: str) -> tuple[int, str, int]:
    race, round = _split_race_id(rid)
    rank = RACE_ORDER.index(race) if race in RACE_ORDER else len(RACE_ORDER)
    return (rank, race, round)


def _score_race_sort_key(rid: str, races: Sequence[str]) -> tuple[int, int, str]:
    """Real races first, in ``races`` order; then ``ALL_1``, ``ALL_2``."""
    if rid in races:
        return (0, races.index(rid), rid)
    return (1, 0, rid)


# --------------------------------------------------------------------------
# The encoder -- pure, no I/O
# --------------------------------------------------------------------------


def encode_shard(mun: MunicipalityData) -> dict[str, Any]:
    """The shard document for ``mun``.  Pure: same input, same dict."""
    sections = sorted(set(mun.sections))
    index = {key: i for i, key in enumerate(sections)}
    n = len(sections)

    def position(zone: int, section: int, what: str) -> int:
        try:
            return index[(zone, section)]
        except KeyError:
            raise ValueError(
                f"{mun.code}: {what} references section (zone={zone}, "
                f"section={section}), which is not in this municipality"
            ) from None

    races = sorted(
        {row.race for row in mun.stats}
        | {row.race for row in mun.votes}
        | {row.race for row in mun.candidates},
        key=_race_sort_key,
    )

    doc: dict[str, Any] = {
        "v": SHARD_VERSION,
        "code": mun.code,
        "name": mun.name,
        "uf": mun.uf,
    }
    if mun.zone_only is not None:
        doc["zone_only"] = int(mun.zone_only)

    doc["races"] = races
    doc["cand"] = _encode_candidates(mun.candidates, races)
    doc["zone"] = [zone for zone, _ in sections]
    doc["section"] = [section for _, section in sections]
    doc["stats"] = _encode_stats(mun.stats, races, n, position)
    doc["votes"] = _encode_votes(mun.votes, races, position)
    scores, score_detail = _encode_scores(mun.scores, races, n, position)
    doc["scores"] = scores
    doc["score_detail"] = score_detail
    return doc


def _encode_candidates(
    rows: Iterable[CandidateRow], races: Sequence[str]
) -> dict[str, list[list[Any]]]:
    by_race: dict[str, dict[int, tuple[str, int | None]]] = defaultdict(dict)
    for row in rows:
        by_race[row.race][int(row.number)] = (row.name, row.party)
    return {
        rid: [
            [number, name, party]
            for number, (name, party) in sorted(by_race[rid].items())
        ]
        for rid in races
        if by_race.get(rid)
    }


def _encode_stats(
    rows: Iterable[StatsRow],
    races: Sequence[str],
    n: int,
    position,
) -> dict[str, dict[str, list[int]]]:
    stats = {rid: {name: [0] * n for name in STAT_FIELDS} for rid in races}
    for row in rows:
        i = position(row.zone, row.section, "stats")
        target = stats[row.race]
        target["aptos"][i] = int(row.aptos or 0)
        target["comp"][i] = int(row.comp or 0)
        target["blank"][i] = int(row.blank or 0)
        target["null_votes"][i] = int(row.null_votes or 0)
        target["legenda"][i] = int(row.legenda or 0)
    return stats


def _encode_votes(
    rows: Iterable[VoteRow], races: Sequence[str], position
) -> dict[str, dict[str, list[int]]]:
    triplets: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for row in rows:
        votes = int(row.votes)
        if votes <= 0:            # sparse by contract: no row means no votes
            continue
        triplets[row.race].append(
            (position(row.zone, row.section, "votes"), int(row.number), votes)
        )
    out: dict[str, dict[str, list[int]]] = {}
    for rid in races:
        ordered = sorted(triplets.get(rid, ()))
        out[rid] = {
            "sec": [sec for sec, _, _ in ordered],
            "cand": [cand for _, cand, _ in ordered],
            "n": [count for _, _, count in ordered],
        }
    return out


def _encode_scores(
    rows: Iterable[ScoreRow],
    races: Sequence[str],
    n: int,
    position,
) -> tuple[dict[str, dict[str, list[float | None]]], dict[str, dict[str, dict[str, Any]]]]:
    buckets: dict[str, dict[str, list[float | None]]] = defaultdict(dict)
    detail: dict[str, dict[str, dict[str, Any]]] = {}
    known = set(races)
    for row in rows:
        rid = row.race
        if rid not in known and rid not in CROSS_RACE_IDS:
            raise ValueError(
                f"score race id {rid!r} is neither one of this shard's races "
                f"{sorted(known)} nor a reserved cross-race id "
                f"{sorted(CROSS_RACE_IDS)}"
            )
        i = position(row.zone, row.section, "scores")
        column = buckets[rid].get(row.key)
        if column is None:
            column = buckets[rid][row.key] = [None] * n
        column[i] = None if row.score is None else round(float(row.score), SCORE_DECIMALS)
        if row.detail is not None:
            detail.setdefault(rid, {}).setdefault(str(i), {})[row.key] = row.detail

    scores = {
        rid: {key: buckets[rid][key] for key in sorted(buckets[rid])}
        for rid in sorted(buckets, key=lambda r: _score_race_sort_key(r, races))
    }
    detail = {
        rid: {sec: dict(sorted(keys.items())) for sec, keys in sorted(
            detail[rid].items(), key=lambda item: int(item[0])
        )}
        for rid in sorted(detail, key=lambda r: _score_race_sort_key(r, races))
    }
    return scores, detail


def encode_split_index(mun: MunicipalityData, zones: Sequence[int]) -> dict[str, Any]:
    """The index written at ``{code}.json.gz`` for a split municipality."""
    return {
        "v": SHARD_VERSION,
        "split": True,
        "code": mun.code,
        "name": mun.name,
        "uf": mun.uf,
        "zones": [int(zone) for zone in zones],
    }


def encode_bytes(doc: dict[str, Any]) -> bytes:
    """``doc`` as the exact bytes that land on disk.

    Deterministic: compact separators, UTF-8 (municipality names carry
    accents), and ``mtime=0`` so a re-run of the exporter produces
    byte-identical files -- which is what makes Task 10's content hashes
    stable and this repo's exports diffable.
    """
    raw = json.dumps(
        doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)


# --------------------------------------------------------------------------
# The writer -- budget enforcement + I/O
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    """What :func:`write_shard` wrote.

    ``index_bytes`` is set **only** for a split municipality, and is the
    gzipped index that was written at ``{code}.json.gz``.  ``gz_sizes`` is
    keyed by file name.
    """

    paths: list[Path]
    index_bytes: bytes | None
    gz_sizes: dict[str, int]

    @property
    def split(self) -> bool:
        return self.index_bytes is not None

    @property
    def total_bytes(self) -> int:
        return sum(self.gz_sizes.values())

    @property
    def max_bytes(self) -> int:
        return max(self.gz_sizes.values(), default=0)


def shard_paths(out_dir: Path, code: str, zones: Sequence[int] | None = None) -> list[Path]:
    """Where a municipality's files live.  ``zones=None`` -> the single file."""
    root = out_dir / f"{code}.json.gz"
    if zones is None:
        return [root]
    return [root, *(out_dir / f"{code}-z{zone}.json.gz" for zone in zones)]


def write_shard(mun: MunicipalityData, out_dir: Path) -> WriteResult:
    """Encode ``mun`` into ``out_dir``, splitting by zone if it is too big.

    Every byte is computed before anything is written, so a municipality that
    breaches the hard budget raises :class:`ShardBudgetError` without leaving
    a half-written shard (or a stale unsplit one) behind.
    """
    out_dir = Path(out_dir)
    blob = encode_bytes(encode_shard(mun))
    if len(blob) <= SHARD_TARGET_BYTES:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{mun.code}.json.gz"
        path.write_bytes(blob)
        return WriteResult([path], None, {path.name: len(blob)})

    zones = mun.zones
    if not zones:
        raise ShardBudgetError(
            f"{mun.code}: {len(blob):,} B gzipped exceeds the "
            f"{SHARD_TARGET_BYTES:,} B budget and the municipality has no "
            "sections to split by zone"
        )

    subshards: list[tuple[int, bytes]] = []
    for zone in zones:
        sub = encode_bytes(encode_shard(restrict_to_zone(mun, zone)))
        if len(sub) > SHARD_HARD_BYTES:
            raise ShardBudgetError(
                f"{mun.code}-z{zone}: {len(sub):,} B gzipped exceeds the hard "
                f"{SHARD_HARD_BYTES:,} B subshard budget "
                f"({len(mun.sections):,} sections in the municipality). "
                "A zone is the finest split the schema has; this needs a "
                "schema decision, not a retry."
            )
        subshards.append((zone, sub))

    index_bytes = encode_bytes(encode_split_index(mun, zones))
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / f"{mun.code}.json.gz"
    index_path.write_bytes(index_bytes)
    paths = [index_path]
    sizes = {index_path.name: len(index_bytes)}
    for zone, sub in subshards:
        path = out_dir / f"{mun.code}-z{zone}.json.gz"
        path.write_bytes(sub)
        paths.append(path)
        sizes[path.name] = len(sub)
    return WriteResult(paths, index_bytes, sizes)


# --------------------------------------------------------------------------
# Warehouse -> MunicipalityData
# --------------------------------------------------------------------------
#
# One pass per UF, five queries, grouped in Python.  A UF is the natural unit:
# it is what `04_export.py --uf` exports, it keeps every query partition- and
# index-friendly (`dim_section (mun_code, zone)`), and at national scale it is
# the chunk that bounds memory -- Brazil never has to be resident at once.

#: Fallback flag threshold when a detector has no stored summary.
DEFAULT_FLAG_THRESHOLD = 1.0

#: The score query, in its two ``score_detail`` variants (see :func:`read_uf`).
#: Detail is attached in SQL rather than filtered in Python because at national
#: scale it is ~99 % of the bytes this query would otherwise move.
_SCORES_COMPOSITE = """
    SELECT s.mun_code, sc.race, sc.round, s.zone, s.section, sc.detector, sc.score,
           CASE WHEN COALESCE((comp.detail ->> 'high')::boolean, false)
                THEN sc.detail END AS detail
    FROM analysis.section_scores sc
    JOIN dim_section s ON s.id = sc.section_id
    LEFT JOIN analysis.section_scores comp
           ON comp.detector = %s
          AND comp.section_id = sc.section_id
          AND comp.race = %s
          AND comp.round = sc.round
    WHERE s.uf = %s
"""

_SCORES_PER_DETECTOR = """
    SELECT s.mun_code, sc.race, sc.round, s.zone, s.section, sc.detector, sc.score,
           CASE WHEN sc.score >= COALESCE(
                    (d.params ->> 'flag_threshold')::double precision, %s)
                THEN sc.detail END AS detail
    FROM analysis.section_scores sc
    JOIN dim_section s ON s.id = sc.section_id
    LEFT JOIN analysis.detector_summary d ON d.detector = sc.detector
    WHERE s.uf = %s
"""


def municipality_codes(db: Database, uf: str) -> list[str]:
    return [row[0] for row in db.fetch(
        "SELECT code FROM dim_municipality WHERE uf = %s ORDER BY code", (uf,)
    )]


def read_uf(db: Database, uf: str) -> Iterator[MunicipalityData]:
    """Every municipality of ``uf``, in code order, ready to encode.

    ``score_detail`` policy (Task 18, the frozen contract): a score carries its
    detail when its section is in the **composite high set for that round** --
    ``detail.high`` on the section's ``composite`` row, which is the top
    ``ceil(0.01 N)`` by composite and is what "composite percentile >= 99"
    means once ties are handled honestly (see ``detectors/composite.py``).  The
    rule is per (section, round) and applies to EVERY detector's score on that
    section: detail exists so a reader can see why a flagged section is flagged,
    and half of that answer is what the other detectors said about it.

    **Fallback, when the composite has not been computed.**  A warehouse
    analysed with ``--only`` a couple of detectors, or one being exported
    mid-migration, has no ``composite`` rows at all.  Emitting no detail
    whatsoever there would be a silent, total loss; so in that case -- and only
    that case -- the Task 8 interim rule stands in: a score carries its detail
    when it is at or above **its own detector's** ``flag_threshold``.  Which of
    the two ran is not guesswork at read time: the composite's presence in
    ``analysis.section_scores`` decides it.
    """
    composite_ran = bool(db.scalar(
        "SELECT EXISTS (SELECT 1 FROM analysis.section_scores WHERE detector = %s)",
        (COMPOSITE_DETECTOR,),
    ))
    names = dict(db.fetch(
        "SELECT code, name FROM dim_municipality WHERE uf = %s", (uf,)
    ))
    sections = _group(db.fetch(
        "SELECT mun_code, zone, section FROM dim_section WHERE uf = %s "
        "ORDER BY mun_code, zone, section",
        (uf,),
    ))
    candidates = _group(db.fetch(
        """
        SELECT DISTINCT s.mun_code, v.race, v.round, c.number, c.name, c.party_number
        FROM fact_votes v
        JOIN dim_section s   ON s.id = v.section_id
        JOIN dim_candidate c ON c.id = v.candidate_id
        WHERE s.uf = %s AND v.votavel_kind = 'candidate' AND v.votes > 0
        """,
        (uf,),
    ))
    votes = _group(db.fetch(
        """
        SELECT s.mun_code, v.race, v.round, s.zone, s.section, c.number, v.votes
        FROM fact_votes v
        JOIN dim_section s   ON s.id = v.section_id
        JOIN dim_candidate c ON c.id = v.candidate_id
        WHERE s.uf = %s AND v.votavel_kind = 'candidate' AND v.votes > 0
        """,
        (uf,),
    ))
    # Legenda is a votável, not a candidate: it belongs in `stats`, summed per
    # (section, race, round) across parties.
    legenda = {
        (row[0], row[1], row[2], row[3], row[4]): int(row[5])
        for row in db.fetch(
            """
            SELECT s.mun_code, v.race, v.round, s.zone, s.section, sum(v.votes)
            FROM fact_votes v
            JOIN dim_section s ON s.id = v.section_id
            WHERE s.uf = %s AND v.votavel_kind = 'legenda'
            GROUP BY 1, 2, 3, 4, 5
            """,
            (uf,),
        )
    }
    stats = _group(db.fetch(
        """
        SELECT s.mun_code, f.race, f.round, s.zone, s.section,
               f.aptos, f.comparecimento, f.blank, f.null_votes
        FROM fact_section_stats f
        JOIN dim_section s ON s.id = f.section_id
        WHERE s.uf = %s
        """,
        (uf,),
    ))
    scores = _group(db.fetch(
        _SCORES_COMPOSITE if composite_ran else _SCORES_PER_DETECTOR,
        ((COMPOSITE_DETECTOR, _CROSS_RACE, uf) if composite_ran
         else (DEFAULT_FLAG_THRESHOLD, uf)),
    ))

    for code in sorted(names):
        yield MunicipalityData(
            code=code,
            name=names[code],
            uf=uf,
            sections=[(int(zone), int(section))
                      for zone, section in sections.get(code, ())],
            candidates=[
                CandidateRow(race_id(race, rnd), int(number), name,
                             None if party is None else int(party))
                for race, rnd, number, name, party in candidates.get(code, ())
            ],
            stats=[
                StatsRow(
                    race_id(race, rnd), int(zone), int(section),
                    int(aptos or 0), int(comp or 0), int(blank or 0),
                    int(null_votes or 0),
                    legenda.get((code, race, rnd, zone, section), 0),
                )
                for race, rnd, zone, section, aptos, comp, blank, null_votes
                in stats.get(code, ())
            ],
            votes=[
                VoteRow(race_id(race, rnd), int(zone), int(section),
                        int(number), int(count))
                for race, rnd, zone, section, number, count in votes.get(code, ())
            ],
            scores=[
                ScoreRow(race_id(race, rnd), int(zone), int(section),
                         score_key(detector), float(score), detail)
                for race, rnd, zone, section, detector, score, detail
                in scores.get(code, ())
            ],
        )


def _group(rows: Iterable[Sequence[Any]]) -> dict[str, list[tuple]]:
    """Bucket rows whose first column is ``mun_code``, dropping that column."""
    out: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        out[row[0]].append(tuple(row[1:]))
    return out
