"""The export validation gate — the second hard gate, and the last thing that
stands between Postgres and a published site.

    from observatory.export import validate

    report = validate.run_gate(out_root, db)     # raises ExportGateError

WHAT IT CHECKS, IN THIS ORDER, ALWAYS FROM DISK
    0.  the manifest exists, is ``v == 1``, covers exactly the files on disk,
        and every physical file's sha256 and byte count are what it claims
    1.  every file parses (gunzip + JSON) and its arrays line up
    2.  every file carries ``v == 1``
    3.  every shard's nominal totals — per race, the COO ``n`` column summed —
        equal ``fact_votes[votavel_kind='candidate']`` for that municipality
        (or that zone, for a subshard), and every ``stats`` array, including
        ``blank``, ``null_votes`` and ``legenda``, equals its Postgres
        counterpart
    4.  no shard is over 1 MiB; shards over 300 KiB are warn-listed
    5.  at most 20 000 files, none over 25 MiB

    Nothing short-circuits.  A failing gate produces the FULL list of findings
    and only then raises :class:`ExportGateError`, because the report is the
    diagnostic — an export that is wrong in four places should say so once, not
    across four re-runs.

WHY FROM DISK, ALWAYS
    The gate never validates an in-memory document the exporter handed it.  Its
    entire reason to exist is catching a bad *write* — a truncated gzip, a file
    the manifest lost track of, a stale shard from a previous UF — and every
    one of those is invisible to an in-process check.  So it re-reads, re-hashes
    and re-parses the bytes a browser would download.

THE POSTGRES COUNTERPARTS
    ``votes``      SUM(fact_votes.votes) WHERE votavel_kind = 'candidate'
    ``legenda``    SUM(fact_votes.votes) WHERE votavel_kind = 'legenda'
    ``aptos`` ``comp`` ``blank`` ``null_votes``
                   SUM over fact_section_stats
    all grouped by (mun_code, zone, race, round) so a subshard can be checked
    against exactly its own slice.  Those are the columns the shard writer read;
    comparing against anything else would be checking a different question.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db import Database
from . import manifest, shards

__all__ = [
    "ExportGateError",
    "Finding",
    "GATE_NAME",
    "GateReport",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "run_gate",
]

#: ``meta.gate_runs.gate`` for this gate.
GATE_NAME = "export"

#: Hosting limits (Cloudflare Pages).  Task 24 re-checks the WHOLE build
#: directory; here we check the data export, which is the part that scales with
#: the country.
MAX_FILES = 20_000
MAX_FILE_BYTES = 25 * 1024 * 1024

#: Findings are a diagnostic, not a data set.  A systematic defect at national
#: scale would otherwise produce tens of thousands of identical lines.
MAX_FINDINGS = 200

#: The shard fields whose sums are compared with Postgres.  ``votes`` is the
#: sparse COO column; the rest are the dense ``stats`` arrays.
_MEASURED: tuple[str, ...] = ("votes", *shards.STAT_FIELDS)


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One defect, named by the check that found it."""

    kind: str          # manifest | parse | version | totals | budget | limits
    file: str          # logical name, or a relative path for unmanaged files
    message: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.file}: {self.message}"


@dataclass
class GateReport:
    """What the gate saw.  ``ok`` is the whole point."""

    root: Path
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: int = 0
    count: int = 0
    bytes: int = 0
    shard_files: int = 0
    largest: tuple[str, int] | None = None
    over_target: list[tuple[str, int]] = field(default_factory=list)
    municipalities: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, kind: str, file: str, message: str) -> None:
        if len(self.findings) >= MAX_FINDINGS:
            self.truncated += 1
            return
        self.findings.append(Finding(kind, file, message))

    def summary(self) -> str:
        largest = (
            f"{self.largest[0]} {self.largest[1]:,} B" if self.largest else "none"
        )
        head = (
            f"{'PASS' if self.ok else 'FAIL'} — {self.count:,} files, "
            f"{self.bytes:,} B, {self.shard_files:,} shards over "
            f"{self.municipalities:,} municipalities; largest shard {largest}; "
            f"{len(self.over_target)} over the {shards.SHARD_TARGET_BYTES:,} B target"
        )
        if self.ok:
            return head
        kinds: dict[str, int] = {}
        for finding in self.findings:
            kinds[finding.kind] = kinds.get(finding.kind, 0) + 1
        counts = ", ".join(f"{kind}={n}" for kind, n in sorted(kinds.items()))
        more = f" (+{self.truncated:,} more suppressed)" if self.truncated else ""
        return f"{head}; {len(self.findings)} findings: {counts}{more}"

    def report(self) -> str:
        lines = [self.summary()]
        lines += [f"  {finding}" for finding in self.findings]
        if self.truncated:
            lines.append(f"  … {self.truncated:,} further findings suppressed")
        return "\n".join(lines)


