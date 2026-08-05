"""Acceptance test for Task 23's two storage levers.

The national run does not fit on this machine unmitigated: the measured RR
footprint extrapolates to ~28.5 GiB against ~31.7 GiB free, and the budget
requires 20 % headroom. Two levers close that, and both are behaviour the
pipeline has to carry rather than something an operator remembers to do:

* a **pre-flight** that refuses to start a run it cannot finish, and
* **deleting each per-UF archive once it is safely ingested**, so the 27 state
  archives cost one file's worth of disk instead of 3.4 GiB.

Pre-authored by the orchestrator. Read-only to the implementer.
"""

from __future__ import annotations

import json

import pytest

from observatory import download, storage


# ---------------------------------------------------------------------------
# pre-flight
# ---------------------------------------------------------------------------


def test_preflight_refuses_when_free_disk_is_short(monkeypatch, tmp_path):
    """A run that cannot finish must not start.

    Refusing costs a second; discovering it at hour three costs the run and
    leaves a half-loaded warehouse behind.
    """
    monkeypatch.setattr(storage, "free_bytes", lambda path: 10 * storage.GIB)
    with pytest.raises(storage.InsufficientDisk) as excinfo:
        storage.preflight(required_bytes=25 * storage.GIB, path=tmp_path)

    message = str(excinfo.value)
    # The operator has to be able to act on this without reading the source:
    # how much is needed, how much there is, and how much is missing.
    assert "10" in message and "25" in message
    assert "GiB" in message


def test_preflight_requires_headroom_above_the_bare_footprint(monkeypatch, tmp_path):
    """Fitting exactly is not fitting: the budget is footprint + 20 %."""
    monkeypatch.setattr(storage, "free_bytes", lambda path: 21 * storage.GIB)
    with pytest.raises(storage.InsufficientDisk):
        storage.preflight(required_bytes=20 * storage.GIB, path=tmp_path)

    monkeypatch.setattr(storage, "free_bytes", lambda path: 25 * storage.GIB)
    storage.preflight(required_bytes=20 * storage.GIB, path=tmp_path)  # 20 % clear


def test_headroom_fraction_is_the_budgeted_twenty_percent():
    assert storage.HEADROOM == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# reclaiming per-UF archives
# ---------------------------------------------------------------------------


def _raw(tmp_path, names: dict[str, bytes]) -> dict[str, str]:
    checksums = {}
    for name, body in names.items():
        (tmp_path / name).write_bytes(body)
        checksums[name] = download._sha256(tmp_path / name)
    (tmp_path / "checksums.json").write_text(json.dumps(checksums, indent=2))
    return checksums


def test_reclaim_removes_only_the_named_uf_archive(tmp_path, monkeypatch):
    """The national archives are read by EVERY state's ingest.

    Deleting `votacao_secao_2022_BR.zip` after the first UF would strand all
    presidential and exterior rows (amendment A2) for the remaining 26 — a
    failure that would surface hours later as a reconciliation gap, not as a
    missing file.
    """
    monkeypatch.setattr(download, "RAW_DIR", tmp_path)
    _raw(
        tmp_path,
        {
            "votacao_secao_2022_RR.zip": b"rr",
            "votacao_secao_2022_SP.zip": b"sp",
            "votacao_secao_2022_BR.zip": b"br",
            "detalhe_votacao_secao_2022.zip": b"detail",
        },
    )

    freed = storage.reclaim_uf_archive("RR")

    assert not (tmp_path / "votacao_secao_2022_RR.zip").exists()
    assert freed == 2  # the bytes it reported are the bytes it removed
    for survivor in (
        "votacao_secao_2022_SP.zip",
        "votacao_secao_2022_BR.zip",
        "detalhe_votacao_secao_2022.zip",
    ):
        assert (tmp_path / survivor).exists(), survivor


def test_reclaimed_archive_is_re_downloadable(tmp_path, monkeypatch):
    """Reclaiming trades disk for bandwidth, so the trade has to be honoured.

    A stale checksum entry for a file that is no longer on disk must not make
    the downloader skip it — that would turn a space saving into a silently
    incomplete re-run.
    """
    monkeypatch.setattr(download, "RAW_DIR", tmp_path)
    _raw(tmp_path, {"votacao_secao_2022_RR.zip": b"rr"})
    storage.reclaim_uf_archive("RR")

    fetched: list[str] = []

    def fake_fetch(url, dest):
        fetched.append(url)
        dest.write_bytes(b"rr")

    monkeypatch.setattr(download, "_fetch", fake_fetch)
    download.fetch_all("RR")

    assert any("votacao_secao_2022_RR.zip" in url for url in fetched)


def test_reclaim_is_idempotent(tmp_path, monkeypatch):
    """Re-running a completed national pass must not fail on an absent file."""
    monkeypatch.setattr(download, "RAW_DIR", tmp_path)
    _raw(tmp_path, {"votacao_secao_2022_RR.zip": b"rr"})
    assert storage.reclaim_uf_archive("RR") == 2
    assert storage.reclaim_uf_archive("RR") == 0
