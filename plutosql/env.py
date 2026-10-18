"""Yerel ayarlar: ortam değişkenleri, yoksa proje kökündeki .env dosyası."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def env_value(key: str, default: str | None = None, env_file: Path | None = None) -> str | None:
    """Ortam değişkeni önceliklidir; boş değerler tanımsız sayılır."""
    value = os.environ.get(key) or read_env_file(env_file or ROOT / ".env").get(key)
    return value if value else default
