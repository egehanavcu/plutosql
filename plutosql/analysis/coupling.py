"""H2: siteler arası kuplajın ölçülmesi.

Toplamsal (etkileşimsiz) model: y(x) = β0 + Σ_i β_i[x_i]   (her site için referans kodlaması; varsayılan alternatif = 0)
Soğurulan siteler kanonik olarak varsayılan değeri aldığından ebeveyn SET_BASED etkisi onları da kapsar.

- R²_add: toplamsal modelin açıkladığı varyans. 1 − R²_add = etkileşim (kuplaj) payı.
- Gürültü payı: denemeler arası varyansın medyana yansıyan kısmı / toplam varyans. Etkileşim payı gürültü
  payından belirgin büyükse kuplaj ölçüm hatasıyla açıklanamaz.
- Çift kazancı: (i, j) çifti için etkileşim terimleri eklenince R²'deki artış → hangi siteler kuplajlı.
- Ölçek: ham ve log. Log ölçekte toplamsal = çarpımsal bağımsız etkiler; ham ölçekte kuplaj görünüp log'da
  kayboluyorsa etkiler çarpımsaldır, gerçek bir etkileşim değildir.
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass

import numpy as np

from plutosql.transformer.space import SearchSpace

OBJECTIVES = {"f1": ("f1", "consistent_gets"), "f2": ("f2", "delta_pga")}


@dataclass(frozen=True)
class AdditivityReport:
    objective: str
    scale: str
    n: int
    r2_additive: float
    interaction_share: float
    noise_share: float
    pair_gains: dict[tuple[str, str], float]


def _design(space: SearchSpace, configs: list[dict], pair: tuple[str, str] | None = None) -> np.ndarray:
    cols = [np.ones(len(configs))]
    levels = {s.id: [a for a in s.alternatives if a != s.default] for s in space.sites}
    for s in space.sites:
        for a in levels[s.id]:
            cols.append(np.array([1.0 if c[s.id] == a else 0.0 for c in configs]))
    if pair is not None:
        i, j = pair
        for a, b in itertools.product(levels[i], levels[j]):
            cols.append(np.array([1.0 if c[i] == a and c[j] == b else 0.0 for c in configs]))
    return np.column_stack(cols)


def _r2(x: np.ndarray, y: np.ndarray) -> float:
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    sse = float(np.sum((y - x @ beta) ** 2))
    sst = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - sse / sst if sst > 0 else 1.0


def additivity(space: SearchSpace, records: list[dict], objective: str = "f1",
               scale: str = "log") -> AdditivityReport:
    key, trial_key = OBJECTIVES[objective]
    rows = [r for r in records if r["valid"] and r[key] is not None]
    configs = [space.canonicalize(r["config"]) for r in rows]
    transform = (lambda v: math.log(max(v, 1.0))) if scale == "log" else float
    y = np.array([transform(r[key]) for r in rows])

    base = _design(space, configs)
    r2_add = _r2(base, y)
    gains = {}
    for a, b in itertools.combinations([s.id for s in space.sites], 2):
        gains[(a, b)] = _r2(_design(space, configs, (a, b)), y) - r2_add

    # Medyanın gürültüsü ≈ denemeler arası varyans / deneme sayısı (kaba ama muhafazakâr olmayan bir alt sınır)
    noise = []
    for r in rows:
        t = [transform(tr[trial_key]) for tr in r.get("trials", [])]
        if len(t) >= 2:
            noise.append(statistics.pvariance(t) / len(t))
    var_y = float(np.var(y))
    noise_share = (statistics.fmean(noise) / var_y) if noise and var_y > 0 else 0.0

    return AdditivityReport(objective, scale, len(rows), r2_add, 1 - r2_add, noise_share, gains)


def format_report(rep: AdditivityReport, top: int = 3) -> str:
    pairs = sorted(rep.pair_gains.items(), key=lambda kv: -kv[1])[:top]
    pair_txt = ", ".join(f"{a}×{b}: {g:.3f}" for (a, b), g in pairs)
    return (f"{rep.objective} ({rep.scale:<3}) n={rep.n:<4} R²_toplamsal={rep.r2_additive:.3f}  "
            f"etkileşim payı={rep.interaction_share:.3f}  gürültü payı={rep.noise_share:.4f}  "
            f"en kuplajlı çiftler: {pair_txt}")
