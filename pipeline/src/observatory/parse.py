"""TSE CSV readers + quirk normalization.

This module is the SINGLE home for knowledge about how TSE's published CSVs
behave.  Everything downstream (ingest, reconciliation, detectors, export)
consumes the normalized frames produced here and must never re-parse raw TSE
files itself.

Every reader returns ``(good, bad)`` where ``bad`` has columns
``raw_line, reason``.  Rows are never silently dropped: a row is either
normalized into ``good`` or explained in ``bad``.

=============================================================================
OBSERVED QUIRKS  (verified against the 2022 archives in ``data/raw/``,
                  not copied from documentation)
=============================================================================

FILE FORMAT
  Q1  Encoding is latin-1 (ISO-8859-1).  ``CANTÁ`` is ``CANT\\xc1``,
      ``Eleição`` is ``Elei\\xe7\\xe3o``.  Decoding as UTF-8 raises.
  Q2  Separator ``;``, quote char ``"``, line terminator CRLF (``\\r\\n``).
  Q3  Quoting is INCONSISTENT WITHIN A ROW: text fields are quoted, numeric
      fields usually are not -- but not always (see Q7).  A quote-aware
      parser is mandatory; naive ``split(";")`` is not obviously wrong here
      (no embedded separators were found, see Q4) but is one publication
      away from being wrong.
  Q4  No embedded ``;`` or ``"`` inside any quoted field was found in any of
      the RR / BR members scanned (votação secção, detalhe secção, all three
      munzona rollups).  We still parse with the ``csv`` module.
  Q5  Header names are stable across every UF member of an archive: all 29
      CSV members of ``detalhe_votacao_secao_2022.zip`` share one 37-column
      header, byte for byte.

SENTINELS AND MAGIC STRINGS
  Q6  ``-1`` and ``-3`` are "not available" sentinels.  Observed in
      ``SQ_CANDIDATO`` (-1 on blank/null votável rows, -3 on legenda rows),
      ``NR_FEDERACAO`` (-1 = no federation), ``CD_MODELO_URNA`` (-1),
      ``NR_LOCAL_VOTACAO`` (-1), and every ``CD_SITUACAO_*`` column (-3).
      They were NOT observed in any ``QT_*`` count column in RR/BR, but the
      normalization maps them to NULL in count columns anyway -- never 0.
  Q7  ``#NULO#``, ``#NULO`` (no trailing ``#``) and ``#NE`` are textual
      "not applicable" markers.  All three appear in the munzona rollups
      (``NM_SOCIAL_CANDIDATO`` uses the *unterminated* ``#NULO`` form,
      ``NM_FEDERACAO``/``SG_FEDERACAO``/``DS_COMPOSICAO_FEDERACAO`` use
      ``#NULO#``, ``DS_DETALHE_SITUACAO_CAND`` uses ``#NE``).  All are
      normalized to NULL.
  Q8  ``NM_VOTAVEL`` is "VOTO BRANCO" / "VOTO NULO" on votável 95 / 96.
      Those are kept verbatim -- they are real labels, not sentinels.

IDENTIFIERS
  Q9  ``CD_MUNICIPIO`` is published INCONSISTENTLY: unquoted and unpadded in
      ``votacao_secao`` / ``votacao_candidato_munzona`` / ``votacao_partido_munzona``
      (``3069``), but quoted and zero-padded in ``detalhe_votacao_munzona``
      (``"03069"``).  Canonical form for this project is TEXT zero-padded to
      5 characters, applied here and never undone downstream.
  Q10 Exterior ("ZZ") sections carry ``SG_UF='ZZ'``, ``SG_UE='BR'`` and a
      5-digit municipality code whose ``NM_MUNICIPIO`` is a foreign city
      (``MADRI``, ``BOSTON``, ``MAPUTO``).  They exist only in the
      president-scoped members (``*_BR.csv``), never in a UF member.
  Q11 ``NR_VOTAVEL`` width encodes the office: 2 digits for
      president/governor, 3 for senator, 4 for federal deputy, 5 for state
      deputy -- plus 95/96 everywhere and 2-digit legenda numbers in the
      proportional races.  Never zero-padded.
  Q12 ``votavel_kind`` derivation (95 -> blank, 96 -> null, 2-digit in
      DEPFED/DEPEST/DEPDIS -> legenda, else candidate) is independently
      CONFIRMED by ``SQ_CANDIDATO``: in ``votacao_secao_2022_RR.csv`` there
      are exactly 11 941 rows with ``SQ_CANDIDATO=-1`` and exactly 11 941
      rows with votável in {95,96}; exactly 11 493 rows with
      ``SQ_CANDIDATO=-3`` and exactly 11 493 two-digit non-95/96 rows in
      cargos 6 and 7.  Governor (cargo 3) candidate numbers are also
      2-digit (10, 22, 13, ...) which is why the legenda rule is restricted
      to proportional races.
  Q13 The votação-secção file carries NO party column at all -- no
      ``NR_PARTIDO``, no ``SG_PARTIDO``.  ``party_number`` is therefore
      derived as the first two digits of the votável number, and party
      SIGLAS have to come from the munzona rollups.
  Q14 ``NM_VOTAVEL`` is the only name field in the votação-secção file
      (there is no ``NM_URNA_CANDIDATO`` at section level).  On LEGENDA rows
      it holds the PARTY name, and the casing is not stable -- short names
      arrive upper-case ("REPUBLICANOS"), long names title-case
      ("Partido Liberal", "Movimento Democrático Brasileiro").

ROWS THAT DO NOT EXIST
  Q14b The votação-secção file has NO zero-vote rows: 0 of 204 593 RR rows
      have ``QT_VOTOS='0'``.  A (section, votável) pair with no votes is
      simply absent, so "candidate X got 0 votes here" must be inferred by
      absence, never read.  Section x votável is therefore a SPARSE matrix.

ELECTION SCOPE  (this one is a trap)
  Q15 ONE ARCHIVE CONTAINS MORE THAN ONE ELECTION.  ``CD_ELEICAO`` observed:
        544 -> president, round 1        (DT_ELEICAO 02/10/2022)
        545 -> president, round 2        (DT_ELEICAO 30/10/2022)
        546 -> state offices, round 1    (DT_ELEICAO 02/10/2022)
        547 -> state offices, round 2    (DT_ELEICAO 30/10/2022)
       6278 -> "Eleição Suplementar Governador 2026", DT_ELEICAO 21/06/2026
      The 2026 supplementary governor election is mixed into the RR members
      of every 2022 archive (6 906 of 204 593 rows in
      ``votacao_secao_2022_RR.csv``; 1 483 of 6 555 in the RR section
      detalhe) and carries ``ANO_ELEICAO=2022`` and ``NR_TURNO=1``, so it
      collides on ``(uf, mun, zone, section, race, round)`` with the real
      2022 general election.  Readers therefore emit ``election_id`` and
      callers MUST filter (``ELECTIONS_2022_GENERAL``).  Ingesting without
      filtering produces duplicate keys and a guaranteed reconciliation
      failure.
  Q16 ``DS_ELEICAO`` casing is not stable across archives
      ("Eleições Gerais Estaduais 2022" in the secção files,
      "ELEIÇÕES GERAIS ESTADUAIS 2022" in ``votacao_candidato_munzona``).
      Never key on it; key on ``CD_ELEICAO``.

COVERAGE GAPS
  Q17 ``votacao_secao_2022_RR.zip`` contains NO president rows and NO round-2
      rows: cargos are exactly {3 GOV, 5 SEN, 6 DEPFED, 7 DEPEST}, all
      ``NR_TURNO=1``.  Per-section PRESIDENTIAL VOTES live only in
      ``votacao_secao_2022_BR.zip``, which is NOT downloaded.  Per-section
      president TURNOUT is available (``detalhe_votacao_secao_2022_BR.csv``,
      944 150 rows, both rounds, 28 UFs incl. ZZ) -- but the per-candidate
      breakdown is not.
  Q18 ``detalhe_votacao_secao_2022_<UF>.csv`` holds only that UF's state
      offices; the president rows for every UF (plus ZZ) are in the single
      ``detalhe_votacao_secao_2022_BR.csv`` member.

AGGREGATED SECTIONS  ("seções agregadas")
  Q19 THERE IS NO PER-SECTION AGGREGATION COLUMN.  The 37-column section
      detalhe header has no ``QT_SECOES_AGREGADAS`` (or any equivalent).
      Aggregation counts exist ONLY in ``detalhe_votacao_munzona`` as
      ``QT_SECOES_PRINCIPAIS / QT_SECOES_AGREGADAS /
      QT_SECOES_NAO_INSTALADAS / QT_TOTAL_SECOES``, at
      ``(municipality, zone, office)`` granularity.
  Q20 Aggregated sections are ABSORBED, not reported.  Verified for RR, AP
      and AC: for every ``(mun, zone, office, election, round)`` group, the
      number of rows in the section file equals
      ``QT_SECOES_PRINCIPAIS + QT_SECOES_NAO_INSTALADAS`` and the section
      sums of ``QT_APTOS`` and ``QT_COMPARECIMENTO`` equal the munzona
      totals EXACTLY (64/64, 68/68, 92/92 groups).  The absorbing section's
      ``QT_APTOS`` already includes the aggregated sections' electorate, so
      ``comparecimento <= aptos`` holds everywhere (0 violations in RR).
  Q21 The aggregation mapping is not recoverable from CESP either: the
      ``csec`` expected-correspondence file lists 1 268 RR sections, exactly
      the number of section rows in the results, one urna each -- the
      aggregated sections simply do not appear.
      => ``read_detail(...)["agg_secoes"]`` is ALWAYS NULL.  The real,
         non-NULL numbers are in ``read_rollup_detail(...)["agg_secoes"]``
         at zone granularity.

SECTION STATUS
  Q22 ``ST_SECAO_INSTALADA='Não'`` exists (94 rows in the president member)
      with ``QT_APTOS > 0`` and ``QT_COMPARECIMENTO = 0``.
  Q23 ``DS_ORIGEM_VOTO`` is not always "Urna Eletrônica": also
      "Sistema de apuração", "Totalização",
      "BU gerado por RED em urna de seção *** RED - Recuperador de dados",
      "BU gerado por RED em urna de contingência".  Task 17 (urna models)
      must treat these as non-urna origins.

TRANSIT VOTING
  Q24 The munzona rollups are split by ``ST_VOTO_EM_TRANSITO`` (S/N), so a
      ``(mun, zone, office, round)`` key can appear twice.  Only 'N' rows
      exist in RR, but reconciliation must aggregate over the flag.

RECONCILIATION TRAPS  (measured on RR; relevant to the Task 5 gate)
  Q25 Turnout reconciles EXACTLY.  Section sums of aptos / comparecimento /
      blancos / nulos per (mun, race, round) equal
      ``detalhe_votacao_munzona`` on all 60 RR keys, and the blank/null rows
      of the votação file equal the detalhe file's blank/null on all 60.
  Q26 NOMINAL TOTALS DO NOT.  22 candidates that received votes at section
      level have NO ROW AT ALL in ``votacao_candidato_munzona`` -- 137 of
      8 207 (mun, race, round, candidate) keys, 27 160 votes (2.4 % of RR
      nominal).  The dominant case is senate candidate 333 HELDER GIRÃO
      BARRETO with 25 554 votes (third place), present in every section but
      absent from the rollup; all eight remaining RR senate candidates match
      to the vote.  These are candidacies rejected after the election: TSE
      removes them from the rollup but their urna votes stay in the section
      file.  A tolerance-zero candidate-keyed comparison will therefore fail
      on real data unless "no rollup row" is treated as its own category.
  Q27 Likewise for legenda: 23 (mun, race, round, party) keys where the
      section file has 1-2 legenda votes and
      ``QT_VOTOS_LEGENDA_VALIDOS`` is 0 -- those sit in
      ``QT_VOTOS_LEGENDA_ANULADOS`` / ``..._ANUL_SUBJUD`` instead.  Valid
      totals alone are not comparable to raw section votes.
  Q28 The two rollups disagree with each other: for DEPEST the candidato
      file's nominal total is 286 631 while the partido file's
      valid+annulled nominal total is 282 892.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, TextIO

import polars as pl

__all__ = [
    "ZipMember",
    "Source",
    "CARGO_TO_RACE",
    "RACES",
    "PROPORTIONAL_RACES",
    "VOTAVEL_KINDS",
    "ELECTIONS_2022_GENERAL",
    "ELECTION_IDS",
    "NA_SENTINELS",
    "NA_STRINGS",
    "list_members",
    "member",
    "read_votes",
    "read_detail",
    "read_rollup_cand",
    "read_rollup_party",
    "read_rollup_detail",
    "iter_votes",
    "iter_detail",
    "iter_rollup_cand",
    "iter_rollup_party",
    "iter_rollup_detail",
]

ENCODING = "latin-1"
DELIMITER = ";"
QUOTECHAR = '"'
DEFAULT_BATCH_SIZE = 250_000

#: CD_CARGO -> canonical race code.  Only these six are in scope.
CARGO_TO_RACE: dict[str, str] = {
    "1": "PRES",
    "3": "GOV",
    "5": "SEN",
    "6": "DEPFED",
    "7": "DEPEST",
    "8": "DEPDIS",
}
RACES: tuple[str, ...] = ("PRES", "GOV", "SEN", "DEPFED", "DEPEST", "DEPDIS")
#: Races where a 2-digit votável number is a legenda (party) vote (Q12).
PROPORTIONAL_RACES: tuple[str, ...] = ("DEPFED", "DEPEST", "DEPDIS")
VOTAVEL_KINDS: tuple[str, ...] = ("candidate", "blank", "null", "legenda")

#: CD_ELEICAO of the 2022 general election (Q15).  Everything else in the
#: 2022 archives belongs to a different pleito and must be filtered out.
ELECTION_IDS: dict[int, str] = {
    544: "president round 1",
    545: "president round 2",
    546: "state offices round 1",
    547: "state offices round 2",
    6278: "RR supplementary governor 2026 (NOT part of the 2022 general)",
}
ELECTIONS_2022_GENERAL: tuple[int, ...] = (544, 545, 546, 547)

#: Numeric "not available" sentinels (Q6).
NA_SENTINELS: tuple[int, ...] = (-1, -3)
#: Textual "not available" markers (Q7).  ``-1``/``-3`` are included because
#: code-like text columns (``SQ_CANDIDATO``, ``CD_MODELO_URNA``) carry the
#: numeric sentinels as text; no TSE text column has a legitimate "-1" value.
NA_STRINGS: tuple[str, ...] = ("#NULO#", "#NULO", "#NE#", "#NE", "-1", "-3", "")


# --------------------------------------------------------------------------
# Sources: a plain CSV path, or a member inside a zip (archives are never
# extracted to disk -- see the plan's disk budget).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ZipMember:
    """A CSV member inside a zip archive, streamed without extraction."""

    archive: Path
    name: str

    def __str__(self) -> str:  # pragma: no cover - repr sugar
        return f"{self.archive}::{self.name}"


Source = "str | os.PathLike[str] | ZipMember"


def list_members(archive: str | os.PathLike[str], suffix: str = ".csv") -> list[str]:
    """Member names inside ``archive`` ending with ``suffix``."""
    with zipfile.ZipFile(os.fspath(archive)) as zf:
        return [n for n in zf.namelist() if n.lower().endswith(suffix)]


def member(archive: str | os.PathLike[str], name_contains: str) -> ZipMember:
    """The single CSV member of ``archive`` whose name contains ``name_contains``."""
    archive = Path(archive)
    hits = [n for n in list_members(archive) if name_contains in n]
    if len(hits) != 1:
        raise ValueError(
            f"{archive}: expected exactly one CSV member containing "
            f"{name_contains!r}, found {hits!r}"
        )
    return ZipMember(archive, hits[0])


def _resolve(src) -> ZipMember | Path:
    if isinstance(src, ZipMember):
        return src
    path = Path(os.fspath(src))
    if path.suffix.lower() == ".zip":
        members = list_members(path)
        if len(members) != 1:
            raise ValueError(
                f"{path}: archive has {len(members)} CSV members; pass a "
                f"parse.ZipMember to choose one: {members!r}"
            )
        return ZipMember(path, members[0])
    return path


class _Opened:
    """Context manager yielding a latin-1 text stream for a Source."""

    def __init__(self, src):
        self._src = _resolve(src)
        self._zf: zipfile.ZipFile | None = None
        self._raw: BinaryIO | None = None
        self._text: TextIO | None = None

    def __enter__(self) -> TextIO:
        if isinstance(self._src, ZipMember):
            self._zf = zipfile.ZipFile(os.fspath(self._src.archive))
            self._raw = self._zf.open(self._src.name)
        else:
            self._raw = open(self._src, "rb")
        self._text = io.TextIOWrapper(self._raw, encoding=ENCODING, newline="")
        return self._text

    def __exit__(self, *exc) -> None:
        for closeable in (self._text, self._raw, self._zf):
            if closeable is not None:
                closeable.close()


class _LineCapture:
    """Wraps a line iterator so the exact source line(s) of each CSV record
    can be recovered for the ``bad`` frame -- including multi-line quoted
    records, which ``csv.reader`` consumes transparently."""

    __slots__ = ("_it", "_buf")

    def __init__(self, it) -> None:
        self._it = iter(it)
        self._buf: list[str] = []

    def __iter__(self) -> _LineCapture:
        return self

    def __next__(self) -> str:
        line = next(self._it)
        self._buf.append(line)
        return line

    def take(self) -> str:
        raw = "".join(self._buf)
        self._buf.clear()
        return raw.rstrip("\r\n")


def _iter_raw_batches(src, batch_size: int | None) -> Iterator[tuple[list[str], list[list[str]], list[str]]]:
    """Yield ``(header, rows, raw_lines)`` batches from a TSE CSV."""
    with _Opened(src) as text:
        capture = _LineCapture(text)
        reader = csv.reader(capture, delimiter=DELIMITER, quotechar=QUOTECHAR)
        try:
            header = [h.strip().lstrip("﻿") for h in next(reader)]
        except StopIteration:
            return
        capture.take()
        rows: list[list[str]] = []
        raws: list[str] = []
        limit = batch_size or -1
        for fields in reader:
            raw = capture.take()
            if not fields and not raw.strip():
                continue  # trailing blank line
            rows.append(fields)
            raws.append(raw)
            if limit > 0 and len(rows) >= limit:
                yield header, rows, raws
                rows, raws = [], []
        yield header, rows, raws


# --------------------------------------------------------------------------
# Shared normalization expressions
# --------------------------------------------------------------------------

RAW = "__raw_line"


def _txt(col: str) -> pl.Expr:
    """Trimmed text with TSE's textual NA markers (Q7) mapped to NULL."""
    stripped = pl.col(col).str.strip_chars()
    return (
        pl.when(stripped.is_in(list(NA_STRINGS)))
        .then(None)
        .otherwise(stripped)
        .alias(col)
    )


