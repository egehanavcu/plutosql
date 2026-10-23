"""Ölçüm deposu: üretilen kodun özeti (digest) → ölçüm kaydı (JSON Lines).

İlk satır bağlamdır (iş yükü, veri durumu, protokol, veritabanı sürümü). Aynı dosyaya farklı bağlamla
yazılmaya çalışılırsa hata verilir: farklı koşullarda alınmış ölçümler karışmaz. Her ölçüm bir satırdır;
dosya yalnızca sona eklenir, yarıda kalan bir koşu kaldığı yerden devam eder.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class ContextMismatch(Exception):
    pass


class MeasurementStore:
    def __init__(self, path: Path, context: dict):
        self.path = Path(path)
        self.context = json.loads(json.dumps(context, sort_keys=True, default=str))
        self.records: dict[str, dict] = {}
        if self.path.exists() and self.path.stat().st_size > 0:
            self._load()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._append({"context": self.context})

    def _load(self) -> None:
        with self.path.open(encoding="utf-8") as f:
            header = json.loads(f.readline())
            if header.get("context") != self.context:
                raise ContextMismatch(
                    f"{self.path}: kayıtlı bağlam farklı.\n  kayıtlı: {header.get('context')}\n  istenen: {self.context}")
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    self.records[rec["digest"]] = rec

    def _append(self, obj: dict, attempts: int = 8) -> None:
        line = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        delay = 0.25
        for attempt in range(1, attempts + 1):
            try:
                with self.path.open("a", encoding="utf-8", newline="\n") as f:
                    f.write(line)
                return
            except PermissionError:
                # Windows: dosya başka bir süreç (editör, antivirüs, git) tarafından anlık kilitli olabilir.
                if attempt == attempts:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 8.0)

    def get(self, digest: str) -> dict | None:
        return self.records.get(digest)

    def put(self, record: dict) -> None:
        self.records[record["digest"]] = record
        self._append(record)

    def __len__(self) -> int:
        return len(self.records)
