"""Content-hashed physical names + the one mutable file that maps to them.

    from observatory.export import manifest

    doc = manifest.finalize(out_root)     # rename to hashed names, write manifest
    doc = manifest.load(out_root)
    ok  = manifest.verify_hashes(out_root)

THE MANIFEST SHAPE IS FROZEN (``v = 1``)
    Task 11's TypeScript data layer is written against exactly this::

        {
          "v": 1, "generated": "2026-08-04T12:00:00Z",
          "files": {
            "shards/03018.json.gz": {"path": "shards/03018.ab12cd34.json.gz",
                                     "sha256": "…", "bytes": 48213},
            "summary.json":         {"path": "summary.ab99ffee.json",
                                     "sha256": "…", "bytes": 3120}
          },
          "count": 22, "bytes": 1048576
        }

    ``v`` is the SINGLE version stamp of the whole export: the manifest and the
    data schema (:data:`observatory.export.shards.SHARD_VERSION`) move
    together, so a client that accepts the manifest has already accepted the
    shard and aggregate schemas.

WHY HASHED NAMES
    Every file but the manifest is immutable: its name contains the first 8 hex
    digits of its own sha256, so the hosting layer can serve it with a
    forever-cache header and a redeploy can never hand a visitor last week's
    shard behind this week's URL.  ``data-manifest.json`` is the only file a
    client must fetch uncached, and it is the only file whose content changes
    under a stable name.  Its own bytes are therefore NOT listed in ``files``.

    The hash sits before the FULL extension -- ``03018.ab12cd34.json.gz``, not
    ``03018.json.ab12cd34.gz`` -- so that content negotiation and static-host
    rules keyed on ``.json.gz`` keep working.

DETERMINISM
    ``generated`` is the only non-reproducible byte in the entire export.  The
    writers (shards, aggregates) stamp no wall clock, so two runs over an
    unchanged warehouse produce identical physical names and identical hashes;
    only the manifest's timestamp differs.  Keep it that way: a hash that moves
    without the data moving turns the immutability guarantee into noise.

WHAT ``finalize`` OWNS
    The export root is the exporter's territory.  ``finalize`` renames every
    freshly written logical file to its hashed name and PRUNES every
    ``.json``/``.json.gz`` file the resulting manifest does not reference -- a
    stale shard is worse than a missing one, because it still renders.

    A full run is therefore an exact snapshot: the caller passes ``written``,
    the logical names it just produced, and whatever is not in that list and
    not carried forward is gone.  A municipality that left the warehouse leaves
    the export with it -- which no amount of looking at the directory could
    have decided, since "not rewritten" and "no longer exists" look identical
    on disk.  ``partial=True`` (what ``--shards-only`` and ``--aggregates-only``
    pass) is the exception: it carries forward the previous manifest's entries
    whose physical files are still there, so re-exporting one half of the
    export does not delete the other.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import shards

__all__ = [
    "HASH_PREFIX_LEN",
    "MANAGED_SUFFIXES",
    "MANIFEST_NAME",
    "MANIFEST_VERSION",
    "document",
    "dump",
    "finalize",
    "hashed_name",
    "load",
    "manifest_path",
    "physical_paths",
    "sha256_bytes",
    "sha256_file",
    "split_suffix",
    "verify_hashes",
]

#: The one mutable file.  Fetched with ``cache: "no-store"`` by the dashboard.
MANIFEST_NAME = "data-manifest.json"

#: One stamp for the manifest AND the data schema.  They move together.
MANIFEST_VERSION = shards.SHARD_VERSION

#: How much of the digest goes into the file name.  32 bits of content address
#: over an export of a few thousand files: a collision would need ~100 000
#: files sharing one logical name, which cannot happen -- names are unique per
#: logical path, so the hash only has to distinguish *versions* of one file.
HASH_PREFIX_LEN = 8

#: Extensions the exporter produces, longest first (``.json.gz`` before
#: ``.json``).  Only these are renamed, hashed and pruned.
MANAGED_SUFFIXES: tuple[str, ...] = (".json.gz", ".json")

#: Filesystem litter that may appear inside a browsed export directory.  Swept
#: with the managed files so it never becomes a gate finding.
JUNK_NAMES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db"})

_HASHED_RE = re.compile(r"\.(?P<hash>[0-9a-f]{%d})$" % HASH_PREFIX_LEN)

#: Read in chunks: a shard is tiny, but this file also hashes whatever the
#: national export grows into.
_CHUNK = 1 << 20


# --------------------------------------------------------------------------
# Names and digests
# --------------------------------------------------------------------------


def split_suffix(name: str) -> tuple[str, str]:
    """``"shards/03018.json.gz" -> ("shards/03018", ".json.gz")``."""
    for suffix in MANAGED_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)], suffix
    stem, dot, ext = name.rpartition(".")
    return (stem, dot + ext) if dot else (name, "")


def hashed_name(logical: str, digest: str) -> str:
    """Physical relative path for ``logical`` at content ``digest``."""
    stem, suffix = split_suffix(logical)
    return f"{stem}.{digest[:HASH_PREFIX_LEN]}{suffix}"


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    """``(hexdigest, size)`` of one file, read from disk."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _is_final_name(relpath: str, digest: str) -> bool:
    """True when ``relpath`` already carries its own content hash.

    Self-consistency, not pattern matching: a file is "already final" only if
    the 8 hex digits in its name are the first 8 of the digest of the bytes
    actually on disk.  That makes :func:`finalize` idempotent and lets it
    recover a directory whose manifest was lost.
    """
    stem, _suffix = split_suffix(relpath)
    match = _HASHED_RE.search(stem)
    return match is not None and match.group("hash") == digest[:HASH_PREFIX_LEN]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def manifest_path(export_root: Path | str) -> Path:
    return Path(export_root) / MANIFEST_NAME