class ExportGateError(RuntimeError):
    """The export is not publishable.  Carries every finding, not the first."""

    def __init__(self, report: GateReport) -> None:
        super().__init__(f"export gate failed: {report.report()}")
        self.report = report
        self.findings: list[Finding] = list(report.findings)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def run_gate(
    export_root: Path | str,
    db: Database,
    *,
    raise_on_failure: bool = True,
) -> GateReport:
    """Validate the export at ``export_root`` against ``db``.

    Stamps ``meta.gate_runs`` either way — a failing gate's evidence is worth
    at least as much as a passing one's — and raises :class:`ExportGateError`
    unless ``raise_on_failure=False``.
    """
    root = Path(export_root)
    report = GateReport(root=root)

    # -- 0. the manifest --------------------------------------------------
    try:
        doc = manifest.load(root)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        report.add("manifest", manifest.MANIFEST_NAME, str(exc))
        return _finish(db, report, raise_on_failure)

    if doc.get("v") != manifest.MANIFEST_VERSION:
        report.add(
            "version", manifest.MANIFEST_NAME,
            f"manifest v={doc.get('v')!r}, expected {manifest.MANIFEST_VERSION}",
        )

    entries: dict[str, dict[str, Any]] = doc["files"]
    report.count = len(entries)
    report.bytes = sum(int(entry["bytes"]) for entry in entries.values())
    _check_coverage(root, entries, report)

    expected = _Expected.read(db)
    report.municipalities = len(expected.zones)

    for logical in sorted(entries):
        entry = entries[logical]
        blob = _verify_file(root, logical, entry, report)
        if blob is None:
            continue
        # -- 1. parses ----------------------------------------------------
        parsed = _parse(logical, blob, report)
        if parsed is None:
            continue
        # -- 2. version ---------------------------------------------------
        if parsed.get("v") != manifest.MANIFEST_VERSION:
            report.add(
                "version", logical,
                f"v={parsed.get('v')!r}, expected {manifest.MANIFEST_VERSION}",
            )
        if logical.startswith("shards/"):
            report.shard_files += 1
            # -- 3. totals, against Postgres ------------------------------
            _check_shard(logical, parsed, entries, expected, report)
            # -- 4. shard budgets -----------------------------------------
            _check_shard_budget(logical, int(entry["bytes"]), report)
        # -- 5. hosting limits ------------------------------------------
        if int(entry["bytes"]) > MAX_FILE_BYTES:
            report.add(
                "limits", logical,
                f"{int(entry['bytes']):,} B exceeds the {MAX_FILE_BYTES:,} B "
                "per-file hosting limit",
            )

    if report.count > MAX_FILES:
        report.add(
            "limits", manifest.MANIFEST_NAME,
            f"{report.count:,} files exceeds the {MAX_FILES:,} file hosting limit",
        )

    return _finish(db, report, raise_on_failure)


def _finish(db: Database, report: GateReport, raise_on_failure: bool) -> GateReport:
    if report.ok:
        db.stamp_gate(GATE_NAME, True, report.summary())
        return report
    db.stamp_gate_out_of_band(GATE_NAME, False, report.summary())
    if raise_on_failure:
        raise ExportGateError(report)
    return report


# --------------------------------------------------------------------------
# 0. Manifest coverage and per-file integrity
# --------------------------------------------------------------------------


def _check_coverage(
    root: Path, entries: dict[str, dict[str, Any]], report: GateReport
) -> None:
    """The manifest must describe the directory exactly.

    Both directions matter: a listed-but-absent file is a 404 on the site, and
    an unlisted file on disk is either a stale artefact from a previous run or
    a write nobody accounted for.
    """
    on_disk = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    } - {manifest.MANIFEST_NAME}
    listed = manifest.physical_paths({"files": entries})

    for stray in sorted(on_disk - listed):
        report.add("manifest", stray, "on disk but not listed in the manifest")
    for missing in sorted(listed - on_disk):
        logical = next(k for k, v in entries.items() if v["path"] == missing)
        report.add("manifest", logical, f"manifest lists {missing}, which is not on disk")


def _verify_file(
    root: Path, logical: str, entry: dict[str, Any], report: GateReport
) -> bytes | None:
    """Read one physical file and check it is the bytes the manifest promises."""
    path = root / entry["path"]
    try:
        blob = path.read_bytes()
    except OSError as exc:
        report.add("manifest", logical, f"unreadable: {exc}")
        return None
    digest = manifest.sha256_bytes(blob)
    if digest != entry["sha256"]:
        report.add(
            "manifest", logical,
            f"sha256 mismatch: file {digest}, manifest {entry['sha256']}",
        )
        return None
    if len(blob) != int(entry["bytes"]):
        report.add(
            "manifest", logical,
            f"byte count mismatch: file {len(blob):,}, manifest {int(entry['bytes']):,}",
        )
    if entry["path"] != manifest.hashed_name(logical, digest):
        report.add(
            "manifest", logical,
            f"physical name {entry['path']} is not the content address of its "
            f"bytes (expected {manifest.hashed_name(logical, digest)})",
        )
    return blob


