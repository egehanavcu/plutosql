"""LLM politika ajanı — sahte istemciyle (API çağrısı yok) ve isteğe bağlı gerçek Gemini duman testi."""

import json

import pytest

from plutosql.search.experiment import GroundTruth, run_grid
from plutosql.search.llm import (
    CachedClient, LLMPolicy, LLMReply, _parse, build_prompt, response_schema,
)
from plutosql.search.replay import ReplayEvaluator


class ScriptedClient:
    """Sırayla verilen yanıtları döndürür; çağrıları kaydeder."""
    model = "fake"
    settings = {}

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, prompt, schema, *, temperature, seed):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema, "seed": seed})
        text = self.replies.pop(0) if self.replies else json.dumps({"rationale": "", "config": {}})
        return LLMReply(text)


def answer(**cfg):
    return json.dumps({"rationale": "deneme", "config": cfg})


def replay(space, records, budget=None):
    return ReplayEvaluator(space, records, budget)


def test_schema_restricts_each_site_to_its_alternatives(space, records):
    schema = response_schema(replay(space, records))
    props = schema["properties"]["config"]["properties"]
    assert props["s2"]["enum"] == ["LOOKUP_SQL", "LOOKUP_CACHE"]
    assert schema["properties"]["config"]["required"] == ["s1", "s2", "s3"]


