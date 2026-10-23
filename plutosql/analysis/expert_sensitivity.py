"""Uzman kuralının maliyet tablosuna duyarlılığı.

Uzman tablosu (EXPERT_CG / EXPERT_MEM) sonuçlar bilindikten sonra yazıldı. Bu, tablonun sonuçlara göre
seçilmiş olma riskini taşır. Burada tablonun her bileşeni önceden makul sayılabilecek alternatiflerle
değiştirilir (tam ızgara) ve her varyant aynı protokolle (ρ, tohumlar) koşulur. Sorular:
  - LLM (adlar) ile uzman kuralı arasındaki fark varyanta göre ne kadar değişiyor?
  - Asıl tablo varyantlar arasında nerede duruyor (en iyi uçta mı, ortada mı)?
LLM koşuları yeniden koşulmaz; collect çıktısındaki (runs.jsonl) satırlar kullanılır.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass
from pathlib import Path

from plutosql.analysis.collect import CLASSICAL_SEEDS, ground_truth_dirs
from plutosql.analysis.metrics import noise_epsilon
from plutosql.analysis.stats import friedman, pairwise
from plutosql.search.algorithms import EXPERT_CG, EXPERT_MEM, ExpertRule
from plutosql.search.experiment import GroundTruth, run_grid
from plutosql.search.replay import ReplayEvaluator

# Bileşen başına seçenekler; ilk seçenek asıl tablodaki değerdir.
FACTORS = {
    "bulk_cg": {"sıralı": {"BULK_50": 3, "BULK_500": 2, "BULK_5000": 1},       # LIMIT büyüdükçe CG azalır
                "düz": {"BULK_50": 2, "BULK_500": 2, "BULK_5000": 2}},         # LIMIT önemsiz
    "set_cg": {"en iyi": {"SET_BASED": 0},
               "toplu kadar": {"SET_BASED": 1}},
    "lookup_cg": {"2": {"LOOKUP_SQL": 2}, "1": {"LOOKUP_SQL": 1}, "4": {"LOOKUP_SQL": 4}},
    "set_mem": {"1": {"SET_BASED": 1}, "0": {"SET_BASED": 0}, "3": {"SET_BASED": 3}},
    "cache_mem": {"1": {"LOOKUP_CACHE": 1}, "3": {"LOOKUP_CACHE": 3}},
    "bulk_mem": {"sıralı": {"BULK_50": 1, "BULK_500": 2, "BULK_5000": 3},
                 "düz": {"BULK_50": 2, "BULK_500": 2, "BULK_5000": 2}},
    "dedup": {"evet": True, "hayır": False},
}
CG_FACTORS = ("bulk_cg", "set_cg", "lookup_cg")
MEM_FACTORS = ("set_mem", "cache_mem", "bulk_mem")
OTHERS = ["llm_named", "llm_opaque", "random", "nsga2", "sa", "greedy"]


@dataclass(frozen=True)
class Variant:
    choice: tuple[tuple[str, str], ...]          # (bileşen, seçenek)

    @property
    def name(self) -> str:
        return "expert[" + ",".join(f"{k}={v}" for k, v in self.choice) + "]"

    @property
    def is_original(self) -> bool:
        return all(v == next(iter(FACTORS[k])) for k, v in self.choice)

    def algorithm(self) -> ExpertRule:
        c = dict(self.choice)
        cg, mem = dict(EXPERT_CG), dict(EXPERT_MEM)
        for k in CG_FACTORS:
            cg.update(FACTORS[k][c[k]])
        for k in MEM_FACTORS:
            mem.update(FACTORS[k][c[k]])
        return ExpertRule(name=self.name, cg_table=cg, mem_table=mem, dedup=FACTORS["dedup"][c["dedup"]])


def variants() -> list[Variant]:
    keys = list(FACTORS)
    return [Variant(tuple(zip(keys, combo))) for combo in itertools.product(*(FACTORS[k] for k in keys))]


def run_variants(rho: float = 0.1, seeds: int = CLASSICAL_SEEDS, root: Path = Path("results"),
                 log=print) -> list[dict]:
    """Her varyantı her iş yükünde koşar; collect satırlarıyla aynı biçimde (yalnız gerekli alanlar)."""
    vs = variants()
    algos = {v.name: v.algorithm() for v in vs}
    rows = []
    for workload, d in ground_truth_dirs(root).items():
        replay = ReplayEvaluator.from_results(d)
        gt = GroundTruth.from_records(replay.records, noise_epsilon(replay.records))
        for r in run_grid(algos, replay, gt, [rho], range(seeds)):
            rows.append({"workload": workload, "condition": r.algorithm, "rho": r.rho,
                         "igd_plus": r.igd_plus, "hv_ratio": r.hv_ratio})
        log(f"  {workload}: {len(vs)} varyant × {seeds} tohum")
    return rows


def evaluate(variant_rows: list[dict], base_rows: list[dict], rho: float = 0.1) -> list[dict]:
    """Varyant başına: LLM (adlar)'a karşı çift karşılaştırma + uzmanın yerine konduğu Friedman sırası."""
    base = [r for r in base_rows if r["rho"] == rho and r["condition"] in OTHERS]
    out = []
    for v in variants():
        mine = [r for r in variant_rows if r["condition"] == v.name]
        rows = base + mine
        pw = pairwise(rows, "llm_named", v.name)
        pr = pairwise(rows, v.name, "random")
        fr = friedman(rows, rho, ["llm_named", v.name] + OTHERS[1:])
        gap = fr.mean_ranks[v.name] - fr.mean_ranks["llm_named"]
        out.append({
            "varyant": v.name, "asıl": v.is_original, **dict(v.choice),
            "uzman_ort_IGD+": statistics.fmean(r["igd_plus"] for r in mine),
            "uzman_sıra": fr.mean_ranks[v.name], "llm_sıra": fr.mean_ranks["llm_named"],
            "sıra_farkı": gap, "CD": fr.critical_difference, "farkı_anlamlı": gap > fr.critical_difference,
            "llm_iyi": sum(r.p_holm < 0.05 and r.a12 > 0.5 for r in pw),
            "llm_kötü": sum(r.p_holm < 0.05 and r.a12 < 0.5 for r in pw),
            "llm_A12_medyan": statistics.median(r.a12 for r in pw),
            "uzman_random_iyi": sum(r.p_holm < 0.05 and r.a12 > 0.5 for r in pr),
            "uzman_klasik_en_iyi": all(fr.mean_ranks[v.name] < fr.mean_ranks[c] for c in OTHERS[2:]),
        })
    return out