# --------------------------------------------------------------------------
# 1. Parsing + structural alignment
# --------------------------------------------------------------------------


def _parse(logical: str, blob: bytes, report: GateReport) -> dict[str, Any] | None:
    try:
        raw = gzip.decompress(blob) if logical.endswith(".gz") else blob
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.add("parse", logical, f"{exc.__class__.__name__}: {exc}")
        return None
    if not isinstance(doc, dict):
        report.add("parse", logical, f"top level is {type(doc).__name__}, not an object")
        return None
    if logical.startswith("shards/") and not doc.get("split"):
        _check_alignment(logical, doc, report)
    return doc


def _check_alignment(logical: str, doc: dict[str, Any], report: GateReport) -> None:
    """Columnar arrays are only data if they line up.

    A shard is parallel arrays indexed by section position; one short column
    silently shifts every value after it, which no downstream assertion would
    ever notice.  This is the cheapest possible place to notice.
    """
    n = len(doc.get("zone") or [])
    if len(doc.get("section") or []) != n:
        report.add(
            "parse", logical,
            f"zone[{n}] and section[{len(doc.get('section') or [])}] have "
            "different lengths",
        )
        return
    for rid, columns in (doc.get("stats") or {}).items():
        for name, column in columns.items():
            if len(column) != n:
                report.add(
                    "parse", logical,
                    f"stats.{rid}.{name} has {len(column)} entries for {n} sections",
                )
    for rid, coo in (doc.get("votes") or {}).items():
        lengths = {name: len(coo.get(name) or []) for name in ("sec", "cand", "n")}
        if len(set(lengths.values())) > 1:
            report.add("parse", logical, f"votes.{rid} COO columns disagree: {lengths}")
            continue
        out_of_range = [sec for sec in coo.get("sec", ()) if not 0 <= sec < n]
        if out_of_range:
            report.add(
                "parse", logical,
                f"votes.{rid} references section index {out_of_range[0]} "
                f"outside 0..{n - 1}",
            )
    for rid, keys in (doc.get("scores") or {}).items():
        for key, column in keys.items():
            if len(column) != n:
                report.add(
                    "parse", logical,
                    f"scores.{rid}.{key} has {len(column)} entries for {n} sections",
                )


# --------------------------------------------------------------------------
# 3. Totals against Postgres
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Expected:
    """Postgres' answer, grouped fine enough to check a per-zone subshard.

    ``sums[(mun_code, zone)][race_id]`` holds one dict per measured field, so a
    whole-municipality shard is checked against the sum over its zones and a
    subshard against exactly its own.
    """

    sums: dict[tuple[str, int], dict[str, dict[str, int]]]
    zones: dict[str, set[int]]

    @classmethod
    def read(cls, db: Database) -> "_Expected":
        sums: dict[tuple[str, int], dict[str, dict[str, int]]] = {}

        def cell(mun: str, zone: int, race: str, rnd: int) -> dict[str, int]:
            per_race = sums.setdefault((mun, int(zone)), {})
            return per_race.setdefault(
                shards.race_id(race, rnd), dict.fromkeys(_MEASURED, 0)
            )

        for mun, zone, race, rnd, votes, legenda in db.fetch(
            """
            SELECT s.mun_code, s.zone, v.race, v.round,
                   COALESCE(SUM(v.votes) FILTER (WHERE v.votavel_kind = 'candidate'), 0),
                   COALESCE(SUM(v.votes) FILTER (WHERE v.votavel_kind = 'legenda'), 0)
            FROM fact_votes v
            JOIN dim_section s ON s.id = v.section_id
            WHERE v.votavel_kind IN ('candidate', 'legenda')
            GROUP BY 1, 2, 3, 4
            """
        ):
            entry = cell(mun, zone, race, rnd)
            entry["votes"] = int(votes or 0)
            entry["legenda"] = int(legenda or 0)

        for mun, zone, race, rnd, aptos, comp, blank, nulls in db.fetch(
            """
            SELECT s.mun_code, s.zone, f.race, f.round,
                   COALESCE(SUM(f.aptos), 0), COALESCE(SUM(f.comparecimento), 0),
                   COALESCE(SUM(f.blank), 0), COALESCE(SUM(f.null_votes), 0)
            FROM fact_section_stats f
            JOIN dim_section s ON s.id = f.section_id
            GROUP BY 1, 2, 3, 4
            """
        ):
            entry = cell(mun, zone, race, rnd)
            entry["aptos"] = int(aptos or 0)
            entry["comp"] = int(comp or 0)
            entry["blank"] = int(blank or 0)
            entry["null_votes"] = int(nulls or 0)

        zones: dict[str, set[int]] = {}
        for mun, zone in db.fetch(
            "SELECT DISTINCT mun_code, zone FROM dim_section ORDER BY 1, 2"
        ):
            zones.setdefault(mun, set()).add(int(zone))
        return cls(sums, zones)

    def scope(self, code: str, zones: set[int]) -> dict[str, dict[str, int]]:
        """Expected sums per race id over ``zones`` of municipality ``code``."""
        merged: dict[str, dict[str, int]] = {}
        for zone in sorted(zones):
            for rid, entry in self.sums.get((code, zone), {}).items():
                target = merged.setdefault(rid, dict.fromkeys(_MEASURED, 0))
                for name in _MEASURED:
                    target[name] += entry[name]
        return merged


