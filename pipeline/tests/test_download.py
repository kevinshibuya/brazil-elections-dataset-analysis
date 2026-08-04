from observatory import sources


def test_rr_subset_includes_national_files():
    ds = sources.datasets("RR")
    kinds = {d.kind for d in ds}
    assert {"votes_section", "detail_section", "rollup_cand_munzona",
            "rollup_party_munzona", "rollup_detail_munzona"} <= kinds
    assert any(d.uf == "RR" for d in ds if d.kind == "votes_section")
    assert all(d.uf is None for d in ds if d.kind == "detail_section")  # national-only file
    assert all(d.url.startswith("https://cdn.tse.jus.br/") for d in ds if d.url is not None)
    assert all(d.resolver == "ckan" for d in ds if d.url is None)       # correspondence resolved at fetch time


def test_idempotent_skip(tmp_path, monkeypatch):
    from observatory import download
    calls = []

    def fake_fetch(url, dest):
        calls.append(url)
        dest.write_bytes(b"zipbytes")

    monkeypatch.setattr(download, "_fetch", fake_fetch)
    monkeypatch.setattr(download, "RAW_DIR", tmp_path)
    download.fetch_all("RR")
    n = len(calls)
    download.fetch_all("RR")
    assert len(calls) == n  # checksummed files not re-fetched
