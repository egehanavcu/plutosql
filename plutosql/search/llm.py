"""LLM güdümlü arama politikası.

LLM kod üretmez. Her adımda girdi olarak
  - AST özeti: siteler, kalıpları, yapısal özetleri, alternatif kümeleri A_i, iç içelik (soğurma) ilişkisi,
  - geçmiş: değerlendirilen konfigürasyonlar ve (f1, f2) vektörleri, geçerlilik, güncel Pareto arşivi,
  - kalan bütçe
alır ve JSON şemasıyla kısıtlanmış bir yanıtla (her site için yalnızca A_i'deki değerler) bir sonraki
konfigürasyonu x_next seçer.

Tekrar önleme: önerilen x (kanonik) daha önce değerlendirilmişse, geri bildirimle en fazla `max_retries` kez
yeniden sorulur; yine olmazsa denenmemiş rastgele bir konfigürasyona düşülür. Yeniden sorma ve düşme sayıları
`SearchResult.meta` içinde raporlanır (LLM'in kendi katkısını ayırt etmek için).

Tekrarlanabilirlik: her çağrı (model, sıcaklık, tohum, istem) ile anahtarlanıp `CachedClient` ile diske
yazılır; aynı deney API'ye gitmeden yeniden üretilir.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from plutosql.harness.evaluator import Evaluation

from plutosql.transformer.sites import ABSORBING_ALTERNATIVES

from .base import EvaluatorLike, SearchAlgorithm, archive_of

# ================================================================ istemciler


@dataclass(frozen=True)
class LLMReply:
    text: str
    usage: dict = field(default_factory=dict)
    cached: bool = False


class LLMClient(Protocol):
    model: str
    settings: dict                       # yanıtı etkileyen istemci ayarları (önbellek anahtarına girer)

    def complete(self, system: str, prompt: str, schema: dict, *, temperature: float, seed: int) -> LLMReply: ...


class GeminiClient:
    """Gemini API (google-genai). JSON şemasıyla kısıtlı çıktı; 429/5xx'te üstel geri çekilme."""

    def __init__(self, model: str, api_key: str, max_attempts: int = 6, thinking_budget: int | None = None):
        from google import genai                                  # isteğe bağlı bağımlılık

        self.model = model
        self.max_attempts = max_attempts
        self.thinking_budget = thinking_budget
        self.settings = {"thinking_budget": thinking_budget}
        self._client = genai.Client(api_key=api_key)

    @classmethod
    def from_env(cls, model: str | None = None, **kw) -> "GeminiClient":
        from plutosql.env import env_value

        key = env_value("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY tanımlı değil (.env dosyasına ekleyin)")
        return cls(model or env_value("GEMINI_MODEL", "gemini-3.8-flash"), key, **kw)

    def complete(self, system: str, prompt: str, schema: dict, *, temperature: float, seed: int) -> LLMReply:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            seed=seed,
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        if self.thinking_budget is not None:
            config.thinking_config = types.ThinkingConfig(thinking_budget=self.thinking_budget)

        delay = 2.0
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self._client.models.generate_content(model=self.model, contents=prompt, config=config)
                usage = resp.usage_metadata
                return LLMReply(
                    text=resp.text or "",
                    usage={
                        "prompt_tokens": getattr(usage, "prompt_token_count", None),
                        "output_tokens": getattr(usage, "candidates_token_count", None),
                        "thought_tokens": getattr(usage, "thoughts_token_count", None),
                    },
                )
            except errors.APIError as e:
                retryable = e.code == 429 or (e.code is not None and e.code >= 500)
                if not retryable or attempt == self.max_attempts:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise AssertionError("ulaşılamaz")


