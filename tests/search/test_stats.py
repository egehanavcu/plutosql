import pytest

from plutosql.analysis.stats import a12, friedman, holm, magnitude, mann_whitney, pairwise


def test_a12_directions():
    assert a12([1, 2], [3, 4]) == 1.0                       # düşük iyi: x hep iyi
    assert a12([1, 2], [3, 4], lower_is_better=False) == 0.0
    assert a12([1, 1], [1, 1]) == 0.5
    assert magnitude(0.75) == "büyük" and magnitude(0.55) == "ihmal" and magnitude(0.36) == "orta"


def test_holm_is_monotone_and_bounded():
    p = [0.01, 0.04, 0.03, 0.5]
    adj = holm(p)
    assert adj[0] == pytest.approx(0.04)                    # 4 · 0.01
    assert adj[2] == pytest.approx(0.09) and adj[1] == pytest.approx(0.09)   # max(3·0.03, 2·0.04)
    assert adj[3] == pytest.approx(0.5)
    assert all(a >= b for a, b in zip(adj, p))


def test_mann_whitney_identical_samples():
    assert mann_whitney([1.0, 1.0], [1.0, 1.0]) == 1.0
    assert mann_whitney(list(range(10)), list(range(100, 110))) < 0.001


def rows_for(values: dict[tuple[str, str], list[float]], rho=0.1):
    return [{"workload": w, "rho": rho, "condition": c, "igd_plus": v, "front_found_at": None, "hv_ratio": 0.0}
            for (w, c), vs in values.items() for v in vs]


def test_pairwise_and_friedman():
    vals = {}
    for i, w in enumerate(["w1", "w2", "w3", "w4", "w5"]):
        vals[(w, "good")] = [0.1 + i * 0.01 + j * 0.001 for j in range(5)]
        vals[(w, "mid")] = [0.3 + i * 0.01 + j * 0.001 for j in range(5)]
        vals[(w, "bad")] = [0.6 + i * 0.01 + j * 0.001 for j in range(5)]
    rows = rows_for(vals)
    res = pairwise(rows, "good", "bad")
    assert len(res) == 5 and all(r.a12 == 1.0 for r in res)
    assert all(r.p_holm >= r.p for r in res)
    fr = friedman(rows, 0.1, ["good", "mid", "bad"])
    assert fr.mean_ranks == {"good": 1.0, "mid": 2.0, "bad": 3.0}
    assert fr.p < 0.01 and fr.critical_difference > 0
