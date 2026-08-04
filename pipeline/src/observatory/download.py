"""Idempotent download layer for the TSE source archives.

Contract:

* One command fetches exactly the files a run needs into `data/raw/`.
* Every file's SHA-256 is recorded in `data/raw/checksums.json`.
* A file already on disk whose checksum matches the recorded one is **never**
  re-fetched; a file whose checksum *mismatches* is a hard error, not a silent
  re-download. Raw files are never mutated in place.
* Archives are never extracted — downstream readers stream from the zips. The
  disk budget depends on this.
* `_fetch` is the single network primitive. Tests monkeypatch exactly that name
  (and `RAW_DIR`), so nothing else in this module may open a socket. Both are
  read as module globals at call time, which is what makes the monkeypatching
  work.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from observatory import sources
from observatory.config import settings

RAW_DIR: Path = settings.raw_dir

CHECKSUMS_FILE = "checksums.json"

_USER_AGENT = "observatory-pipeline/0.1 (+https://github.com/; election data research)"
_CHUNK = 1 << 20
_TIMEOUT = 120


class DownloadError(RuntimeError):
    """Raised when a raw file cannot be trusted."""


class ChecksumMismatch(DownloadError):
    """An existing raw file no longer matches its recorded SHA-256."""


@dataclass
class DownloadReport:
    fetched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# network primitive
# --------------------------------------------------------------------------


def _fetch(url: str, dest: Path) -> None:
    """Stream `url` to `dest`, atomically.

    Downloads to a sibling `.part` file and renames on completion, so an
    interrupted run can never leave a truncated file that looks complete.
    """
    tmp = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            with open(tmp, "wb") as handle:
                while chunk := response.read(_CHUNK):
                    handle.write(chunk)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# checksums
# --------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _load_checksums(raw_dir: Path) -> dict[str, str]:
    path = raw_dir / CHECKSUMS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DownloadError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DownloadError(f"{path} must contain a filename -> sha256 object")
    return {str(k): str(v) for k, v in data.items()}


def _save_checksums(raw_dir: Path, checksums: dict[str, str]) -> None:
    path = raw_dir / CHECKSUMS_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(dict(sorted(checksums.items())), indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def _ensure_file(
    url: str,
    dest: Path,
    checksums: dict[str, str],
    report: DownloadReport,
) -> Path:
    """Make `dest` exist with a recorded, verified checksum. Idempotent."""
    name = dest.name
    if dest.exists():
        actual = _sha256(dest)
        recorded = checksums.get(name)
        if recorded is None:
            checksums[name] = actual
            report.adopted.append(name)
            _save_checksums(dest.parent, checksums)
        elif recorded != actual:
            raise ChecksumMismatch(
                f"{dest} does not match its recorded SHA-256.\n"
                f"  recorded: {recorded}\n  on disk:  {actual}\n"
                "Raw files are immutable. Investigate, then delete the file and "
                "its checksums.json entry to re-download deliberately."
            )
        else:
            report.skipped.append(name)
        return dest

    _fetch(url, dest)
    checksums[name] = _sha256(dest)
    report.fetched.append(name)
    _save_checksums(dest.parent, checksums)
    return dest


# --------------------------------------------------------------------------
# CKAN resolution (urna correspondence)
# --------------------------------------------------------------------------


def _ckan_index(
    ckan_id: str,
    raw_dir: Path,
    checksums: dict[str, str],
    report: DownloadReport,
    cache: dict[str, dict[str, str] | None],
) -> dict[str, str] | None:
    """Map `prefix -> newest resource URL` for one CKAN package.

    The package listing itself is fetched through `_fetch` (the single network
    primitive) and cached in `data/raw/` like any other raw artifact, so a
    second run resolves without touching the network. Delete
    `ckan_<id>.json` to force a refresh.

    Returns None if the listing cannot be fetched or parsed; callers treat that
    as "correspondence unresolved" rather than a fatal error.
    """
    if ckan_id in cache:
        return cache[ckan_id]

    dest = raw_dir / f"ckan_{ckan_id}.json"
    try:
        _ensure_file(sources.CKAN_API + ckan_id, dest, checksums, report)
        payload: Any = json.loads(dest.read_text(encoding="utf-8", errors="strict"))
        resources = payload["result"]["resources"]
    except ChecksumMismatch:
        raise
    except Exception as exc:  # noqa: BLE001 - CKAN failure must never be fatal
        print(f"warning: could not read CKAN package {ckan_id}: {exc}", file=sys.stderr)
        cache[ckan_id] = None
        return None

    newest: dict[str, tuple[datetime, str]] = {}
    for resource in resources:
        url = (resource or {}).get("url") or ""
        filename = url.rsplit("/", 1)[-1]
        if not filename.endswith(".zip"):
            continue
        stem = filename[: -len(".zip")]
        prefix, _, stamp = stem.rpartition("_")
        if not prefix or len(stamp) != 12 or not stamp.isdigit():
            continue
        try:
            published = datetime.strptime(stamp, "%d%m%Y%H%M")
        except ValueError:
            continue
        current = newest.get(prefix)
        if current is None or published > current[0]:
            newest[prefix] = (published, url)

    index = {prefix: url for prefix, (_, url) in newest.items()}
    cache[ckan_id] = index
    return index


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------


def fetch_all_report(uf: str | None = None) -> DownloadReport:
    """Fetch every dataset for `uf` and report what happened."""
    raw_dir = RAW_DIR  # read at call time so tests can monkeypatch it
    raw_dir.mkdir(parents=True, exist_ok=True)
    checksums = _load_checksums(raw_dir)
    report = DownloadReport()
    ckan_cache: dict[str, dict[str, str] | None] = {}

    for dataset in sources.datasets(uf):
        url = dataset.url
        if url is None:
            if dataset.resolver != "ckan" or dataset.ckan_id is None:
                raise DownloadError(f"{dataset.name} has no url and no CKAN resolver")
            index = _ckan_index(dataset.ckan_id, raw_dir, checksums, report, ckan_cache)
            url = (index or {}).get(dataset.name)
            if url is None:
                report.unresolved.append(dataset.name)
                continue
        _ensure_file(url, raw_dir / url.rsplit("/", 1)[-1], checksums, report)

    _save_checksums(raw_dir, checksums)
    if report.unresolved:
        print(
            "warning: unresolved urna_correspondence datasets: "
            + ", ".join(sorted(report.unresolved)),
            file=sys.stderr,
        )
    return report


def fetch_all(uf: str | None = None) -> None:
    """Idempotently download every source file a run for `uf` needs."""
    fetch_all_report(uf)


def plan(uf: str | None = None) -> list[str]:
    """Describe what a run would fetch, without touching the network."""
    lines = []
    for dataset in sources.datasets(uf):
        target = dataset.filename or f"<resolved via CKAN: {dataset.name}_*.zip>"
        lines.append(f"{dataset.kind:<24} {dataset.uf or '-':<3} {target}")
    return lines