class CachedClient:
    """Yanıtları (model, sistem, istem, şema, sıcaklık, tohum) anahtarıyla JSONL dosyasında saklar."""

    def __init__(self, inner: LLMClient, path: Path):
        self.inner = inner
        self.model = inner.model
        self.settings = dict(getattr(inner, "settings", {}))
        self.path = Path(path)
        self.calls = 0                                            # gerçek API çağrısı sayısı
        self._cache: dict[str, dict] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        self._cache[rec["key"]] = rec

    def key(self, system: str, prompt: str, schema: dict, temperature: float, seed: int) -> str:
        blob = json.dumps([self.model, self.settings, system, prompt, schema, temperature, seed],
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def complete(self, system: str, prompt: str, schema: dict, *, temperature: float, seed: int) -> LLMReply:
        k = self.key(system, prompt, schema, temperature, seed)
        rec = self._cache.get(k)
        if rec is not None:
            return LLMReply(rec["text"], rec.get("usage", {}), cached=True)
        reply = self.inner.complete(system, prompt, schema, temperature=temperature, seed=seed)
        self.calls += 1
        rec = {"key": k, "model": self.model, "settings": self.settings, "temperature": temperature, "seed": seed,
               "text": reply.text, "usage": reply.usage}
        self._cache[k] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return reply


# ================================================================ istem

SYSTEM_PROMPT = """\
You are the search policy of a multi-objective optimizer for PL/SQL rewrite configurations.
You never write code. Each turn you choose exactly one configuration to evaluate next: one alternative
per optimization site, taken only from that site's allowed alternatives.

Objectives (both minimized, measured on a real Oracle database):
  f1 = consistent gets (logical I/O, blocks read from the buffer cache)
  f2 = delta PGA (net peak private session memory, bytes)
The goal is to discover the Pareto front of (f1, f2) with as few evaluations as possible.

Rules:
- A configuration equivalent to one already evaluated wastes your turn. Two configurations are equivalent
  when they differ only in sites absorbed by a SET_BASED ancestor (see "absorbed_by").
- Prefer configurations likely to be non-dominated, or that test an unexplored part of the trade-off.
- Use the measured history; do not assume effects are additive without evidence.
{hints}
Respond with JSON: a short rationale (at most 40 words) and the configuration."""

DOMAIN_HINTS = """\
Alternative semantics (prior knowledge; may be wrong for this workload):
- ROW: original row-by-row loop. BULK_<L>: BULK COLLECT ... LIMIT L + FORALL; fewer context switches,
  memory grows with L. SET_BASED: the whole loop becomes one SQL statement (INSERT ... SELECT or MERGE);
  nested sites are absorbed.
- LOOKUP_SQL: one SELECT per row. LOOKUP_CACHE: the lookup table is preloaded into an associative array;
  fewer logical reads, more session memory."""

# Opak etiket ablation'ı: alan bilgisi, PL/SQL bağlamı ve alternatif adları yok; yalnızca uzayın yapısı
# (siteler, etiketler, soğurma ilişkisi) ve ölçülen geçmiş verilir.
OPAQUE_SYSTEM_PROMPT = """\
You are the search policy of a black-box multi-objective optimizer.
A configuration assigns one opaque label to each site; labels carry no meaning beyond identity, and
their letters are assigned at random. Each turn you choose exactly one configuration to evaluate next.

Objectives: two costs f1 and f2, both minimized. The goal is to discover the Pareto front of (f1, f2)
with as few evaluations as possible.

Rules:
- A configuration equivalent to one already evaluated wastes your turn. Two configurations are equivalent
  when they differ only in sites that are ignored because of an ancestor's label (see "ignored_when").
- Prefer configurations likely to be non-dominated, or that test an unexplored part of the trade-off.
- Infer the effect of each label only from the measured history.

Respond with JSON: a short rationale (at most 40 words) and the configuration."""


class Labels:
    """Gerçek alternatif adları ↔ istemde görünen etiketler.

    Normal modda birebir. Opak modda her sitenin alternatifleri tohuma göre karıştırılıp A, B, C, …
    harfleriyle etiketlenir; böylece ne ad ne de sıra (ör. orijinalin hep ilk olması) bilgi taşır.
    """

    def __init__(self, space, opaque: bool = False, seed: int = 0):
        self.opaque = opaque
        self.to_label: dict[str, dict[str, str]] = {}
        self.from_label: dict[str, dict[str, str]] = {}
        rng = random.Random(f"labels:{seed}")
        for s in space.sites:
            alts = list(s.alternatives)
            if opaque:
                rng.shuffle(alts)
                pairs = {alt: chr(ord("A") + i) for i, alt in enumerate(alts)}
            else:
                pairs = {alt: alt for alt in alts}
            self.to_label[s.id] = pairs
            self.from_label[s.id] = {lab: alt for alt, lab in pairs.items()}

    def label(self, site_id: str, alt: str) -> str:
        return self.to_label[site_id][alt]

    def choices(self, site_id: str) -> list[str]:
        return sorted(self.from_label[site_id]) if self.opaque else list(self.from_label[site_id])

    def show(self, config: Mapping[str, str]) -> str:
        return " ".join(f"{sid}={self.label(sid, alt)}" for sid, alt in config.items())


def _kb(v: float | None) -> str:
    return "-" if v is None else f"{v / 1024:.0f}"


def _site_view(space, s, labels: Labels) -> dict:
    """İstemdeki site özeti. Opak modda yalnızca yapı (etiketler ve soğurma ilişkisi)."""
    ancestors = []
    p = s.parent
    while p is not None:
        ancestors.append(p)
        p = space.site(p).parent
    absorbing = [
        f"{a}={labels.label(a, alt)}"
        for a in ancestors for alt in space.site(a).alternatives if alt in ABSORBING_ALTERNATIVES
    ]
    if labels.opaque:
        return {"id": s.id, "labels": labels.choices(s.id), "original": labels.label(s.id, s.default),
                "ignored_when": absorbing}
    return {"id": s.id, "pattern": s.pattern, "name": s.name, "structure": s.summary(),
            "alternatives": list(s.alternatives), "original": s.default, "absorbed_by": absorbing}


def build_prompt(ev: EvaluatorLike, feedback: list[str], labels: Labels | None = None) -> str:
    space = ev.space
    labels = labels or Labels(space)
    sites = [_site_view(space, s, labels) for s in space.sites]

    seen: dict[str, Evaluation] = {}
    for e in ev.history:
        seen.setdefault(e.digest, e)
    base = next(iter(seen.values()), None)
    front = {e.digest for e in archive_of(list(seen.values()))}

    unit = "" if labels.opaque else "KB"
    rows = []
    for k, e in enumerate(seen.values(), start=1):
        cfg = labels.show(e.config)
        if not e.valid or e.f1 is None:
            detail = "" if labels.opaque else f" ({'; '.join(e.differences)[:80] or e.error})"
            rows.append(f"{k:>3} | {cfg} | INVALID{detail}")
            continue
        rel = ""
        if base is not None and base.f1:
            rel = f" | f1 {100 * e.f1 / base.f1:.0f}% f2 {100 * e.f2 / base.f2:.0f}% of original"
        mark = " | PARETO" if e.digest in front else ""
        rows.append(f"{k:>3} | {cfg} | f1={e.f1:.0f} f2={_kb(e.f2)}{unit}{rel}{mark}")

    left = ev.budget_left
    parts = [
        "## Sites" + ("" if labels.opaque else " (AST summary)"),
        json.dumps(sites, ensure_ascii=False, indent=1),
        f"## Search space\n|X| = {space.size}, distinct (canonical) configurations = {len(space.canonical_configs())}",
        f"## Budget\nused {ev.budget_used}" + ("" if left is None else f", remaining {left}"),
        "## Evaluated configurations (in order)\n" + ("\n".join(rows) if rows else "(none)"),
    ]
    if feedback:
        parts.append("## Feedback on your previous answer this turn\n" + "\n".join(f"- {m}" for m in feedback))
    parts.append("Choose the next configuration to evaluate.")
    return "\n\n".join(parts)


def response_schema(ev: EvaluatorLike, labels: Labels | None = None) -> dict:
    labels = labels or Labels(ev.space)
    return {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
            "config": {
                "type": "object",
                "properties": {s.id: {"type": "string", "enum": labels.choices(s.id)} for s in ev.space.sites},
                "required": [s.id for s in ev.space.sites],
            },
        },
        "required": ["rationale", "config"],
    }


