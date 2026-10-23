"""Tüm iş yüklerindeki arama koşularını tek, yeniden üretilebilir bir veri kümesinde toplar.

Kaynaklar yalnızca saklanan verilerdir:
  - ground truth (measurements.jsonl + space.json),
  - LLM yanıt önbelleği (llm_cache/<model>.jsonl).
Klasik algoritmalar tohumlu olduğundan yeniden koşulur; LLM koşuları API'ye gitmeden önbellekten yeniden
oynatılır (önbellekte olmayan bir çağrı gerekirse o iş yükü/koşul atlanır ve raporlanır). Böylece tezdeki
her sayı `python -m plutosql.analysis collect` ile aynı biçimde yeniden üretilir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from plutosql.analysis.metrics import noise_epsilon
from plutosql.search.algorithms import ALGORITHMS
from plutosql.search.experiment import GroundTruth, run_grid
from plutosql.search.llm import CachedClient, LLMPolicy
from plutosql.search.replay import ReplayEvaluator

RHOS = (0.1, 0.2, 0.3, 0.4)
LLM_RHOS = (0.1,)                       # kütüphane kapsamı (ρ=0.2 ayrıca karar verilecek)
CLASSICAL_SEEDS = 30
LLM_SEEDS = 10
MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class Condition:
    """Bir karşılaştırma koşulu: algoritma + ayarları + ölçüldüğü bütçe/tohum ızgarası."""
    name: str
    kind: str                                   # classical | llm
    rhos: tuple[float, ...]
    seeds: int
    params: dict = field(default_factory=dict)

    def algorithm(self, client=None):
        if self.kind == "classical":
            return ALGORITHMS[self.name]()
        return LLMPolicy(client=client, **self.params)


CONDITIONS = (
    Condition("random", "classical", RHOS, CLASSICAL_SEEDS),
    Condition("greedy", "classical", RHOS, CLASSICAL_SEEDS),
    Condition("sa", "classical", RHOS, CLASSICAL_SEEDS),
    Condition("nsga2", "classical", RHOS, CLASSICAL_SEEDS),
    Condition("expert", "classical", RHOS, CLASSICAL_SEEDS),
    Condition("llm_named", "llm", LLM_RHOS, LLM_SEEDS, {"history": "full", "proposals": 5, "project": True}),
    Condition("llm_opaque", "llm", LLM_RHOS, LLM_SEEDS,
              {"history": "full", "proposals": 5, "project": True, "opaque_labels": True}),
    # geri bildirimsiz ablation: adlar + ipuçları aynı, ölçüm sonuçları istemde yok
    Condition("llm_blind", "llm", LLM_RHOS, LLM_SEEDS, {"history": "blind", "proposals": 5, "project": True}),
)


class CacheMiss(Exception):
    pass


class _CacheOnly:
    """Önbellekte olmayan bir çağrı istenirse durur (yeniden oynatma API'ye gitmez)."""
    model = MODEL
    settings = {"thinking_budget": None, "thinking_level": "LOW"}     # kütüphane LLM deneylerinin ayarı

    def complete(self, *a, **k):
        raise CacheMiss("LLM önbelleğinde yok")


def ground_truth_dirs(root: Path = Path("results")) -> dict[str, Path]:
    out = {}
    for space_file in sorted(root.glob("*/*/space.json")):
        out[space_file.parent.parent.name] = space_file.parent
    return out


def collect(root: Path = Path("results"), conditions=CONDITIONS, log=print) -> list[dict]:
    rows = []
    for workload, d in ground_truth_dirs(root).items():
        replay = ReplayEvaluator.from_results(d)
        gt = GroundTruth.from_records(replay.records, noise_epsilon(replay.records))
        for cond in conditions:
            client = None
            if cond.kind == "llm":
                cache = d / "llm_cache" / f"{MODEL}.jsonl"
                if not cache.exists():
                    log(f"  {workload}/{cond.name}: LLM önbelleği yok, atlandı")
                    continue
                client = CachedClient(_CacheOnly(), cache)
            try:
                runs = run_grid({cond.name: cond.algorithm(client)}, replay, gt, cond.rhos, range(cond.seeds))
            except CacheMiss:
                log(f"  {workload}/{cond.name}: önbellek eksik (deney tamamlanmamış), atlandı")
                continue
            for r in runs:
                meta = {k: v for k, v in r.meta.items() if k not in ("decisions", "label_map")}
                rows.append({
                    "workload": workload, "condition": cond.name, "kind": cond.kind,
                    "rho": r.rho, "budget": r.budget, "seed": r.seed, "evaluations": r.evaluations,
                    "hv_ratio": r.hv_ratio, "igd_plus": r.igd_plus, "eps_additive": r.eps_additive,
                    "eps_coverage": r.eps_coverage, "front_found_at": r.front_found_at,
                    "front_size": len(gt.front), "canonical_size": len(replay.space.canonical_configs()),
                    "space_size": replay.space.size, "hv_curve": r.hv_curve, "meta": meta,
                })
        log(f"{workload}: {sum(1 for r in rows if r['workload'] == workload)} koşu")
    return rows


def save(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
