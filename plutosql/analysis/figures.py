"""Tez grafikleri (statik: PDF + PNG) ve her grafiğin veri tablosu (CSV).

Kaynaklar yalnızca saklanan verilerdir: results/summary/runs.jsonl (collect çıktısı) ve ground truth
dizinleri. `python -m plutosql.analysis figures` hepsini results/figures/ altına yeniden üretir.

Renk ve işaret kuralları (dataviz yöntemi, doğrulanmış varsayılan palet):
- Koşullar sabit sırayla renklenir (mavi, turuncu, aqua, sarı, magenta, yeşil, mor, kırmızı) ve her koşul ayrıca bir
  işaret şekli taşır: gri tonlu baskıda ve renk körlüğünde kimlik renge bağlı kalmaz.
- Aqua/sarı/magenta açık zeminde 3:1 altındadır; bu yüzden ≥2 seride lejant, gerektiğinde doğrudan etiket
  ve her grafiğe CSV tablo eşlik eder.
- Tek eksen, ince işaretler, saç teli ızgara; değer etiketleri metin mürekkebinde, seri renginde değil.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plutosql.analysis.collect import ground_truth_dirs, load  # noqa: E402
from plutosql.analysis.coupling import additivity  # noqa: E402
from plutosql.analysis.pareto import pareto_front  # noqa: E402
from plutosql.analysis.stats import friedman, pairwise  # noqa: E402
from plutosql.search.replay import load_records  # noqa: E402
from plutosql.transformer.space import SearchSpace  # noqa: E402

OUT = Path("results/figures")

# ---------------------------------------------------------------- palet (açık mod, basılı tez)
SURFACE = "#fcfcfb"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

CONDITIONS = ["llm_named", "llm_opaque", "random", "sa", "greedy", "nsga2", "expert", "llm_blind"]
LABEL = {"llm_named": "LLM (adlar)", "llm_opaque": "LLM (opak)", "random": "Random", "sa": "SA", "greedy": "Greedy",
         "nsga2": "NSGA-II", "expert": "Uzman kuralı",
         "llm_blind": "LLM (geri bildirimsiz)"}
COLOR = {c: SLOTS[i] for i, c in enumerate(CONDITIONS)}          # varlığa bağlı, sıraya değil
MARKER = {"llm_named": "o", "llm_opaque": "s", "random": "^", "sa": "D", "greedy": "v", "nsga2": "P", "expert": "X", "llm_blind": "h"}

GROUP = {
    **{w: "ortak önbellek" for w in ("g02_shared_dense", "g03_shared_sparse", "g04_shared_lowsel",
                                      "g05_shared_twopairs", "g09_scale_60k")},
    **{w: "el yazımı" for w in ("h01_invoicing", "h02_inventory", "h03_interest", "h04_loyalty")},
}
GROUP_ORDER = ["ortak önbellek", "el yazımı", "kontrol"]
GROUP_COLOR = {g: SLOTS[i] for i, g in enumerate(GROUP_ORDER)}


def group_of(workload: str) -> str:
    return GROUP.get(workload, "kontrol")


def _style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8.5,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 9, "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "grid.linestyle": "-",
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
        "legend.frameon": False, "legend.fontsize": 8, "lines.linewidth": 1.5, "lines.markersize": 5,
    })


def _save(fig, name: str, table: list[dict]) -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in ("pdf", "png"):
        p = OUT / f"{name}.{ext}"
        fig.savefig(p, dpi=200, bbox_inches="tight")
        paths.append(p)
    plt.close(fig)
    if table:
        p = OUT / f"{name}.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0]))
            w.writeheader()
            w.writerows(table)
        paths.append(p)
    return paths


def _short(w: str) -> str:
    return w.replace("_", " ")


# ================================================================ 1) sıra diyagramı (Friedman + Nemenyi)

def fig_ranks(rows: list[dict], rho: float = 0.1) -> list[Path]:
    panels = []
    for metric, lower, title in (("igd_plus", True, "IGD+"), ("hv_ratio", False, "HV oranı")):
        fr = friedman(rows, rho, CONDITIONS, metric, lower)
        panels.append((title, fr))
    fig, axes = plt.subplots(len(panels), 1, figsize=(6.3, 1.9 * len(panels)), constrained_layout=True)
    table = []
    for ax, (title, fr) in zip(axes, panels):
        ranks = sorted(fr.mean_ranks.items(), key=lambda kv: kv[1])
        k = len(ranks)
        ax.set_xlim(0.7, k + 0.3)
        ax.set_ylim(-1.95, 1.25)
        ax.grid(False)
        ax.spines[["left", "bottom"]].set_visible(False)
        ax.set_yticks([])
        ax.set_xticks(range(1, k + 1))
        ax.axhline(0, color=AXIS, lw=0.8, zorder=1)
        for i, (c, r) in enumerate(ranks):
            ax.scatter([r], [0], s=46, color=COLOR[c], marker=MARKER[c], zorder=3,
                       edgecolors=SURFACE, linewidths=1.2)
            dy = 0.38 if i % 2 == 0 else -0.32
            ax.annotate(f"{LABEL[c]}\n{r:.2f}", (r, 0), xytext=(r, dy), ha="center",
                        va="bottom" if dy > 0 else "top", fontsize=7.5, color=INK,
                        arrowprops=dict(arrowstyle="-", color=AXIS, lw=0.6))
            table.append({"metrik": title, "koşul": LABEL[c], "ortalama_sıra": round(r, 3),
                          "chi2": round(fr.statistic, 2), "p": f"{fr.p:.2e}", "CD": round(fr.critical_difference, 3),
                          "iş_yükü": len(fr.workloads)})
        # Nemenyi: kritik farktan küçük sıra aralıkları anlamlı biçimde ayrışmaz → altta gri çubuk
        cliques = []
        for i in range(k):
            j = max(j for j in range(i, k) if ranks[j][1] - ranks[i][1] <= fr.critical_difference)
            if j > i and not any(a <= i and j <= b for a, b in cliques):
                cliques.append((i, j))
        for n, (i, j) in enumerate(cliques):
            y = -1.3 - 0.17 * n
            ax.plot([ranks[i][1] - 0.04, ranks[j][1] + 0.04], [y, y], color=INK2, lw=2.2, solid_capstyle="round")
        cd = fr.critical_difference
        ax.plot([0.75, 0.75 + cd], [1.12, 1.12], color=INK2, lw=1.2)          # ölçek: sol üst
        ax.text(0.8 + cd, 1.12, f"CD = {cd:.2f}", ha="left", va="center", fontsize=7.5, color=INK2)
        ax.set_title(f"{title} — ρ = {rho}, N = {len(fr.workloads)} iş yükü, Friedman p = {fr.p:.1e}",
                     loc="left", fontsize=8.5)
    axes[-1].set_xlabel("ortalama sıra (1 = en iyi); gri çubuk: Nemenyi'ye göre anlamlı fark yok")
    return _save(fig, "fig1_ranks", table)


# ================================================================ 2) A12 nokta grafiği

COMPARISONS = [("llm_named", "random"), ("llm_named", "nsga2"), ("llm_named", "expert"), ("llm_named", "llm_blind"),
               ("llm_blind", "expert"), ("llm_opaque", "random")]


def fig_a12(rows: list[dict], rho: float = 0.1) -> list[Path]:
    results = {cmp: {r.workload: r for r in pairwise(rows, *cmp) if r.rho == rho} for cmp in COMPARISONS}
    workloads = sorted(results[COMPARISONS[0]], key=lambda w: (GROUP_ORDER.index(group_of(w)), w))
    fig, axes = plt.subplots(1, len(COMPARISONS), figsize=(9.4, 3.8), sharey=True, constrained_layout=True)
    table = []
    for ax, cmp in zip(axes, COMPARISONS):
        a, b = cmp
        ax.axvline(0.5, color=AXIS, lw=0.8, zorder=1)
        for y, w in enumerate(workloads):
            r = results[cmp][w]
            sig = r.p_holm < 0.05
            ax.scatter([r.a12], [y], s=30, marker=MARKER[a], zorder=3, linewidths=1.2,
                       color=COLOR[a] if sig else SURFACE, edgecolors=COLOR[a])
            table.append({"karşılaştırma": f"{LABEL[a]} vs {LABEL[b]}", "iş_yükü": w, "grup": group_of(w),
                          "A12": round(r.a12, 3), "p_holm": f"{r.p_holm:.3g}", "anlamlı": sig,
                          "medyan_IGD+_ilk": f"{r.median_a:.4g}", "medyan_IGD+_ikinci": f"{r.median_b:.4g}"})
        n_sig = sum(results[cmp][w].p_holm < 0.05 and results[cmp][w].a12 > 0.5 for w in workloads)
        n_worse = sum(results[cmp][w].p_holm < 0.05 and results[cmp][w].a12 < 0.5 for w in workloads)
        ax.set_title(f"{LABEL[a]}\nvs {LABEL[b]}\nanlamlı: {n_sig} iyi, {n_worse} kötü", fontsize=8)
        ax.set_xlim(-0.06, 1.06)
        ax.set_xticks([0, 0.5, 1])
        ax.grid(axis="y", visible=False)
    fig.supxlabel("A12 = P(ilk koşul daha iyi)", fontsize=8.5, color=INK2)
    axes[0].set_yticks(range(len(workloads)))
    axes[0].set_yticklabels([_short(w) for w in workloads])
    axes[0].invert_yaxis()
    fig.text(0.0, -0.06, "Dolu: Holm düzeltmeli Mann–Whitney p < 0,05; boş: anlamlı fark yok. "
                         f"ρ = {rho}; LLM 10, klasik 30 tohum.", fontsize=7.5, color=INK2)
    return _save(fig, "fig2_a12", table)


# ================================================================ 3) yakınsama eğrileri

REPRESENTATIVE = ["g01_pilot", "g03_shared_sparse", "h01_invoicing", "h02_inventory"]


def fig_convergence(rows: list[dict], rho: float = 0.1, workloads=REPRESENTATIVE) -> list[Path]:
    fig, axes = plt.subplots(2, 2, figsize=(6.3, 4.4), constrained_layout=True)
    table = []
    for ax, w in zip(axes.flat, workloads):
        for c in CONDITIONS:
            curves = [r["hv_curve"] for r in rows if r["workload"] == w and r["rho"] == rho and r["condition"] == c]
            if not curves:
                continue
            n = min(len(cv) for cv in curves)
            med = [statistics.median(cv[k] for cv in curves) for k in range(n)]
            xs = list(range(1, n + 1))
            ax.plot(xs, med, color=COLOR[c], marker=MARKER[c], markevery=max(1, n // 5), markersize=4,
                    markeredgecolor=SURFACE, markeredgewidth=0.8, label=LABEL[c])
            for k in sorted({0, n // 4, n // 2, 3 * n // 4, n - 1}):
                table.append({"iş_yükü": w, "koşul": LABEL[c], "değerlendirme": k + 1, "medyan_HV_oranı": round(med[k], 4)})
        ax.set_title(_short(w), loc="left")
        ax.set_ylim(0, 1.02)
        ax.set_xlim(1, None)
    for ax in axes[-1]:
        ax.set_xlabel("değerlendirme (benzersiz kod)")
    for ax in axes[:, 0]:
        ax.set_ylabel("HV oranı (medyan)")
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside upper center", ncol=5, handlelength=2.2)
    return _save(fig, "fig3_convergence", table)


# ================================================================ 4) Pareto sınırları (H3)

def _records(d: Path):
    space = SearchSpace.from_description(json.loads((d / "space.json").read_text(encoding="utf-8")))
    return space, list(load_records(d / "measurements.jsonl", space).values())


def fig_pareto(dirs: dict[str, Path], workloads=("g01_pilot", "g03_shared_sparse", "h01_invoicing")) -> list[Path]:
    fig, axes = plt.subplots(1, len(workloads), figsize=(6.3, 2.5), constrained_layout=True)
    table = []
    for ax, w in zip(axes, workloads):
        _, recs = _records(dirs[w])
        valid = [r for r in recs if r["valid"]]
        xs, ys = [r["f1"] / 1000 for r in valid], [r["f2"] / 2**20 for r in valid]
        ax.scatter(xs, ys, s=7, color=AXIS, linewidths=0, zorder=2)
        front = sorted(pareto_front(valid, key=lambda r: (r["f1"], r["f2"])), key=lambda r: r["f1"])
        fx, fy = [r["f1"] / 1000 for r in front], [r["f2"] / 2**20 for r in front]
        ax.step(fx, fy, where="post", color=SLOTS[0], lw=1.2, zorder=3)
        ax.scatter(fx, fy, s=24, color=SLOTS[0], edgecolors=SURFACE, linewidths=1.0, zorder=4)
        ax.set_title(f"{_short(w)}\n|P*| = {len(front)}, n = {len(valid)}", loc="left", fontsize=8)
        ax.set_xlabel("Consistent Gets (bin)")
        for r in front:
            table.append({"iş_yükü": w, "konfigürasyon": " ".join(f"{k}={v}" for k, v in r["config"].items()),
                          "consistent_gets": round(r["f1"]), "delta_pga_MB": round(r["f2"] / 2**20, 3)})
    axes[0].set_ylabel("ΔPGA (MB)")
    fig.text(0.0, -0.05, "Gri: tüm geçerli konfigürasyonlar; mavi: gerçek Pareto sınırı (ikisi de minimize).",
             fontsize=7.5, color=INK2)
    return _save(fig, "fig4_pareto", table)


# ================================================================ 5) kuplaj (H2)

def fig_coupling(dirs: dict[str, Path]) -> list[Path]:
    data = []
    for w, d in dirs.items():
        space, recs = _records(d)
        f1 = additivity(space, recs, "f1", "raw")
        f2 = additivity(space, recs, "f2", "raw")
        top = max(f2.pair_gains.items(), key=lambda kv: kv[1])
        data.append({"iş_yükü": w, "grup": group_of(w), "etkileşim_f1": round(f1.interaction_share, 4),
                     "etkileşim_f2": round(f2.interaction_share, 4), "gürültü_f2": round(f2.noise_share, 5),
                     "en_kuplajlı_çift_f2": f"{top[0][0]}×{top[0][1]}", "çift_kazancı": round(top[1], 4)})
    data.sort(key=lambda r: (GROUP_ORDER.index(r["grup"]), -r["etkileşim_f2"]))
    fig, ax = plt.subplots(figsize=(6.3, 3.4), constrained_layout=True)
    ys = list(range(len(data)))
    for y, r in zip(ys, data):
        ax.barh(y, r["etkileşim_f2"] * 100, height=0.62, color=GROUP_COLOR[r["grup"]], zorder=2)
        ax.text(r["etkileşim_f2"] * 100 + 0.5, y, f"{r['etkileşim_f2'] * 100:.1f}", va="center", fontsize=7, color=INK2)
    ax.set_yticks(ys)
    ax.set_yticklabels([_short(r["iş_yükü"]) for r in data])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("ΔPGA (f2) varyansında etkileşim payı, % (1 − R² toplamsal model)")
    from matplotlib.patches import Patch

    ax.legend(handles=[Patch(facecolor=GROUP_COLOR[g], label=g) for g in GROUP_ORDER], loc="lower right")
    ax.set_title("Siteler arası kuplaj (H2): toplamsal modelin açıklayamadığı varyans", loc="left")
    return _save(fig, "fig5_coupling", data)


# ================================================================ 6) geçerlilik: AUTO / MANUAL

def fig_validity(dirs: dict[str, Path], manual_root: Path = Path("results/validity/manual")) -> list[Path]:
    pairs = [(w, next((manual_root / w).glob("*/space.json")).parent) for w in ("g01_pilot", "h01_invoicing")
             if (manual_root / w).exists()]
    if not pairs:
        return []
    fig, axes = plt.subplots(1, len(pairs), figsize=(6.3, 2.7), constrained_layout=True)
    axes = axes if len(pairs) > 1 else [axes]
    table = []
    for ax, (w, mdir) in zip(axes, pairs):
        space, auto = _records(dirs[w])
        _, manual = _records(mdir)
        key = lambda r: tuple(space.canonicalize(r["config"]).values())  # noqa: E731
        m = {key(r): r for r in manual if r["valid"]}
        pts = [(a["f2"] / 2**20, m[key(a)]["f2"] / 2**20) for a in auto if a["valid"] and key(a) in m]
        lim = max(max(p) for p in pts) * 1.05
        ax.plot([0, lim], [0, lim], color=AXIS, lw=0.8, zorder=1)
        ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=10, color=SLOTS[0], linewidths=0, zorder=2)
        ax.set_xlim(0, lim)
        ax.set_ylim(0, lim)
        ax.set_aspect("equal")
        ax.set_title(_short(w), loc="left")
        ax.set_xlabel("ΔPGA, AUTO (MB)")
        table += [{"iş_yükü": w, "auto_MB": round(x, 3), "manual_MB": round(y, 3)} for x, y in pts]
    axes[0].set_ylabel("ΔPGA, MANUAL (MB)")
    fig.text(0.0, -0.05, "Her nokta bir konfigürasyon; çizgi y = x. Çalışma alanı politikası ΔPGA düzeyini "
                         "değiştirir, algoritma sıralamasını değiştirmez.", fontsize=7.5, color=INK2)
    return _save(fig, "fig6_validity", table)


def make_all(runs_path: Path = Path("results/summary/runs.jsonl")) -> list[Path]:
    _style()
    rows = load(runs_path)
    dirs = ground_truth_dirs()
    out = []
    out += fig_ranks(rows)
    out += fig_a12(rows)
    out += fig_convergence(rows)
    out += fig_pareto(dirs)
    out += fig_coupling(dirs)
    out += fig_validity(dirs)
    return out