# ================================================================ politika


@dataclass
class LLMPolicy(SearchAlgorithm):
    client: LLMClient = None
    name: str = "llm"
    temperature: float = 0.2
    max_retries: int = 2
    domain_hints: bool = True
    opaque_labels: bool = False             # ablation: alternatif adları ve alan bağlamı gizlenir
    max_steps: int = 200                    # bütçesiz (canlı) kullanımda üst sınır

    def _seed(self, step: int, attempt: int) -> int:
        """Çağrı tohumu: koşu tohumu, adım ve deneme numarasından deterministik (31 bit)."""
        h = hashlib.sha256(f"{self.seed}:{step}:{attempt}".encode()).digest()
        return int.from_bytes(h[:4], "big") & 0x7FFFFFFF

    def _search(self, ev: EvaluatorLike, rng: random.Random) -> None:
        if self.client is None:
            raise ValueError("LLMPolicy bir LLM istemcisi gerektirir")
        space = ev.space
        labels = Labels(space, opaque=self.opaque_labels, seed=self.seed)
        if self.opaque_labels:
            system = OPAQUE_SYSTEM_PROMPT
        else:
            system = SYSTEM_PROMPT.format(hints=DOMAIN_HINTS if self.domain_hints else "")
        schema = response_schema(ev, labels)
        decisions = self.meta.setdefault("decisions", [])
        self.meta.update({"model": self.client.model, "client_settings": dict(getattr(self.client, "settings", {})),
                          "temperature": self.temperature,
                          "domain_hints": self.domain_hints and not self.opaque_labels,
                          "opaque_labels": self.opaque_labels, "retries": 0, "fallbacks": 0,
                          "malformed": 0, "api_calls": 0, "cached_calls": 0})
        if self.opaque_labels:
            self.meta["label_map"] = labels.to_label

        ev.evaluate(space.default())
        total = len(space.canonical_configs())
        for step in range(1, self.max_steps + 1):
            if ev.budget_left is not None and ev.budget_left <= 0:
                return                                           # boşa API çağrısı yapma
            seen = {tuple(space.canonicalize(e.config).values()) for e in ev.history}
            if len(seen) >= total:
                return
            feedback: list[str] = []
            chosen, rationale, source = None, "", "llm"
            for attempt in range(self.max_retries + 1):
                prompt = build_prompt(ev, feedback, labels)
                reply = self.client.complete(system, prompt, schema,
                                             temperature=self.temperature, seed=self._seed(step, attempt))
                self.meta["cached_calls" if reply.cached else "api_calls"] += 1
                x, rationale, problem = _parse(reply.text, ev, labels)
                if problem is None:
                    key = tuple(space.canonicalize(x).values())
                    if key not in seen:
                        chosen = x
                        break
                    problem = (f"{labels.show(x)} is equivalent to an already "
                               f"evaluated configuration; choose a different one")
                else:
                    self.meta["malformed"] += 1
                feedback.append(problem)
                if attempt < self.max_retries:
                    self.meta["retries"] += 1
            if chosen is None:                                   # LLM tekrar etmeye devam etti
                self.meta["fallbacks"] += 1
                source = "fallback"
                unseen = [c for c in space.canonical_configs() if tuple(c.values()) not in seen]
                if not unseen:
                    return
                chosen = rng.choice(unseen)
            decisions.append({"step": step, "config": dict(chosen), "source": source,
                              "rationale": rationale[:300], "feedback": feedback})
            ev.evaluate(chosen)


def _parse(text: str, ev: EvaluatorLike, labels: Labels | None = None) -> tuple[dict, str, str | None]:
    """(gerçek adlarla config, gerekçe, sorun). Şema kısıtlı olsa da yanıt yine de doğrulanır."""
    labels = labels or Labels(ev.space)
    try:
        data = json.loads(text)
        cfg, rationale = data["config"], str(data.get("rationale", ""))
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}, "", "response was not valid JSON with keys 'rationale' and 'config'"
    if not isinstance(cfg, Mapping):
        return {}, rationale, "'config' must be an object"
    x = {}
    for s in ev.space.sites:
        lab = cfg.get(s.id)
        if lab not in labels.from_label[s.id]:
            return {}, rationale, f"site {s.id}: {lab!r} is not one of {labels.choices(s.id)}"
        x[s.id] = labels.from_label[s.id][lab]
    extra = set(cfg) - set(x)
    if extra:
        return {}, rationale, f"unknown site ids: {sorted(extra)}"
    return x, rationale, None
