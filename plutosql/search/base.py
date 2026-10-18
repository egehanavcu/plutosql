"""Arama algoritmalarının ortak altyapısı.

Her algoritma bir değerlendirici (canlı `Evaluator` ya da `ReplayEvaluator`) üzerinde çalışır ve bütçe
bitene (`BudgetExhausted`) ya da kendi durma koşuluna kadar konfigürasyon ister. Sonuç, istek sırasıyla
değerlendirme geçmişi ve bunun geçerli noktalarından oluşan baskılanmamış arşivdir.

Adalet kuralı: tüm algoritmalar önce varsayılan (orijinal) kodu değerlendirir. Gerçek kullanımda orijinal
kod her zaman ölçülür; ayrıca skalerleştirmede normalizasyon tabanı olarak kullanılır. Bütçeye dahildir.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from plutosql.analysis.pareto import pareto_front
from plutosql.harness.evaluator import BudgetExhausted, Evaluation
from plutosql.transformer.space import Config, SearchSpace


class EvaluatorLike(Protocol):
    space: SearchSpace
    history: list[Evaluation]

    @property
    def budget_used(self) -> int: ...

    @property
    def budget_left(self) -> int | None: ...

    def evaluate(self, x: Mapping[str, str]) -> Evaluation: ...


@dataclass
class SearchResult:
    algorithm: str
    seed: int
    budget: int | None
    history: list[Evaluation]
    archive: list[Evaluation] = field(default_factory=list)
    meta: dict = field(default_factory=dict)          # algoritmaya özgü iz (ör. LLM kararları)

    @classmethod
    def from_history(cls, algorithm: str, seed: int, budget: int | None,
                     history: Sequence[Evaluation]) -> "SearchResult":
        return cls(algorithm, seed, budget, list(history), archive_of(history))


def archive_of(evaluations: Sequence[Evaluation]) -> list[Evaluation]:
    """Geçerli değerlendirmelerin baskılanmamış kümesi (aynı kod bir kez)."""
    unique = {e.digest: e for e in evaluations if e.valid and e.f1 is not None}
    return pareto_front(list(unique.values()), key=lambda e: e.objectives)


class SearchAlgorithm:
    name: str = "base"

    def run(self, evaluator: EvaluatorLike, seed: int) -> SearchResult:
        rng = random.Random(seed)
        self.meta: dict = {}
        self.seed = seed
        try:
            self._search(evaluator, rng)
        except BudgetExhausted:
            pass
        result = SearchResult.from_history(self.name, seed, getattr(evaluator, "budget", None), evaluator.history)
        result.meta = self.meta
        return result

    def _search(self, evaluator: EvaluatorLike, rng: random.Random) -> None:
        raise NotImplementedError


# ================================================================ skalerleştirme

@dataclass(frozen=True)
class Scalarizer:
    """Varsayılan koda göre normalize edilmiş amaçların skalerleştirilmesi.

    weighted:    w1·n1 + w2·n2
    tchebycheff: max(w1·n1, w2·n2) + ρ·(n1 + n2)   (artırılmış; konveks olmayan sınır noktalarını da bulur)
    n_i = f_i / f_i(varsayılan). Geçersiz değerlendirme +∞ alır.
    """
    base: tuple[float, float]
    weights: tuple[float, float]
    kind: str = "tchebycheff"
    augmentation: float = 1e-3

    def __call__(self, e: Evaluation) -> float:
        if not e.valid or e.f1 is None:
            return float("inf")
        n1 = e.f1 / self.base[0] if self.base[0] else e.f1
        n2 = e.f2 / self.base[1] if self.base[1] else e.f2
        w1, w2 = self.weights
        if self.kind == "weighted":
            return w1 * n1 + w2 * n2
        return max(w1 * n1, w2 * n2) + self.augmentation * (n1 + n2)


def spread_weights(k: int) -> list[tuple[float, float]]:
    """[0,1] aralığına eşit yayılmış k ağırlık vektörü; ortadan başlayıp uçlara doğru sıralı."""
    if k == 1:
        return [(0.5, 0.5)]
    ws = [(i / (k - 1), 1 - i / (k - 1)) for i in range(k)]
    return sorted(ws, key=lambda w: abs(w[0] - 0.5))


def random_config(space: SearchSpace, rng: random.Random) -> Config:
    return {s.id: rng.choice(s.alternatives) for s in space.sites}


def neighbors(space: SearchSpace, x: Mapping[str, str]) -> list[Config]:
    """Tek bir sitenin alternatifini değiştiren, kanonik olarak farklı komşular."""
    base = space.canonicalize(x)
    out, seen = [], {tuple(base.values())}
    for s in space.sites:
        for a in s.alternatives:
            if a == x[s.id]:
                continue
            y = space.canonicalize({**x, s.id: a})
            key = tuple(y.values())
            if key not in seen:
                seen.add(key)
                out.append(y)
    return out