def _int(col: str) -> pl.Expr:
    return pl.col(col).str.strip_chars().cast(pl.Int64, strict=False)


def _count(col: str) -> pl.Expr:
    """Count column: numeric sentinels (Q6) become NULL, never 0."""
    v = _int(col)
    return pl.when(v.is_in(list(NA_SENTINELS))).then(None).otherwise(v)


def _bool_sn(col: str) -> pl.Expr:
    """TSE 'S'/'N' and 'Sim'/'Não' flags."""
    v = pl.col(col).str.strip_chars().str.to_uppercase()
    return (
        pl.when(v.is_in(["S", "SIM"]))
        .then(pl.lit(True))
        .when(v.is_in(["N", "NAO", "NÃO"]))
        .then(pl.lit(False))
        .otherwise(None)
    )


def _mun_code() -> pl.Expr:
    """Canonical municipality code: TEXT, zero-padded to 5 (Q9)."""
    return pl.col("CD_MUNICIPIO").str.strip_chars().str.zfill(5)


def _race() -> pl.Expr:
    return (
        pl.col("CD_CARGO")
        .str.strip_chars()
        .replace_strict(CARGO_TO_RACE, default=None, return_dtype=pl.Utf8)
    )


def _round() -> pl.Expr:
    t = _int("NR_TURNO")
    return pl.when(t.is_in([1, 2])).then(t).otherwise(None).cast(pl.Int8)


