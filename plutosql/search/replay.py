"""Ground truth deposundan yanıt veren değerlendirici.

Exhaustive koşusuyla tüm kanonik konfigürasyonlar ölçüldükten sonra, arama algoritmaları farklı bütçe
oranları ve tohumlarla saniyeler içinde karşılaştırılabilir. Bütçe sayımı canlı `Evaluator` ile aynıdır
(benzersiz kod; ADR-003). Ölçüm tekrarlanmadığı için gürültü ölçüm anında değil, ground truth'un medyanı
düzeyinde sabittir — bu, geçerlilik tehditleri bölümünde belirtilmelidir.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from plutosql.harness.evaluator import BudgetExhausted, Evaluation, evaluation_from_record
from plutosql.transformer.space import SearchSpace


class MissingMeasurement(KeyError):
    pass


class ReplayEvaluator:
    def __init__(self, space: SearchSpace, records: Mapping[tuple, dict], budget: int | None = None):
        self.space = space
        self.budget = budget
        self.history: list[Evaluation] = []
        self._records = records
        self._requested: set[str] = set()

    @classmethod
    def from_results(cls, directory: Path, budget: int | None = None) -> "ReplayEvaluator":
        """`exhaustive` çıktısı dizini: measurements.jsonl + space.json."""
        directory = Path(directory)
        space = SearchSpace.from_description(json.loads((directory / "space.json").read_text(encoding="utf-8")))
        records = load_records(directory / "measurements.jsonl", space)
        return cls(space, records, budget)

    def fresh(self, budget: int | None) -> "ReplayEvaluator":
        """Aynı veriyle yeni bir koşu (boş geçmiş, yeni bütçe)."""
        return ReplayEvaluator(self.space, self._records, budget)

    @property
    def records(self) -> list[dict]:
        return list(self._records.values())

    @property
    def budget_used(self) -> int:
        return len(self._requested)

    @property
    def budget_left(self) -> int | None:
        return None if self.budget is None else self.budget - self.budget_used

    def evaluate(self, x: Mapping[str, str]) -> Evaluation:
        config = self.space.canonicalize(x)
        rec = self._records.get(tuple(config.values()))
        if rec is None:
            raise MissingMeasurement(config)
        if rec["digest"] not in self._requested:
            if self.budget is not None and self.budget_used >= self.budget:
                raise BudgetExhausted(f"bütçe doldu ({self.budget})")
            self._requested.add(rec["digest"])
        ev = evaluation_from_record({**rec, "config": config}, measured_now=False)
        self.history.append(ev)
        return ev


def load_records(path: Path, space: SearchSpace) -> dict[tuple, dict]:
    records = {}
    with Path(path).open(encoding="utf-8") as f:
        next(f)                                           # bağlam satırı
        for line in f:
            if line.strip():
                rec = json.loads(line)
                records[tuple(space.canonicalize(rec["config"]).values())] = rec
    missing = len(space.canonical_configs()) - len(records)
    if missing:
        raise MissingMeasurement(f"{missing} kanonik konfigürasyonun ölçümü yok (önce exhaustive)")
    return records
