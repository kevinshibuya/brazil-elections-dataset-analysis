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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

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
# entry points
# --------------------------------------------------------------------------


def fetch_report(datasets: Iterable[sources.Dataset]) -> DownloadReport:
    """Fetch exactly `datasets` and report what happened.

    The national run downloads in two waves — the national archives once, then
    one UF's section archive at a time so the reclaimer can delete it again
    (see `observatory.storage`) — which is why the dataset list is a parameter
    rather than always being derived from a UF.
    """
    raw_dir = RAW_DIR  # read at call time so tests can monkeypatch it
    raw_dir.mkdir(parents=True, exist_ok=True)
    checksums = _load_checksums(raw_dir)
    report = DownloadReport()

    for dataset in datasets:
        # A6: every dataset now carries a direct CDN url. The CKAN
        # `package_show` resolver that used to fill in the timestamped
        # correspondence filenames went with the archives it resolved.
        url = dataset.url
        if url is None:
            raise DownloadError(f"{dataset.name} has no url")
        _ensure_file(url, raw_dir / url.rsplit("/", 1)[-1], checksums, report)

    _save_checksums(raw_dir, checksums)
    return report


def fetch_all_report(uf: str | None = None) -> DownloadReport:
    """Fetch every dataset for `uf` and report what happened."""
    return fetch_report(sources.datasets(uf))


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
