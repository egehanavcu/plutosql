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


# ================================================================ uzman kuralı (statik ön bilgi)

# Ders kitabı DBA bilgisi; alternatif adları ve yapı özetinden, ölçüm görmeden çıkarılan sıralı maliyetler.
# CG: satır satır < küçük toplu < büyük toplu < küme tabanlı (azalan çağrı/blok ziyareti); arama SQL > önbellek.
# Bellek: satır satır en ucuz; toplu işlem LIMIT ile büyür; küme tabanlı orta; her önbelleğe alınan tablo bir dizi.
EXPERT_CG = {"ROW": 4, "BULK_50": 3, "BULK_500": 2, "BULK_5000": 1, "SET_BASED": 0,
             "LOOKUP_SQL": 2, "LOOKUP_CACHE": 0}
EXPERT_MEM = {"ROW": 0, "BULK_50": 1, "BULK_500": 2, "BULK_5000": 3, "SET_BASED": 1,
              "LOOKUP_SQL": 0, "LOOKUP_CACHE": 1}


def expert_costs(space, x, cg_table=None, mem_table=None, dedup: bool = True) -> tuple[int, int]:
    """Konfigürasyonun uzman modeline göre öngörülen (CG, bellek) sıralı maliyeti.

    Yutulan siteler katkı vermez. Aynı tabloyu önbelleğe alan siteler belleğe bir kez katkı verir
    (tablo adı LLM'e verilen yapı özetinde de vardır; uzman da onu görür). Tablolar ve bu kural
    duyarlılık analizi için değiştirilebilir.
    """
    cg_table = EXPERT_CG if cg_table is None else cg_table
    mem_table = EXPERT_MEM if mem_table is None else mem_table
    cg = mem = 0
    cached: set[str] = set()
    for s in space.sites:
        if space.is_absorbed(x, s.id):
            continue
        a = x[s.id]
        cg += cg_table.get(a, 0)
        if a == "LOOKUP_CACHE" and dedup:
            table = s.summary().get("table", s.name)
            if table not in cached:
                cached.add(table)
                mem += mem_table[a]
        else:
            mem += mem_table.get(a, 0)
    return cg, mem


def nondominated_ranks(points: list[tuple[float, float]]) -> list[int]:
    """Baskılanmama sıraları (0 = ilk cephe); iki amaç, en küçükleme."""
    unique = list(dict.fromkeys(points))                  # eşit noktalar aynı sırayı alır
    rank_of: dict = {}
    remaining, r = set(unique), 0
    while remaining:
        front = {p for p in remaining
                 if not any(q[0] <= p[0] and q[1] <= p[1] and q != p for q in remaining)}
        for p in front:
            rank_of[p] = r
        remaining -= front
        r += 1
    return [rank_of[p] for p in points]


@dataclass
class ExpertRule(SearchAlgorithm):
    """Geri bildirimsiz uzman tabanı: LLM'in adlardan alabileceği ön bilginin deterministik karşılığı.

    Kanonik uzayın her noktasına `expert_costs` ile öngörülen (CG, bellek) maliyeti verilir; noktalar
    öngörülen baskılanmama sırasına göre (önce öngörülen Pareto cephesi) değerlendirilir. Aynı sıradaki
    noktalar arasında sıra tohuma göre rastgeledir. Ölçümler sırayı değiştirmez: yöntem öğrenmez.
    """
    name: str = "expert"
    cg_table: dict | None = None          # None: EXPERT_CG
    mem_table: dict | None = None         # None: EXPERT_MEM
    dedup: bool = True

    def __post_init__(self):
        self._memo: dict[int, tuple] = {}             # uzay başına (konfigürasyonlar, maliyetler, sıralar)

    def _search(self, ev: EvaluatorLike, rng: random.Random) -> None:
        space = ev.space
        ev.evaluate(space.default())
        if id(space) not in self._memo:
            configs = space.canonical_configs()
            costs = [expert_costs(space, x, self.cg_table, self.mem_table, self.dedup) for x in configs]
            self._memo[id(space)] = (space, configs, costs, nondominated_ranks(costs))
        _, configs, costs, ranks = self._memo[id(space)]
        order = list(range(len(configs)))
        rng.shuffle(order)
        order.sort(key=lambda i: (ranks[i], sum(costs[i])))
        for i in order:
            ev.evaluate(configs[i])


