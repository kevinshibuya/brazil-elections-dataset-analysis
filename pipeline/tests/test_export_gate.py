import pytest

from observatory.export import manifest, validate
from tests.conftest import physical_files, rewrite_version, tamper_one_vote

pytestmark = pytest.mark.db


def test_tampered_shard_fails_totals_check(exported_rr, test_db):
    tamper_one_vote(exported_rr)                          # helper: +1 to one COO entry
    with pytest.raises(validate.ExportGateError) as e:
        validate.run_gate(exported_rr, test_db)
    assert "totals" in str(e.value)


def test_manifest_covers_every_file_and_hashes_verify(exported_rr, test_db):
    validate.run_gate(exported_rr, test_db)
    m = manifest.load(exported_rr)
    on_disk = physical_files(exported_rr)
    assert {f["path"] for f in m["files"].values()} == on_disk - {"data-manifest.json"}
    assert manifest.verify_hashes(exported_rr)            # sha256 of each physical file


def test_version_mismatch_fails(exported_rr, test_db):
    rewrite_version(exported_rr, "summary.json", v=2)
    with pytest.raises(validate.ExportGateError):
        validate.run_gate(exported_rr, test_db)


def test_gate_reports_all_findings_not_just_first(exported_rr, test_db):
    # The report is the diagnostic -- the gate must never fail on first diff.
    tamper_one_vote(exported_rr)
    rewrite_version(exported_rr, "summary.json", v=2)
    with pytest.raises(validate.ExportGateError) as e:
        validate.run_gate(exported_rr, test_db)
    assert len(e.value.findings) >= 2