def load(export_root: Path | str) -> dict[str, Any]:
    """The parsed manifest.  Raises if it is missing or not a manifest."""
    path = manifest_path(export_root)
    if not path.is_file():
        raise FileNotFoundError(
            f"missing {MANIFEST_NAME} in {export_root} — the export has not "
            "been finalized (see observatory.export.manifest.finalize)"
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
        raise ValueError(f"{path} is not a manifest: no 'files' object")
    return doc


def physical_paths(doc: dict[str, Any]) -> set[str]:
    """The relative physical paths a manifest document references."""
    return {entry["path"] for entry in doc["files"].values()}


def verify_hashes(export_root: Path | str) -> bool:
    """Recompute the sha256 of every physical file the manifest lists.

    Returns ``False`` on the first defect it can prove (missing file, wrong
    digest, wrong byte count).  The gate wants the *list* of defects and
    computes them itself; this is the cheap boolean the dashboard's tooling and
    the acceptance test ask for.
    """
    root = Path(export_root)
    doc = load(root)
    for logical, entry in doc["files"].items():
        path = root / entry["path"]
        if not path.is_file():
            return False
        digest, size = sha256_file(path)
        if digest != entry["sha256"] or size != int(entry["bytes"]):
            return False
        # ... and the NAME has to be the address too, or the cache-forever
        # promise the hashed name makes is not backed by anything.
        if entry["path"] != hashed_name(logical, digest):
            return False
    return True


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def document(entries: dict[str, dict[str, Any]], generated: str | None = None) -> dict[str, Any]:
    """The manifest document for ``entries`` (``{logical: {path, sha256, bytes}}``)."""
    files = {logical: entries[logical] for logical in sorted(entries)}
    return {
        "v": MANIFEST_VERSION,
        "generated": generated or _now(),
        "files": files,
        "count": len(files),
        "bytes": sum(int(entry["bytes"]) for entry in files.values()),
    }


def dump(export_root: Path | str, doc: dict[str, Any]) -> Path:
    """Write ``doc`` as ``data-manifest.json``.  Returns the path."""
    path = manifest_path(export_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
    )
    return path


def _walk(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def finalize(
    export_root: Path | str,
    *,
    written: Iterable[str] | None = None,
    partial: bool = False,
    generated: str | None = None,
) -> dict[str, Any]:
    """Hash-rename everything under ``export_root`` and write the manifest.

    ``written`` is the logical names this run produced -- the caller knows them
    exactly, and saying so is what lets a full run prune a municipality that
    left the warehouse.  Passing ``None`` falls back to discovery: keep
    everything the previous manifest still resolves plus everything freshly
    written, which is the safe read for an ad-hoc call.

    ``partial=True`` (``--shards-only``, ``--aggregates-only``) keeps the half
    of the export this run did not touch, whatever ``written`` says.

    Idempotent in every mode: a second call renames nothing, prunes nothing and
    rewrites the same manifest apart from ``generated``.
    """
    root = Path(export_root)
    root.mkdir(parents=True, exist_ok=True)

    try:
        previous = {
            logical: {
                "path": entry["path"],
                "sha256": entry["sha256"],
                "bytes": int(entry["bytes"]),
            }
            for logical, entry in load(root)["files"].items()
            if (root / entry["path"]).is_file()
        }
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        previous = {}

    fresh: dict[str, dict[str, Any]] = {}
    for path in _walk(root):
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST_NAME or not rel.endswith(MANAGED_SUFFIXES):
            continue
        digest, size = sha256_file(path)
        if _is_final_name(rel, digest):
            # Already a physical file.  It survives only if an entry below
            # still points at it -- which is how a stale artefact from a
            # previous run gets swept.
            continue
        target_rel = hashed_name(rel, digest)
        os.replace(path, root / target_rel)
        fresh[rel] = {"path": target_rel, "sha256": digest, "bytes": size}

    if partial or written is None:
        keep = set(previous) | set(fresh)
    else:
        # A name the caller declared but did not rewrite is already hashed on
        # disk from an earlier finalize of the same run; anything else is gone.
        keep = set(fresh) | (set(written) & set(previous))

    entries = {
        name: fresh.get(name) or previous[name]
        for name in keep
        if name in fresh or name in previous
    }
    doc = document(entries, generated)
    _prune(root, physical_paths(doc))
    dump(root, doc)
    return doc


def _prune(root: Path, keep: set[str]) -> list[str]:
    """Delete managed files (and filesystem litter) nothing references."""
    removed: list[str] = []
    for path in _walk(root):
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST_NAME or rel in keep:
            continue
        if rel.endswith(MANAGED_SUFFIXES) or path.name in JUNK_NAMES:
            path.unlink()
            removed.append(rel)
    for directory in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    return removed