# ================================================================ NSGA-II (ayrık uzay)

@dataclass
class NSGA2(SearchAlgorithm):
    """Ayrık konfigürasyon uzayına uyarlanmış NSGA-II (Deb vd., 2002).

    Birey = site → alternatif eşlemesi (kanonik). Başlangıç: varsayılan kod + rastgele bireyler.
    Ebeveyn seçimi: (sıra, kalabalık mesafesi) üzerinde ikili turnuva. Çaprazlama: site başına düzgün
    (olasılık `crossover`). Mutasyon: site başına 1/K olasılıkla rastgele alternatif. Daha önce
    değerlendirilmiş (kanonik olarak aynı) çocuklar yeniden üretilir; `max_tries` denemede yeni çocuk
    çıkmazsa görülmemiş rastgele bir nokta alınır. Çevresel seçim: ebeveyn ∪ çocuk üzerinde (μ+λ).
    Popülasyon bütçeye göre ölçeklenir: N = clip(B/5, 4, `pop_max`); ~5 nesil garanti edilir.
    """
    name: str = "nsga2"
    pop_max: int = 20
    crossover: float = 0.9
    max_tries: int = 20
    max_generations: int = 1000

    def _search(self, ev: EvaluatorLike, rng: random.Random) -> None:
        space = ev.space
        total = len(space.canonical_configs())
        budget = ev.budget_left
        n = self.pop_max if budget is None else max(4, min(self.pop_max, budget // 5))
        seen: set[tuple] = set()

        def key(x):
            return tuple(space.canonicalize(x).values())

        def unseen_random():
            for _ in range(self.max_tries * 10):
                x = space.canonicalize(random_config(space, rng))
                if key(x) not in seen:
                    return x
            return None

        def evaluate(x):
            seen.add(key(x))
            e = ev.evaluate(x)
            f = e.objectives if e.valid and e.f1 is not None else (float("inf"), float("inf"))
            return (dict(x), f)

        pop = [evaluate(space.default())]
        while len(pop) < n and len(seen) < total:
            x = unseen_random()
            if x is None:
                break
            pop.append(evaluate(x))

        for _ in range(self.max_generations):
            if len(seen) >= total:
                return
            rank, crowd = _rank_crowding([f for _, f in pop])

            def tournament():
                i, j = rng.randrange(len(pop)), rng.randrange(len(pop))
                return pop[min((i, j), key=lambda k: (rank[k], -crowd[k]))][0]

            children = []
            while len(children) < n and len(seen) < total:
                child = None
                for _ in range(self.max_tries):
                    p1, p2 = tournament(), tournament()
                    if rng.random() < self.crossover:
                        y = {s.id: (p1 if rng.random() < 0.5 else p2)[s.id] for s in space.sites}
                    else:
                        y = dict(p1)
                    k = len(space.sites)
                    for s in space.sites:
                        if rng.random() < 1 / k:
                            y[s.id] = rng.choice(s.alternatives)
                    y = space.canonicalize(y)
                    if key(y) not in seen:
                        child = y
                        break
                child = child or unseen_random()
                if child is None:
                    return
                children.append(evaluate(child))
            union = pop + children
            rank, crowd = _rank_crowding([f for _, f in union])
            keep = sorted(range(len(union)), key=lambda i: (rank[i], -crowd[i]))[:n]
            pop = [union[i] for i in keep]


def _rank_crowding(fs: list[tuple[float, float]]) -> tuple[list[int], list[float]]:
    rank = nondominated_ranks(fs)
    crowd = [0.0] * len(fs)
    for r in set(rank):
        idx = [i for i in range(len(fs)) if rank[i] == r]
        for m in range(2):
            idx.sort(key=lambda i: fs[i][m])
            lo, hi = fs[idx[0]][m], fs[idx[-1]][m]
            crowd[idx[0]] = crowd[idx[-1]] = float("inf")
            if not math.isfinite(hi - lo) or hi == lo:
                continue
            for a in range(1, len(idx) - 1):
                crowd[idx[a]] += (fs[idx[a + 1]][m] - fs[idx[a - 1]][m]) / (hi - lo)
    return rank, crowd


ALGORITHMS = {
    "random": RandomSearch,
    "greedy": GreedySearch,
    "sa": SimulatedAnnealing,
    "nsga2": NSGA2,
    "expert": ExpertRule,
}
