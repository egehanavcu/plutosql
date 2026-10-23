"""NSGA-II, uzman kuralı ve amaç–süre ilişkisi (sentetik uzay; veritabanı yok)."""

import pytest

from plutosql.analysis.validity import runtime_relation
from plutosql.search.algorithms import NSGA2, ExpertRule, expert_costs, nondominated_ranks
from plutosql.search.replay import ReplayEvaluator


def replay(space, records, budget=None):
    return ReplayEvaluator(space, records, budget)


def test_nondominated_ranks():
    pts = [(1, 5), (2, 2), (5, 1), (3, 3), (4, 4), (2, 2)]
    assert nondominated_ranks(pts) == [0, 0, 0, 1, 2, 0]


def test_expert_costs_respect_absorption_and_names(space):
    assert expert_costs(space, space.default()) == (4 + 2 + 4, 0)
    # s1 SET_BASED s2'yi soğurur: s2'nin önbelleği maliyete girmez
    assert expert_costs(space, {"s1": "SET_BASED", "s2": "LOOKUP_CACHE", "s3": "ROW"}) == (0 + 4, 1)
    assert expert_costs(space, {"s1": "BULK_50", "s2": "LOOKUP_CACHE", "s3": "ROW"}) == (3 + 0 + 4, 1 + 1)


def test_expert_evaluates_predicted_front_first(space, records):
    ev = replay(space, records, budget=100)
    result = ExpertRule().run(ev, seed=0)
    assert ev.budget_used == len(space.canonical_configs())
    configs = [e.config for e in result.history[1:]]
    costs = [expert_costs(space, c) for c in configs]
    ranks = nondominated_ranks([expert_costs(space, c) for c in space.canonical_configs()])
    first_front = sum(r == 0 for r in ranks)
    predicted = nondominated_ranks(costs)
    assert all(r == 0 for r in predicted[:first_front - 1])        # varsayılan zaten ilk değerlendirildi


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_nsga2_covers_space_with_full_budget(space, records, seed):
    ev = replay(space, records, budget=100)
    NSGA2().run(ev, seed=seed)
    assert ev.budget_used == len(space.canonical_configs())


def test_nsga2_never_repeats_code(space, records):
    ev = replay(space, records, budget=10)
    result = NSGA2().run(ev, seed=4)
    digests = [e.digest for e in result.history]
    assert len(digests) == len(set(digests)) == 10


def test_runtime_relation(space):
    records = []
    for c in space.canonical_configs():
        n = sum(a != "ROW" and a != "LOOKUP_SQL" for a in c.values())
        f1, f2, t = 100 - 10 * n, 10 + 5 * n, 50.0 - 5 * n
        records.append({"config": c, "valid": True, "f1": f1, "f2": f2,
                        "trials": [{"elapsed_ms": t}, {"elapsed_ms": t + 1}]})
    r = runtime_relation("sentetik", space, records)
    assert r.n == 15
    assert r.spearman_f1_time == pytest.approx(1.0)
    assert r.spearman_f2_time == pytest.approx(-1.0)
    assert r.speedup_min_f1 == r.speedup_best > 1
    assert r.front_time_regret == 1.0


def test_expert_variants_grid_and_original(space, records):
    from plutosql.analysis.expert_sensitivity import FACTORS, variants
    from plutosql.search.algorithms import EXPERT_CG, EXPERT_MEM

    vs = variants()
    n = 1
    for opts in FACTORS.values():
        n *= len(opts)
    assert len(vs) == n == len({v.name for v in vs})
    orig = [v for v in vs if v.is_original]
    assert len(orig) == 1
    algo = orig[0].algorithm()
    assert algo.cg_table == EXPERT_CG and algo.mem_table == EXPERT_MEM and algo.dedup
    a = algo.run(replay(space, records, 6), seed=2)
    b = ExpertRule().run(replay(space, records, 6), seed=2)
    assert [e.digest for e in a.history] == [e.digest for e in b.history]


def test_expert_without_dedup_counts_each_cache(space):
    x = {"s1": "BULK_50", "s2": "LOOKUP_CACHE", "s3": "ROW"}
    assert expert_costs(space, x, dedup=False) == expert_costs(space, x)
