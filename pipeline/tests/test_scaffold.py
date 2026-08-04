from pathlib import Path


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from observatory.config import settings
    assert settings.database_url.startswith("postgresql://")
    assert settings.data_dir.name == "data"


def test_raw_dir_gitignored():
    root = Path(__file__).resolve().parents[2]
    gi = (root / ".gitignore").read_text()
    assert "data/raw" in gi and "public/data" in gi
