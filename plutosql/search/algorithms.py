"""Klasik sezgisel arama yöntemleri: Random, Greedy, Simulated Annealing.

Çok kriterli uyarlama: Greedy ve SA tek amaçlı yöntemlerdir; birden çok ağırlık vektörü üzerinde
(artırılmış Tchebycheff skalerleştirmesi) sırayla çalışarak Pareto sınırının farklı bölgelerini hedefler.
Tüm değerlendirmeler arşive girer; sonuç arşivi baskılanmamış geçerli noktalardır.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from plutosql.harness.evaluator import Evaluation

from .base import (
    EvaluatorLike, Scalarizer, SearchAlgorithm, neighbors, random_config, spread_weights,
)


@dataclass
class RandomSearch(SearchAlgorithm):
    """Sıfır hipotezi: 𝒳 üzerinde düzgün örnekleme (kanonik olarak görülmüş noktalar atlanır)."""
    name: str = "random"

    def _search(self, ev: EvaluatorLike, rng: random.Random) -> None:
        space = ev.space
        total = len(space.canonical_configs())
        seen = {tuple(space.canonicalize(ev.evaluate(space.default()).config).values())}
        while len(seen) < total:
            x = space.canonicalize(random_config(space, rng))
            key = tuple(x.values())
            if key in seen:
                continue
            seen.add(key)
            ev.evaluate(x)


@dataclass
class GreedySearch(SearchAlgorithm):
    """Koordinat bazlı açgözlü arama: siteler sırayla, her biri diğerleri sabitken yerel olarak optimize edilir.

    Her ağırlık vektörü için bir geçiş yapılır; bütçe kalırsa sonraki ağırlığa geçilir. `shuffle=True`
    ise site sırası tohuma göre karıştırılır (aksi halde kaynak sırası; yöntem deterministiktir).
    """
    name: str = "greedy"
    n_weights: int = 3
    kind: str = "tchebycheff"
    shuffle: bool = False
    passes: int = 1

    def _search(self, ev: EvaluatorLike, rng: random.Random) -> None:
        space = ev.space
        base_eval = ev.evaluate(space.default())
        base = (base_eval.f1, base_eval.f2)
        for w in spread_weights(self.n_weights):
            score = Scalarizer(base, w, self.kind)
            x, fx = space.default(), score(base_eval)
            for _ in range(self.passes):
                order = list(space.sites)
                if self.shuffle:
                    rng.shuffle(order)
                for site in order:
                    if space.is_absorbed(x, site.id):
                        continue
                    best, fbest = x, fx
                    for a in site.alternatives:
                        if a == x[site.id]:
                            continue
                        y = space.canonicalize({**x, site.id: a})
                        e = ev.evaluate(y)
                        if score(e) < fbest:
                            best, fbest = y, score(e)
                    x, fx = best, fbest


@dataclass
class SimulatedAnnealing(SearchAlgorithm):
    """Ağırlık vektörü başına bir SA zinciri; bütçe zincirler arasında eşit bölünür.

    Komşuluk: tek sitenin alternatifini değiştirmek (kanonik olarak farklı komşular).
    Sıcaklık: normalize amaç biriminde T0'dan T0·t_end_ratio'ya geometrik soğuma, zincirin bütçesine yayılır.
    T0 = 0.1/ln 2: %10'luk bir kötüleşme başlangıçta 0.5 olasılıkla kabul edilir.
    Bütçesiz (canlı) kullanımda zincir başına `steps_without_budget` yeni değerlendirme yapılır.
    """
    name: str = "sa"
    n_weights: int = 3
    kind: str = "tchebycheff"
    t0: float = 0.1 / math.log(2)
    t_end_ratio: float = 0.01
    start: str = "default"                # default | random
    steps_without_budget: int = 20
    max_idle: int = 50                    # bütçe yemeyen (önbellekten dönen) art arda adım sınırı

    def _search(self, ev: EvaluatorLike, rng: random.Random) -> None:
        space = ev.space
        base_eval = ev.evaluate(space.default())
        base = (base_eval.f1, base_eval.f2)
        weights = spread_weights(self.n_weights)
        for k, w in enumerate(weights):
            left = ev.budget_left
            if left is not None and left <= 0:
                return
            steps = self.steps_without_budget if left is None else max(1, left // (len(weights) - k))
            self._chain(ev, rng, Scalarizer(base, w, self.kind), steps, base_eval)

    def _chain(self, ev: EvaluatorLike, rng: random.Random, score: Scalarizer, steps: int,
               base_eval: Evaluation) -> None:
        space = ev.space
        if self.start == "random":
            x = space.canonicalize(random_config(space, rng))
            fx = score(ev.evaluate(x))
        else:
            x, fx = space.default(), score(base_eval)
        alpha = self.t_end_ratio ** (1 / max(1, steps))
        t = self.t0
        used0, idle = ev.budget_used, 0
        while ev.budget_used - used0 < steps and idle < self.max_idle:
            nbrs = neighbors(space, x)
            if not nbrs:
                return
            y = rng.choice(nbrs)
            before = ev.budget_used
            fy = score(ev.evaluate(y))
            idle = idle + 1 if ev.budget_used == before else 0
            delta = fy - fx
            if delta <= 0 or (math.isfinite(delta) and rng.random() < math.exp(-delta / t)):
                x, fx = y, fy
            t *= alpha


ALGORITHMS = {
    "random": RandomSearch,
    "greedy": GreedySearch,
    "sa": SimulatedAnnealing,
}
