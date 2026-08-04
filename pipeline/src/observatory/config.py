"""Runtime configuration, read from the environment with sane local defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/elections"

# .../pipeline/src/observatory/config.py -> repo root is four parents up.
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    """Process-wide settings. Instantiate via `Settings.from_env()`."""

    database_url: str
    data_dir: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL,
            data_dir=Path(os.environ.get("DATA_DIR") or (REPO_ROOT / "data")).resolve(),
        )


settings = Settings.from_env()