def test_prompt_contains_ast_summary_history_and_budget(space, records):
    ev = replay(space, records, budget=5)
    ev.evaluate(space.default())
    ev.evaluate({"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "ROW"})
    prompt = build_prompt(ev, ["önceki yanıt tekrar"])
    assert '"absorbed_by": [\n   "s1=SET_BASED"' in prompt          # s2'nin soğurulma ilişkisi
    assert "used 2, remaining 3" in prompt
    assert "s1=SET_BASED s2=LOOKUP_SQL s3=ROW | f1=" in prompt
    assert "PARETO" in prompt
    assert "önceki yanıt tekrar" in prompt


def test_parse_validates_alternatives(space, records):
    ev = replay(space, records)
    x, _, problem = _parse(answer(s1="ROW", s2="LOOKUP_CACHE", s3="BULK_50"), ev)
    assert problem is None and x["s2"] == "LOOKUP_CACHE"
    assert "not one of" in _parse(answer(s1="ROW", s2="NOPE", s3="ROW"), ev)[2]
    assert "unknown site" in _parse(answer(s1="ROW", s2="LOOKUP_SQL", s3="ROW", s9="X"), ev)[2]
    assert "valid JSON" in _parse("bu json değil", ev)[2]


def test_policy_follows_llm_choices(space, records):
    client = ScriptedClient([
        answer(s1="BULK_50", s2="LOOKUP_CACHE", s3="ROW"),
        answer(s1="SET_BASED", s2="LOOKUP_SQL", s3="SET_BASED"),
    ])
    ev = replay(space, records, budget=3)
    result = LLMPolicy(client=client).run(ev, seed=0)
    assert [e.config for e in result.history] == [
        space.default(),
        {"s1": "BULK_50", "s2": "LOOKUP_CACHE", "s3": "ROW"},
        {"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "SET_BASED"},
    ]
    assert result.meta["fallbacks"] == 0 and result.meta["retries"] == 0
    assert [d["source"] for d in result.meta["decisions"]] == ["llm", "llm"]
    assert ev.budget_used == 3
    assert len(client.calls) == 2                                # bütçe dolunca LLM'e sorulmaz


def test_duplicate_answer_is_retried_with_feedback_then_falls_back(space, records):
    dup = answer(s1="SET_BASED", s2="LOOKUP_CACHE", s3="ROW")    # varsayılandan farklı ama…
    client = ScriptedClient([
        answer(s1="SET_BASED", s2="LOOKUP_SQL", s3="ROW"),       # adım 1: yeni
        dup, dup, dup,                                           # adım 2: kanonik tekrar ×3
    ])
    ev = replay(space, records, budget=3)
    result = LLMPolicy(client=client, max_retries=2).run(ev, seed=1)
    assert result.meta["retries"] == 2 and result.meta["fallbacks"] == 1
    second = result.meta["decisions"][1]
    assert second["source"] == "fallback" and len(second["feedback"]) == 3
    assert "equivalent to an already evaluated" in client.calls[2]["prompt"]
    assert len({tuple(space.canonicalize(e.config).values()) for e in result.history}) == 3


def test_call_seeds_are_deterministic_and_distinct(space, records):
    seeds = []
    for _ in range(2):
        client = ScriptedClient([answer(s1="BULK_50", s2="LOOKUP_SQL", s3="ROW")] * 5)
        LLMPolicy(client=client).run(replay(space, records, budget=2), seed=7)
        seeds.append([c["seed"] for c in client.calls])
    assert seeds[0] == seeds[1]
    other = ScriptedClient([answer(s1="BULK_50", s2="LOOKUP_SQL", s3="ROW")])
    LLMPolicy(client=other).run(replay(space, records, budget=2), seed=8)
    assert other.calls[0]["seed"] != seeds[0][0]


def test_no_hints_ablation_removes_domain_knowledge(space, records):
    client = ScriptedClient([answer(s1="BULK_50", s2="LOOKUP_SQL", s3="ROW")])
    LLMPolicy(client=client, domain_hints=False).run(replay(space, records, budget=2), seed=0)
    assert "LOOKUP_CACHE: the lookup table is preloaded" not in client.calls[0]["system"]


def test_cached_client_reuses_responses(tmp_path, space, records):
    inner = ScriptedClient([answer(s1="BULK_50", s2="LOOKUP_SQL", s3="ROW")] * 10)
    path = tmp_path / "cache.jsonl"
    c1 = CachedClient(inner, path)
    LLMPolicy(client=c1).run(replay(space, records, budget=2), seed=3)
    assert c1.calls == 1
    c2 = CachedClient(inner, path)                               # yeni süreç gibi: diskten yükle
    r = LLMPolicy(client=c2).run(replay(space, records, budget=2), seed=3)
    assert c2.calls == 0 and r.meta["cached_calls"] == 1 and len(inner.calls) == 1


def test_llm_runs_in_experiment_grid(space, records):
    client = ScriptedClient([answer(s1="SET_BASED", s2="LOOKUP_SQL", s3="SET_BASED")] * 50)
    gt = GroundTruth.from_records(list(records.values()), (0.0, 0.0))
    runs = run_grid({"llm": LLMPolicy(client=client)}, replay(space, records), gt, [0.2], range(2))
    assert len(runs) == 2 and all("decisions" in r.meta for r in runs)


@pytest.mark.gemini
def test_gemini_smoke(space, records):
    """Gerçek API: şema kısıtlı yanıt geçerli bir konfigürasyon olmalı."""
    from plutosql.env import env_value
    from plutosql.search.llm import GeminiClient
    if not env_value("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY yok")
    client = GeminiClient.from_env()
    ev = replay(space, records, budget=3)
    result = LLMPolicy(client=client).run(ev, seed=0)
    assert ev.budget_used == 3
    assert result.meta["malformed"] == 0


def test_cache_key_depends_on_client_settings(tmp_path):
    a, b = ScriptedClient([]), ScriptedClient([])
    b.settings = {"thinking_budget": 256}
    ka = CachedClient(a, tmp_path / "c.jsonl").key("s", "p", {}, 0.2, 1)
    kb = CachedClient(b, tmp_path / "c.jsonl").key("s", "p", {}, 0.2, 1)
    assert ka != kb


# ---------------------------------------------------------------- opak etiket ablation'ı

from plutosql.search.llm import OPAQUE_SYSTEM_PROMPT, Labels  # noqa: E402

LEAKS = ("ROW", "BULK", "SET_BASED", "LOOKUP", "SQL", "PL/", "Oracle", "PGA", "consistent", "cursor",
         "table", "INSERT", "UPDATE", "P1", "P2", "absorbed")


def test_opaque_labels_are_seeded_bijections(space):
    a, b, c = Labels(space, True, seed=1), Labels(space, True, seed=1), Labels(space, True, seed=2)
    assert a.to_label == b.to_label
    assert a.to_label != c.to_label
    for s in space.sites:
        assert sorted(a.to_label[s.id].values()) == [chr(ord("A") + i) for i in range(len(s.alternatives))]
        assert {a.from_label[s.id][a.label(s.id, alt)] for alt in s.alternatives} == set(s.alternatives)


def test_opaque_prompt_leaks_no_names_or_domain_context(space, records):
    ev = replay(space, records, budget=4)
    ev.evaluate(space.default())
    ev.evaluate({"s1": "SET_BASED", "s2": "LOOKUP_CACHE", "s3": "BULK_50"})
    labels = Labels(space, True, seed=3)
    prompt = build_prompt(ev, ["x"], labels)
    schema = json.dumps(response_schema(ev, labels))
    for text in (prompt, schema, OPAQUE_SYSTEM_PROMPT):
        for word in LEAKS:
            assert word not in text, (word, text[:200])
    # yapı korunur: s2, s1'in soğurucu etiketiyle yok sayılır
    assert f'"s1={labels.label("s1", "SET_BASED")}"' in prompt


def test_opaque_answers_are_mapped_back(space, records):
    labels = Labels(space, True, seed=0)
    target = {"s1": "BULK_50", "s2": "LOOKUP_CACHE", "s3": "SET_BASED"}
    client = ScriptedClient([answer(**{k: labels.label(k, v) for k, v in target.items()})])
    ev = replay(space, records, budget=2)
    result = LLMPolicy(client=client, opaque_labels=True).run(ev, seed=0)
    assert result.history[1].config == target
    assert result.meta["opaque_labels"] and result.meta["label_map"] == labels.to_label
    assert client.calls[0]["system"] == OPAQUE_SYSTEM_PROMPT
    assert all(word not in client.calls[0]["prompt"] for word in LEAKS)


# ---------------------------------------------------------------- ölçeklenme: compact geçmiş, k öneri

from plutosql.search.llm import RandomClient  # noqa: E402


def many(*cfgs):
    return json.dumps({"rationale": "toplu", "configs": [dict(c) for c in cfgs]})


def test_compact_history_shows_front_recent_and_effects(space, records):
    ev = replay(space, records)
    for c in space.canonical_configs()[:12]:
        ev.evaluate(c)
    full = build_prompt(ev, [])
    compact = build_prompt(ev, [], history="compact", recent=3)
    assert "12 in total" in compact and "omitted" in compact
    assert compact.count(" | f1=") < full.count(" | f1=")
    assert "## Per-site effects" in compact and "s1: ROW: n=" in compact
    assert " 12 | " in compact and "  1 | " in compact                    # son ve orijinal gösterilir
    assert compact.count("PARETO") == full.count("PARETO")                 # arşiv eksiksiz


def test_compact_effects_exclude_absorbed_sites(space, records):
    ev = replay(space, records)
    ev.evaluate(space.default())
    ev.evaluate({"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "ROW"})     # s2 soğurulmuş
    compact = build_prompt(ev, [], history="compact")
    line = next(l for l in compact.splitlines() if l.startswith("s2:"))
    assert "LOOKUP_SQL: n=1 " in line and "LOOKUP_CACHE: untested" in line


def test_k_proposals_schema_and_batch_evaluation(space, records):
    ev = replay(space, records, budget=4)
    schema = response_schema(ev, proposals=3)
    assert schema["properties"]["configs"]["maxItems"] == 3
    client = ScriptedClient([many(
        {"s1": "ROW", "s2": "LOOKUP_SQL", "s3": "ROW"},                    # orijinal: tekrar, atlanır
        {"s1": "BULK_50", "s2": "LOOKUP_SQL", "s3": "ROW"},
        {"s1": "BULK_50", "s2": "LOOKUP_SQL", "s3": "ROW"},                # yanıt içi tekrar
        {"s1": "SET_BASED", "s2": "NOPE", "s3": "ROW"},                    # geçersiz öğe
        {"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "BULK_50"},
    )] + [many({"s1": "ROW", "s2": "LOOKUP_CACHE", "s3": "ROW"})])
    result = LLMPolicy(client=client, proposals=3).run(ev, seed=0)
    assert [e.config for e in result.history[1:3]] == [
        {"s1": "BULK_50", "s2": "LOOKUP_SQL", "s3": "ROW"},
        {"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "BULK_50"},
    ]
    assert result.meta["malformed"] == 1 and len(client.calls) == 2
    assert [d["rank"] for d in result.meta["decisions"]] == [0, 1, 0]
    assert ev.budget_used == 4


def test_k_proposals_all_duplicates_trigger_feedback(space, records):
    dup = many({"s1": "ROW", "s2": "LOOKUP_SQL", "s3": "ROW"})
    client = ScriptedClient([dup, many({"s1": "BULK_50", "s2": "LOOKUP_SQL", "s3": "ROW"})])
    result = LLMPolicy(client=client, proposals=2).run(replay(space, records, budget=2), seed=0)
    assert result.meta["retries"] == 1 and result.meta["fallbacks"] == 0
    assert "all proposed configurations are equivalent" in client.calls[1]["prompt"]


def test_random_client_drives_policy_for_estimates(space, records):
    for k in (1, 4):
        ev = replay(space, records, budget=8)
        result = LLMPolicy(client=RandomClient(seed=1), proposals=k, history="compact").run(ev, seed=0)
        assert ev.budget_used == 8 and result.meta["prompt_chars"] > 0
        assert result.meta["malformed"] == 0
