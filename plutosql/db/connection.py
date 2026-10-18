"""Veritabanı bağlantı ayarları (.env / ortam değişkenleri) ve bağlantı kurma."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import oracledb

from plutosql.env import ROOT, read_env_file


@dataclass(frozen=True)
class DbConfig:
    user: str
    password: str
    dsn: str

    def __repr__(self) -> str:   # parolayı loglara sızdırma
        return f"DbConfig(user={self.user!r}, dsn={self.dsn!r})"


def load_config(env_file: Path | None = None) -> DbConfig:
    """Ortam değişkenleri önceliklidir; yoksa proje kökündeki .env okunur."""
    file_values = read_env_file(env_file or ROOT / ".env")

    def get(key: str, default: str | None = None) -> str:
        value = os.environ.get(key, file_values.get(key, default))
        if value is None:
            raise RuntimeError(f"{key} tanımlı değil (.env.example dosyasını .env olarak kopyalayın)")
        return value

    return DbConfig(
        user=get("PLUTO_USER", "pluto"),
        password=get("PLUTO_PASSWORD"),
        dsn=get("PLUTO_DSN", "localhost:1522/FREEPDB1"),
    )


def connect(config: DbConfig | None = None) -> oracledb.Connection:
    """Yeni bir oturum açar (python-oracledb thin modu; Oracle istemcisi gerekmez)."""
    cfg = config or load_config()
    return oracledb.connect(user=cfg.user, password=cfg.password, dsn=cfg.dsn)