def _check_shard(
    logical: str,
    doc: dict[str, Any],
    entries: dict[str, dict[str, Any]],
    expected: _Expected,
    report: GateReport,
) -> None:
    code = doc.get("code")
    if not isinstance(code, str) or code not in expected.zones:
        report.add(
            "totals", logical,
            f"shard claims municipality {code!r}, which has no sections in Postgres",
        )
        return

    if doc.get("split"):
        _check_split_index(logical, doc, code, entries, expected, report)
        return

    zone_only = doc.get("zone_only")
    zones = {int(zone_only)} if zone_only is not None else set(expected.zones[code])
    want = expected.scope(code, zones)
    got = _measure(doc)

    scope = f"zone {zone_only}" if zone_only is not None else "all zones"
    for rid in sorted(set(want) | set(got)):
        wanted = want.get(rid, dict.fromkeys(_MEASURED, 0))
        found = got.get(rid, dict.fromkeys(_MEASURED, 0))
        for name in _MEASURED:
            if found[name] != wanted[name]:
                report.add(
                    "totals", logical,
                    f"{rid} {name} totals disagree ({scope}): shard "
                    f"{found[name]:,}, postgres {wanted[name]:,}",
                )


def _measure(doc: dict[str, Any]) -> dict[str, dict[str, int]]:
    """The shard's own totals per race id, summed from what is on disk."""
    out: dict[str, dict[str, int]] = {}

    def cell(rid: str) -> dict[str, int]:
        return out.setdefault(rid, dict.fromkeys(_MEASURED, 0))

    for rid, coo in (doc.get("votes") or {}).items():
        cell(rid)["votes"] = sum(int(n) for n in coo.get("n", ()))
    for rid, columns in (doc.get("stats") or {}).items():
        entry = cell(rid)
        for name in shards.STAT_FIELDS:
            entry[name] = sum(int(v) for v in columns.get(name, ()))
    return out


def _check_split_index(
    logical: str,
    doc: dict[str, Any],
    code: str,
    entries: dict[str, dict[str, Any]],
    expected: _Expected,
    report: GateReport,
) -> None:
    """A split index carries no votes — its job is to name every subshard.

    So what has to be true of it is coverage: the zones it lists are exactly
    the municipality's zones, and each one is a file the manifest resolves.
    The subshards' own totals are checked when their turn comes.
    """
    listed = {int(zone) for zone in doc.get("zones") or ()}
    if listed != expected.zones[code]:
        report.add(
            "totals", logical,
            f"split index lists zones {sorted(listed)}, Postgres has "
            f"{sorted(expected.zones[code])} — totals for the missing zones "
            "would never be checked",
        )
    for zone in sorted(listed):
        name = f"shards/{code}-z{zone}.json.gz"
        if name not in entries:
            report.add("totals", logical, f"subshard {name} is not in the manifest")


# --------------------------------------------------------------------------
# 4. Shard size budgets
# --------------------------------------------------------------------------


def _check_shard_budget(logical: str, size: int, report: GateReport) -> None:
    if size > shards.SHARD_HARD_BYTES:
        report.add(
            "budget", logical,
            f"{size:,} B gzipped exceeds the hard {shards.SHARD_HARD_BYTES:,} B "
            "shard budget",
        )
    elif size > shards.SHARD_TARGET_BYTES:
        report.over_target.append((logical, size))
        report.warnings.append(
            f"{logical}: {size:,} B gzipped is over the "
            f"{shards.SHARD_TARGET_BYTES:,} B target"
        )
    if report.largest is None or size > report.largest[1]:
        report.largest = (logical, size)