def _bad_when(cond: pl.Expr, message: pl.Expr | str) -> pl.Expr:
    msg = pl.lit(message) if isinstance(message, str) else message
    return pl.when(cond).then(msg).otherwise(None)


def _with_value(prefix: str, col: str) -> pl.Expr:
    return pl.lit(prefix) + pl.col(col).str.strip_chars().fill_null("<null>")


def _common_checks() -> list[pl.Expr]:
    """Checks shared by every TSE file kind."""
    return [
        _bad_when(
            ~pl.col("CD_MUNICIPIO").str.strip_chars().str.contains(r"^\d{1,5}$"),
            _with_value("invalid CD_MUNICIPIO=", "CD_MUNICIPIO"),
        ),
        _bad_when(_race().is_null(), _with_value("unknown CD_CARGO=", "CD_CARGO")),
        _bad_when(_round().is_null(), _with_value("invalid NR_TURNO=", "NR_TURNO")),
        _bad_when(_int("NR_ZONA").is_null(), _with_value("invalid NR_ZONA=", "NR_ZONA")),
    ]


def _split(df: pl.DataFrame, checks: Sequence[pl.Expr], out: dict[str, pl.Expr]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Apply ``checks`` -> bad frame; project ``out`` for the surviving rows."""
    reason = pl.coalesce(list(checks)).alias("reason")
    tagged = df.with_columns(reason)
    bad = tagged.filter(pl.col("reason").is_not_null()).select(
        pl.col(RAW).alias("raw_line"), pl.col("reason")
    )
    good = tagged.filter(pl.col("reason").is_null()).select(
        [expr.alias(name) for name, expr in out.items()]
    )
    return good, bad


BAD_SCHEMA: dict[str, pl.DataType] = {"raw_line": pl.Utf8, "reason": pl.Utf8}


@dataclass(frozen=True)
class _Spec:
    kind: str
    required: tuple[str, ...]
    schema: dict[str, pl.DataType]
    normalize: Callable[[pl.DataFrame], tuple[pl.DataFrame, pl.DataFrame]]


def _frame(header: Sequence[str], rows: list[list[str]], raws: list[str]) -> tuple[pl.DataFrame, list[tuple[str, str]]]:
    """Build an all-Utf8 frame; rows with the wrong arity go straight to bad."""
    n = len(header)
    ok_rows: list[list[str]] = []
    ok_raws: list[str] = []
    arity_bad: list[tuple[str, str]] = []
    for fields, raw in zip(rows, raws):
        if len(fields) != n:
            arity_bad.append((raw, f"field count {len(fields)} != {n}"))
        else:
            ok_rows.append(fields)
            ok_raws.append(raw)
    schema = {name: pl.Utf8 for name in header}
    if ok_rows:
        df = pl.DataFrame(ok_rows, schema=schema, orient="row")
    else:
        df = pl.DataFrame(schema=schema)
    df = df.with_columns(pl.Series(RAW, ok_raws, dtype=pl.Utf8))
    return df, arity_bad


def _iter(src, spec: _Spec, batch_size: int | None) -> Iterator[tuple[pl.DataFrame, pl.DataFrame]]:
    for header, rows, raws in _iter_raw_batches(src, batch_size):
        missing = [c for c in spec.required if c not in header]
        if missing:
            raise ValueError(
                f"{src}: not a {spec.kind} file -- missing columns {missing}. "
                f"Header has {len(header)} columns: {header}"
            )
        df, arity_bad = _frame(header, rows, raws)
        good, bad = spec.normalize(df)
        if arity_bad:
            bad = pl.concat(
                [
                    bad,
                    pl.DataFrame(
                        {
                            "raw_line": [r for r, _ in arity_bad],
                            "reason": [m for _, m in arity_bad],
                        },
                        schema=BAD_SCHEMA,
                    ),
                ]
            )
        yield good, bad


def _collect(src, spec: _Spec) -> tuple[pl.DataFrame, pl.DataFrame]:
    goods: list[pl.DataFrame] = []
    bads: list[pl.DataFrame] = []
    for good, bad in _iter(src, spec, None):
        goods.append(good)
        bads.append(bad)
    good = pl.concat(goods) if goods else pl.DataFrame(schema=spec.schema)
    bad = pl.concat(bads) if bads else pl.DataFrame(schema=BAD_SCHEMA)
    return good, bad


# --------------------------------------------------------------------------
# read_votes -- votacao_secao_<YEAR>_<UF>.csv
# --------------------------------------------------------------------------

VOTES_SCHEMA: dict[str, pl.DataType] = {
    "uf": pl.Utf8,
    "mun_code": pl.Utf8,
    "mun_name": pl.Utf8,
    "zone": pl.Int32,
    "section": pl.Int32,
    "race": pl.Utf8,
    "round": pl.Int8,
    "votavel_kind": pl.Utf8,
    "cand_number": pl.Int32,
    "cand_name": pl.Utf8,
    "party_number": pl.Int32,
    "votes": pl.Int64,
    "election_id": pl.Int32,
}


def _votavel_kind() -> pl.Expr:
    nr = _int("NR_VOTAVEL")
    return (
        pl.when(nr == 95)
        .then(pl.lit("blank"))
        .when(nr == 96)
        .then(pl.lit("null"))
        .when(_race().is_in(list(PROPORTIONAL_RACES)) & nr.is_between(10, 99))
        .then(pl.lit("legenda"))
        .otherwise(pl.lit("candidate"))
    )


def _party_number() -> pl.Expr:
    nr_txt = pl.col("NR_VOTAVEL").str.strip_chars()
    nr = _int("NR_VOTAVEL")
    kind = _votavel_kind()
    return (
        pl.when(kind == "legenda")
        .then(nr)
        .when((kind == "candidate") & (nr_txt.str.len_chars() >= 2))
        .then(nr_txt.str.slice(0, 2).cast(pl.Int64, strict=False))
        .otherwise(None)
        .cast(pl.Int32)
    )


def _normalize_votes(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    votes = _int("QT_VOTOS")
    checks = _common_checks() + [
        _bad_when(_int("NR_SECAO").is_null(), _with_value("invalid NR_SECAO=", "NR_SECAO")),
        _bad_when(_int("NR_VOTAVEL").is_null(), _with_value("invalid NR_VOTAVEL=", "NR_VOTAVEL")),
        _bad_when(votes.is_null(), _with_value("QT_VOTOS not an integer: ", "QT_VOTOS")),
        _bad_when(
            votes.is_in(list(NA_SENTINELS)),
            _with_value("QT_VOTOS unavailable sentinel: ", "QT_VOTOS"),
        ),
        _bad_when(votes < 0, _with_value("negative QT_VOTOS: ", "QT_VOTOS")),
    ]
    kind = _votavel_kind()
    out = {
        "uf": _txt("SG_UF"),
        "mun_code": _mun_code(),
        "mun_name": _txt("NM_MUNICIPIO"),
        "zone": _int("NR_ZONA").cast(pl.Int32),
        "section": _int("NR_SECAO").cast(pl.Int32),
        "race": _race(),
        "round": _round(),
        "votavel_kind": kind,
        # NOTE: cand_number is the RAW votável number for every row, 95/96
        # included (Q11).  Filter on votavel_kind == 'candidate' before
        # treating it as a candidate number.
        "cand_number": _int("NR_VOTAVEL").cast(pl.Int32),
        "cand_name": _txt("NM_VOTAVEL"),
        "party_number": _party_number(),
        "votes": votes,
        "election_id": _int("CD_ELEICAO").cast(pl.Int32),
    }
    return _split(df, checks, out)


_VOTES = _Spec(
    kind="votacao_secao",
    required=(
        "SG_UF",
        "CD_MUNICIPIO",
        "NM_MUNICIPIO",
        "NR_ZONA",
        "NR_SECAO",
        "CD_CARGO",
        "NR_TURNO",
        "NR_VOTAVEL",
        "NM_VOTAVEL",
        "QT_VOTOS",
        "CD_ELEICAO",
    ),
    schema=VOTES_SCHEMA,
    normalize=_normalize_votes,
)


def read_votes(src) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read a ``votacao_secao`` file (section x votável vote counts)."""
    return _collect(src, _VOTES)


def iter_votes(src, batch_size: int = DEFAULT_BATCH_SIZE):
    """Stream ``read_votes`` in ``(good, bad)`` batches -- use for big members."""
    return _iter(src, _VOTES, batch_size)


# --------------------------------------------------------------------------
# read_detail -- detalhe_votacao_secao_<YEAR>_<UF|BR>.csv
# --------------------------------------------------------------------------

DETAIL_SCHEMA: dict[str, pl.DataType] = {
    "uf": pl.Utf8,
    "mun_code": pl.Utf8,
    "mun_name": pl.Utf8,
    "zone": pl.Int32,
    "section": pl.Int32,
    "race": pl.Utf8,
    "round": pl.Int8,
    "aptos": pl.Int64,
    "comparecimento": pl.Int64,
    "abstentions": pl.Int64,
    "blank": pl.Int64,
    "null_votes": pl.Int64,
    "agg_secoes": pl.Int32,
    "nominal": pl.Int64,
    "legenda": pl.Int64,
    "annulled_apu_sep": pl.Int64,
    "installed": pl.Boolean,
    "annulled": pl.Boolean,
    "vote_origin": pl.Utf8,
    "urna_model": pl.Utf8,
    "local_id": pl.Int32,
    "election_id": pl.Int32,
}

_DETAIL_COUNTS = (
    ("aptos", "QT_APTOS"),
    ("comparecimento", "QT_COMPARECIMENTO"),
    ("abstentions", "QT_ABSTENCOES"),
    ("blank", "QT_VOTOS_BRANCOS"),
    ("null_votes", "QT_VOTOS_NULOS"),
    ("nominal", "QT_VOTOS_NOMINAIS"),
    ("legenda", "QT_VOTOS_LEGENDA"),
    ("annulled_apu_sep", "QT_VOTOS_ANULADOS_APU_SEP"),
)


def _count_checks(pairs: Sequence[tuple[str, str]]) -> list[pl.Expr]:
    """Non-numeric -> bad; -1/-3 -> NULL (handled by _count); any other
    negative -> bad (a real negative count is corruption, not a sentinel)."""
    checks: list[pl.Expr] = []
    for _, col in pairs:
        raw = _int(col)
        checks.append(
            _bad_when(raw.is_null(), _with_value(f"{col} not an integer: ", col))
        )
        checks.append(
            _bad_when(
                (raw < 0) & ~raw.is_in(list(NA_SENTINELS)),
                _with_value(f"negative {col}: ", col),
            )
        )
    return checks


def _normalize_detail(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    checks = (
        _common_checks()
        + [_bad_when(_int("NR_SECAO").is_null(), _with_value("invalid NR_SECAO=", "NR_SECAO"))]
        + _count_checks(_DETAIL_COUNTS)
    )
    out: dict[str, pl.Expr] = {
        "uf": _txt("SG_UF"),
        "mun_code": _mun_code(),
        "mun_name": _txt("NM_MUNICIPIO"),
        "zone": _int("NR_ZONA").cast(pl.Int32),
        "section": _int("NR_SECAO").cast(pl.Int32),
        "race": _race(),
        "round": _round(),
    }
    out |= {name: _count(col) for name, col in _DETAIL_COUNTS[:5]}
    # Q19/Q20/Q21: no per-section aggregation column exists anywhere in the
    # TSE section-level publication.  The column is kept (nullable) so the
    # downstream schema is stable; real, zone-level counts come from
    # read_rollup_detail().agg_secoes.
    out["agg_secoes"] = pl.lit(None, dtype=pl.Int32)
    out |= {name: _count(col) for name, col in _DETAIL_COUNTS[5:]}
    out |= {
        "installed": _bool_sn("ST_SECAO_INSTALADA"),
        "annulled": _bool_sn("ST_SECAO_ANULADA"),
        "vote_origin": _txt("DS_ORIGEM_VOTO"),
        "urna_model": _txt("CD_MODELO_URNA"),
        "local_id": _count("NR_LOCAL_VOTACAO").cast(pl.Int32),
        "election_id": _int("CD_ELEICAO").cast(pl.Int32),
    }
    # keep declared column order
    out = {name: out[name] for name in DETAIL_SCHEMA}
    return _split(df, checks, out)


_DETAIL = _Spec(
    kind="detalhe_votacao_secao",
    required=(
        "SG_UF",
        "CD_MUNICIPIO",
        "NR_ZONA",
        "NR_SECAO",
        "CD_CARGO",
        "NR_TURNO",
        "QT_APTOS",
        "QT_COMPARECIMENTO",
        "QT_ABSTENCOES",
        "QT_VOTOS_BRANCOS",
        "QT_VOTOS_NULOS",
        "CD_ELEICAO",
    ),
    schema=DETAIL_SCHEMA,
    normalize=_normalize_detail,
)


def read_detail(src) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read a ``detalhe_votacao_secao`` file (per-section turnout/blank/null)."""
    return _collect(src, _DETAIL)


def iter_detail(src, batch_size: int = DEFAULT_BATCH_SIZE):
    return _iter(src, _DETAIL, batch_size)


# --------------------------------------------------------------------------
# read_rollup_cand -- votacao_candidato_munzona_<YEAR>_<UF|BR>.csv
# --------------------------------------------------------------------------

ROLLUP_CAND_SCHEMA: dict[str, pl.DataType] = {
    "uf": pl.Utf8,
    "mun_code": pl.Utf8,
    "mun_name": pl.Utf8,
    "zone": pl.Int32,
    "race": pl.Utf8,
    "round": pl.Int8,
    "cand_seq": pl.Utf8,
    "cand_number": pl.Int32,
    "cand_name": pl.Utf8,
    "cand_urna_name": pl.Utf8,
    "party_number": pl.Int32,
    "party_sigla": pl.Utf8,
    "federation_number": pl.Int32,
    "federation_sigla": pl.Utf8,
    "coalition": pl.Utf8,
    "votes_nominal": pl.Int64,
    "votes_nominal_valid": pl.Int64,
    "in_transit": pl.Boolean,
    "situation": pl.Utf8,
    "election_id": pl.Int32,
}

_ROLLUP_CAND_COUNTS = (
    ("votes_nominal", "QT_VOTOS_NOMINAIS"),
    ("votes_nominal_valid", "QT_VOTOS_NOMINAIS_VALIDOS"),
)


def _normalize_rollup_cand(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    checks = _common_checks() + _count_checks(_ROLLUP_CAND_COUNTS)
    out = {
        "uf": _txt("SG_UF"),
        "mun_code": _mun_code(),
        "mun_name": _txt("NM_MUNICIPIO"),
        "zone": _int("NR_ZONA").cast(pl.Int32),
        "race": _race(),
        "round": _round(),
        "cand_seq": _txt("SQ_CANDIDATO"),
        "cand_number": _int("NR_CANDIDATO").cast(pl.Int32),
        "cand_name": _txt("NM_CANDIDATO"),
        "cand_urna_name": _txt("NM_URNA_CANDIDATO"),
        "party_number": _count("NR_PARTIDO").cast(pl.Int32),
        "party_sigla": _txt("SG_PARTIDO"),
        "federation_number": _count("NR_FEDERACAO").cast(pl.Int32),
        "federation_sigla": _txt("SG_FEDERACAO"),
        "coalition": _txt("DS_COMPOSICAO_COLIGACAO"),
        "votes_nominal": _count("QT_VOTOS_NOMINAIS"),
        "votes_nominal_valid": _count("QT_VOTOS_NOMINAIS_VALIDOS"),
        "in_transit": _bool_sn("ST_VOTO_EM_TRANSITO"),
        "situation": _txt("DS_SIT_TOT_TURNO"),
        "election_id": _int("CD_ELEICAO").cast(pl.Int32),
    }
    return _split(df, checks, out)


_ROLLUP_CAND = _Spec(
    kind="votacao_candidato_munzona",
    required=(
        "SG_UF",
        "CD_MUNICIPIO",
        "NR_ZONA",
        "CD_CARGO",
        "NR_TURNO",
        "NR_CANDIDATO",
        "NR_PARTIDO",
        "QT_VOTOS_NOMINAIS",
        "CD_ELEICAO",
    ),
    schema=ROLLUP_CAND_SCHEMA,
    normalize=_normalize_rollup_cand,
)


def read_rollup_cand(src) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read ``votacao_candidato_munzona`` (TSE's published nominal totals)."""
    return _collect(src, _ROLLUP_CAND)


def iter_rollup_cand(src, batch_size: int = DEFAULT_BATCH_SIZE):
    return _iter(src, _ROLLUP_CAND, batch_size)


# --------------------------------------------------------------------------
# read_rollup_party -- votacao_partido_munzona_<YEAR>_<UF|BR>.csv
# --------------------------------------------------------------------------

ROLLUP_PARTY_SCHEMA: dict[str, pl.DataType] = {
    "uf": pl.Utf8,
    "mun_code": pl.Utf8,
    "mun_name": pl.Utf8,
    "zone": pl.Int32,
    "race": pl.Utf8,
    "round": pl.Int8,
    "party_number": pl.Int32,
    "party_sigla": pl.Utf8,
    "federation_number": pl.Int32,
    "federation_sigla": pl.Utf8,
    "coalition": pl.Utf8,
    "votes_legenda_valid": pl.Int64,
    "votes_nom_convr_leg_valid": pl.Int64,
    "votes_total_leg_valid": pl.Int64,
    "votes_nominal_valid": pl.Int64,
    "in_transit": pl.Boolean,
    "election_id": pl.Int32,
}

_ROLLUP_PARTY_COUNTS = (
    ("votes_legenda_valid", "QT_VOTOS_LEGENDA_VALIDOS"),
    ("votes_nom_convr_leg_valid", "QT_VOTOS_NOM_CONVR_LEG_VALIDOS"),
    ("votes_total_leg_valid", "QT_TOTAL_VOTOS_LEG_VALIDOS"),
    ("votes_nominal_valid", "QT_VOTOS_NOMINAIS_VALIDOS"),
)


def _normalize_rollup_party(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    checks = _common_checks() + _count_checks(_ROLLUP_PARTY_COUNTS)
    out = {
        "uf": _txt("SG_UF"),
        "mun_code": _mun_code(),
        "mun_name": _txt("NM_MUNICIPIO"),
        "zone": _int("NR_ZONA").cast(pl.Int32),
        "race": _race(),
        "round": _round(),
        "party_number": _count("NR_PARTIDO").cast(pl.Int32),
        "party_sigla": _txt("SG_PARTIDO"),
        "federation_number": _count("NR_FEDERACAO").cast(pl.Int32),
        "federation_sigla": _txt("SG_FEDERACAO"),
        "coalition": _txt("DS_COMPOSICAO_COLIGACAO"),
    }
    out |= {name: _count(col) for name, col in _ROLLUP_PARTY_COUNTS}
    out |= {
        "in_transit": _bool_sn("ST_VOTO_EM_TRANSITO"),
        "election_id": _int("CD_ELEICAO").cast(pl.Int32),
    }
    out = {name: out[name] for name in ROLLUP_PARTY_SCHEMA}
    return _split(df, checks, out)


_ROLLUP_PARTY = _Spec(
    kind="votacao_partido_munzona",
    required=(
        "SG_UF",
        "CD_MUNICIPIO",
        "NR_ZONA",
        "CD_CARGO",
        "NR_TURNO",
        "NR_PARTIDO",
        "QT_VOTOS_LEGENDA_VALIDOS",
        "CD_ELEICAO",
    ),
    schema=ROLLUP_PARTY_SCHEMA,
    normalize=_normalize_rollup_party,
)


def read_rollup_party(src) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read ``votacao_partido_munzona`` (TSE's published legenda totals)."""
    return _collect(src, _ROLLUP_PARTY)


def iter_rollup_party(src, batch_size: int = DEFAULT_BATCH_SIZE):
    return _iter(src, _ROLLUP_PARTY, batch_size)


# --------------------------------------------------------------------------
# read_rollup_detail -- detalhe_votacao_munzona_<YEAR>_<UF|BR>.csv
# --------------------------------------------------------------------------

ROLLUP_DETAIL_SCHEMA: dict[str, pl.DataType] = {
    "uf": pl.Utf8,
    "mun_code": pl.Utf8,
    "mun_name": pl.Utf8,
    "zone": pl.Int32,
    "race": pl.Utf8,
    "round": pl.Int8,
    "aptos": pl.Int64,
    "comparecimento": pl.Int64,
    "abstentions": pl.Int64,
    "blank": pl.Int64,
    "null_votes": pl.Int64,
    "total_null_votes": pl.Int64,
    "nominal_valid": pl.Int64,
    "legenda_valid": pl.Int64,
    "total_valid": pl.Int64,
    "sec_principais": pl.Int32,
    "agg_secoes": pl.Int32,
    "sec_nao_instaladas": pl.Int32,
    "sec_total": pl.Int32,
    "in_transit": pl.Boolean,
    "election_id": pl.Int32,
}

_ROLLUP_DETAIL_COUNTS = (
    ("aptos", "QT_APTOS"),
    ("comparecimento", "QT_COMPARECIMENTO"),
    ("abstentions", "QT_ABSTENCOES"),
    ("blank", "QT_VOTOS_BRANCOS"),
    ("null_votes", "QT_VOTOS_NULOS"),
    ("total_null_votes", "QT_TOTAL_VOTOS_NULOS"),
    ("nominal_valid", "QT_VOTOS_NOMINAIS_VALIDOS"),
    ("legenda_valid", "QT_VOTOS_LEG_VALIDOS"),
    ("total_valid", "QT_TOTAL_VOTOS_VALIDOS"),
)
_ROLLUP_DETAIL_SECS = (
    ("sec_principais", "QT_SECOES_PRINCIPAIS"),
    ("agg_secoes", "QT_SECOES_AGREGADAS"),
    ("sec_nao_instaladas", "QT_SECOES_NAO_INSTALADAS"),
    ("sec_total", "QT_TOTAL_SECOES"),
)


def _normalize_rollup_detail(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    checks = _common_checks() + _count_checks(_ROLLUP_DETAIL_COUNTS + _ROLLUP_DETAIL_SECS)
    out = {
        "uf": _txt("SG_UF"),
        "mun_code": _mun_code(),
        "mun_name": _txt("NM_MUNICIPIO"),
        "zone": _int("NR_ZONA").cast(pl.Int32),
        "race": _race(),
        "round": _round(),
    }
    out |= {name: _count(col) for name, col in _ROLLUP_DETAIL_COUNTS}
    # Q19: this is the ONLY place TSE publishes aggregated-section counts,
    # and it is per (municipality, zone, race, round) -- not per section.
    out |= {name: _count(col).cast(pl.Int32) for name, col in _ROLLUP_DETAIL_SECS}
    out |= {
        "in_transit": _bool_sn("ST_VOTO_EM_TRANSITO"),
        "election_id": _int("CD_ELEICAO").cast(pl.Int32),
    }
    out = {name: out[name] for name in ROLLUP_DETAIL_SCHEMA}
    return _split(df, checks, out)


_ROLLUP_DETAIL = _Spec(
    kind="detalhe_votacao_munzona",
    required=(
        "SG_UF",
        "CD_MUNICIPIO",
        "NR_ZONA",
        "CD_CARGO",
        "NR_TURNO",
        "QT_APTOS",
        "QT_COMPARECIMENTO",
        "QT_SECOES_AGREGADAS",
        "CD_ELEICAO",
    ),
    schema=ROLLUP_DETAIL_SCHEMA,
    normalize=_normalize_rollup_detail,
)


def read_rollup_detail(src) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read ``detalhe_votacao_munzona`` (TSE's published turnout totals)."""
    return _collect(src, _ROLLUP_DETAIL)


def iter_rollup_detail(src, batch_size: int = DEFAULT_BATCH_SIZE):
    return _iter(src, _ROLLUP_DETAIL, batch_size)
