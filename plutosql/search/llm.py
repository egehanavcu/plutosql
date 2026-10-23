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
import statistics
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from plutosql.harness.evaluator import Evaluation

from plutosql.transformer.sites import ABSORBING_ALTERNATIVES

from .base import EvaluatorLike, SearchAlgorithm, archive_of, neighbors

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
    """Gemini API (google-genai). JSON şemasıyla kısıtlı çıktı.

    Her isteğin zaman aşımı vardır (takılan bir bağlantı koşuyu süresiz bekletmesin); 429/5xx, zaman aşımı
    ve ağ hatalarında üstel geri çekilmeyle yeniden denenir.
    """

    def __init__(self, model: str, api_key: str, max_attempts: int = 6, thinking_budget: int | None = None,
                 timeout_s: float = 120.0, thinking_level: str | None = None):
        from google import genai                                  # isteğe bağlı bağımlılık
        from google.genai import types

        self.model = model
        self.max_attempts = max_attempts
        self.thinking_budget = thinking_budget
        self.thinking_level = thinking_level                      # MINIMAL | LOW | MEDIUM | HIGH (düşünmeye üst sınır)
        # Yanıtı etkileyen ayarlar (önbellek anahtarına girer; zaman aşımı girmez). Geriye uyum için
        # thinking_level yalnızca verildiğinde eklenir: eski önbellek anahtarları değişmez.
        self.settings = {"thinking_budget": thinking_budget}
        if thinking_level is not None:
            self.settings["thinking_level"] = thinking_level
        self._client = genai.Client(api_key=api_key,
                                    http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    @classmethod
    def from_env(cls, model: str | None = None, **kw) -> "GeminiClient":
        from plutosql.env import env_value

        key = env_value("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY tanımlı değil (.env dosyasına ekleyin)")
        return cls(model or env_value("GEMINI_MODEL", "gemini-3.8-flash"), key, **kw)

    def complete(self, system: str, prompt: str, schema: dict, *, temperature: float, seed: int) -> LLMReply:
        import httpx
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
        elif self.thinking_level is not None:
            config.thinking_config = types.ThinkingConfig(thinking_level=self.thinking_level)

        delay = 2.0
        used_seed = seed
        for attempt in range(1, self.max_attempts + 1):
            try:
                config.seed = used_seed
                resp = self._client.models.generate_content(model=self.model, contents=prompt, config=config)
                usage = resp.usage_metadata
                return LLMReply(
                    text=resp.text or "",
                    usage={
                        "prompt_tokens": getattr(usage, "prompt_token_count", None),
                        "output_tokens": getattr(usage, "candidates_token_count", None),
                        "thought_tokens": getattr(usage, "thoughts_token_count", None),
                        "seed_used": used_seed,
                        "attempts": attempt,
                    },
                )
            except (errors.APIError, httpx.TimeoutException, httpx.TransportError) as e:
                runaway = (isinstance(e, httpx.TimeoutException)
                           or (isinstance(e, errors.APIError) and e.code == 504))
                if isinstance(e, errors.APIError):
                    retryable = e.code == 429 or (e.code is not None and e.code >= 500)
                else:
                    retryable = True                              # zaman aşımı / bağlantı hatası
                if not retryable or attempt == self.max_attempts:
                    raise
                if runaway:
                    # Aşırı uzun düşünme tohuma bağlı: aynı tohumla tekrar aynı yanıtı üretir. Tohumu
                    # deterministik biçimde değiştir (kullanılan tohum yanıtla birlikte kaydedilir).
                    used_seed = (seed + 1_000_003 * attempt) & 0x7FFFFFFF
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

# Geri bildirimsiz ablation: adlar ve alan ipuçları aynı; ölçüm sonuçları (f1, f2, Pareto işareti) yok.
# Değerlendirilmiş konfigürasyonlar yine listelenir: model tekrar önermesin, yalnızca ölçüm bilgisi eksilsin.
BLIND_SYSTEM_PROMPT = """\
You are the search policy of a multi-objective optimizer for PL/SQL rewrite configurations.
You never write code. Each turn you choose exactly one configuration to evaluate next: one alternative
per optimization site, taken only from that site's allowed alternatives.

Objectives (both minimized, measured on a real Oracle database):
  f1 = consistent gets (logical I/O, blocks read from the buffer cache)
  f2 = delta PGA (net peak private session memory, bytes)
The goal is to discover the Pareto front of (f1, f2) with as few evaluations as possible.
Measurement results are NOT shown to you: you only see which configurations have already been evaluated.

Rules:
- A configuration equivalent to one already evaluated wastes your turn. Two configurations are equivalent
  when they differ only in sites absorbed by a SET_BASED ancestor (see "absorbed_by").
- Prefer configurations you expect to be non-dominated, covering different parts of the trade-off.
- Rely on the site structure and your knowledge of the alternatives.
{hints}
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


def _pct(v: float, base: float) -> str:
    return f"{100 * v / base:.0f}%" if base else "-"


def _row(k: int, e: Evaluation, labels: Labels, base: Evaluation | None, front: set[str]) -> str:
    cfg = labels.show(e.config)
    if not e.valid or e.f1 is None:
        detail = "" if labels.opaque else f" ({'; '.join(e.differences)[:80] or e.error})"
        return f"{k:>3} | {cfg} | INVALID{detail}"
    unit = "" if labels.opaque else "KB"
    rel = ""
    if base is not None and base.f1:
        rel = f" | f1 {100 * e.f1 / base.f1:.0f}% f2 {100 * e.f2 / base.f2:.0f}% of original"
    mark = " | PARETO" if e.digest in front else ""
    return f"{k:>3} | {cfg} | f1={e.f1:.0f} f2={_kb(e.f2)}{unit}{rel}{mark}"


def _effects(space, evaluated: list[Evaluation], base: Evaluation | None, labels: Labels) -> list[str]:
    """Site başına, her alternatifin geçtiği (soğurulmamış) geçerli konfigürasyonlardaki f1/f2 özeti.

    Değerler orijinal koda göre yüzdedir: en iyi ve medyan. Sıkıştırılmış geçmişte, gösterilmeyen
    değerlendirmelerin bilgisini taşır.
    """
    if base is None or not base.f1:
        return []
    lines = []
    valid = [e for e in evaluated if e.valid and e.f1 is not None]
    for s in space.sites:
        parts = []
        for lab in labels.choices(s.id):
            alt = labels.from_label[s.id][lab]
            vals = [e for e in valid if e.config[s.id] == alt and not space.is_absorbed(e.config, s.id)]
            if not vals:
                parts.append(f"{lab}: untested")
                continue
            f1 = sorted(e.f1 for e in vals)
            f2 = sorted(e.f2 for e in vals)
            parts.append(f"{lab}: n={len(vals)} f1 best {_pct(f1[0], base.f1)} median {_pct(statistics.median(f1), base.f1)}"
                         f", f2 best {_pct(f2[0], base.f2)} median {_pct(statistics.median(f2), base.f2)}")
        lines.append(f"{s.id}: " + " | ".join(parts))
    return lines


def build_prompt(ev: EvaluatorLike, feedback: list[str], labels: Labels | None = None,
                 history: str = "full", recent: int = 10, proposals: int = 1) -> str:
    """İstem. history="full": tüm geçmiş (küçük bütçeler). history="compact": orijinal + güncel Pareto
    arşivi + son `recent` değerlendirme + site başına etki özeti; boyutu bütçeden bağımsızdır."""
    space = ev.space
    labels = labels or Labels(space)
    sites = [_site_view(space, s, labels) for s in space.sites]

    seen: dict[str, Evaluation] = {}
    for e in ev.history:
        seen.setdefault(e.digest, e)
    evaluated = list(seen.values())
    base = evaluated[0] if evaluated else None
    front = {e.digest for e in archive_of(evaluated)}

    if history == "full":
        rows = [_row(k, e, labels, base, front) for k, e in enumerate(evaluated, start=1)]
        hist = ["## Evaluated configurations (in order)\n" + ("\n".join(rows) if rows else "(none)")]
    elif history == "blind":
        rows = [f"{k:>3} | {labels.show(e.config)}" for k, e in enumerate(evaluated, start=1)]
        hist = ["## Already evaluated configurations (results hidden)\n" + ("\n".join(rows) if rows else "(none)")]
    elif history == "compact":
        keep = {e.digest for e in evaluated[:1]} | front | {e.digest for e in evaluated[-recent:]}
        rows = [_row(k, e, labels, base, front) for k, e in enumerate(evaluated, start=1) if e.digest in keep]
        omitted = len(evaluated) - len(rows)
        head = (f"## Evaluated configurations: {len(evaluated)} in total (showing the original, the current "
                f"Pareto set and the last {recent}; {omitted} omitted)")
        hist = [head + "\n" + ("\n".join(rows) if rows else "(none)")]
        effects = _effects(space, evaluated, base, labels)
        if effects:
            hist.append("## Per-site effects over all evaluated configurations (relative to original)\n"
                        + "\n".join(effects))
    else:
        raise ValueError(f"bilinmeyen geçmiş biçimi: {history}")

    left = ev.budget_left
    parts = [
        "## Sites" + ("" if labels.opaque else " (AST summary)"),
        json.dumps(sites, ensure_ascii=False, indent=1),
        f"## Search space\n|X| = {space.size}, distinct (canonical) configurations = {len(space.canonical_configs())}",
        f"## Budget\nused {ev.budget_used}" + ("" if left is None else f", remaining {left}"),
        *hist,
    ]
    if feedback:
        parts.append("## Feedback on your previous answer this turn\n" + "\n".join(f"- {m}" for m in feedback))
    if proposals == 1:
        parts.append("Choose the next configuration to evaluate.")
    else:
        parts.append(f"Propose up to {proposals} distinct configurations to evaluate next, most promising first. "
                     "Each must differ from every evaluated configuration.")
    return "\n\n".join(parts)


def _config_schema(ev: EvaluatorLike, labels: Labels) -> dict:
    return {
        "type": "object",
        "properties": {s.id: {"type": "string", "enum": labels.choices(s.id)} for s in ev.space.sites},
        "required": [s.id for s in ev.space.sites],
    }


def response_schema(ev: EvaluatorLike, labels: Labels | None = None, proposals: int = 1) -> dict:
    labels = labels or Labels(ev.space)
    if proposals == 1:
        return {
            "type": "object",
            "properties": {"rationale": {"type": "string"}, "config": _config_schema(ev, labels)},
            "required": ["rationale", "config"],
        }
    return {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
            "configs": {"type": "array", "items": _config_schema(ev, labels), "minItems": 1, "maxItems": proposals},
        },
        "required": ["rationale", "configs"],
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
    history: str = "full"                   # full | compact (büyük bütçeler) | blind (ablation: sonuçlar gizli)
    recent: int = 10                        # compact: gösterilen son değerlendirme sayısı
    proposals: int = 1                      # çağrı başına önerilen konfigürasyon sayısı (k)
    project: bool = False                   # tekrar eden öneriyi denenmemiş komşusuna izdüşür (ek çağrı yok)
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
        if self.opaque_labels and self.history == "blind":
            raise ValueError("opak etiket + geri bildirimsiz geçmiş anlamsız (bilgi kalmaz)")
        if self.opaque_labels:
            system = OPAQUE_SYSTEM_PROMPT
        elif self.history == "blind":
            system = BLIND_SYSTEM_PROMPT.format(hints=DOMAIN_HINTS if self.domain_hints else "")
        else:
            system = SYSTEM_PROMPT.format(hints=DOMAIN_HINTS if self.domain_hints else "")
        schema = response_schema(ev, labels, self.proposals)
        decisions = self.meta.setdefault("decisions", [])
        self.meta.update({"model": self.client.model, "client_settings": dict(getattr(self.client, "settings", {})),
                          "temperature": self.temperature,
                          "domain_hints": self.domain_hints and not self.opaque_labels,
                          "opaque_labels": self.opaque_labels, "history": self.history, "recent": self.recent,
                          "proposals": self.proposals, "project": self.project, "retries": 0, "fallbacks": 0,
                          "malformed": 0, "api_calls": 0, "cached_calls": 0, "prompt_chars": 0})
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
            queue: list[tuple[dict, str]] = []
            rationale = ""
            last_proposal: dict | None = None
            for attempt in range(self.max_retries + 1):
                prompt = build_prompt(ev, feedback, labels, self.history, self.recent, self.proposals)
                self.meta["prompt_chars"] += len(system) + len(prompt)
                reply = self.client.complete(system, prompt, schema,
                                             temperature=self.temperature, seed=self._seed(step, attempt))
                self.meta["cached_calls" if reply.cached else "api_calls"] += 1
                if self.proposals == 1:
                    x, rationale, problem = _parse(reply.text, ev, labels)
                    xs = [x] if problem is None else []
                else:
                    xs, rationale, problem, bad = _parse_many(reply.text, ev, labels)
                    self.meta["malformed"] += bad
                if problem is None:
                    last_proposal = xs[0]
                    keys: set[tuple] = set()
                    for x in xs:
                        key = tuple(space.canonicalize(x).values())
                        if key not in seen and key not in keys:
                            keys.add(key)
                            queue.append((x, "llm"))
                        elif self.project:
                            # İzdüşüm: tekrar eden öneri yerine onun denenmemiş bir komşusu (ek çağrı yok).
                            near = [n for n in neighbors(space, x)
                                    if tuple(n.values()) not in seen and tuple(n.values()) not in keys]
                            if near:
                                n = rng.choice(near)
                                keys.add(tuple(n.values()))
                                queue.append((n, "llm_projected"))
                                self.meta["projected"] = self.meta.get("projected", 0) + 1
                    if queue:
                        break
                    if self.proposals == 1:
                        problem = (f"{labels.show(xs[0])} is equivalent to an already "
                                   f"evaluated configuration; choose a different one")
                    else:
                        problem = ("all proposed configurations are equivalent to already evaluated ones ("
                                   + "; ".join(labels.show(x) for x in xs[:5]) + "); propose different ones")
                elif self.proposals == 1:
                    self.meta["malformed"] += 1
                feedback.append(problem)
                if attempt < self.max_retries:
                    self.meta["retries"] += 1
            if not queue:                                        # LLM tekrar etmeye devam etti
                self.meta["fallbacks"] += 1
                unseen = [c for c in space.canonical_configs() if tuple(c.values()) not in seen]
                if not unseen:
                    return
                # LLM yönlendirmesine yakın kal: son önerisinin denenmemiş bir komşusu; yoksa rastgele.
                near = [n for n in (neighbors(space, last_proposal) if last_proposal else [])
                        if tuple(n.values()) not in seen]
                if near:
                    queue = [(rng.choice(near), "fallback_neighbor")]
                else:
                    self.meta["fallback_random"] = self.meta.get("fallback_random", 0) + 1
                    queue = [(rng.choice(unseen), "fallback_random")]
            for rank, (x, source) in enumerate(queue):
                if ev.budget_left is not None and ev.budget_left <= 0:
                    return
                decisions.append({"step": step, "rank": rank, "config": dict(x), "source": source,
                                  "rationale": rationale[:300] if rank == 0 else "",
                                  "feedback": feedback if rank == 0 else []})
                ev.evaluate(x)


def _map_config(cfg, ev: EvaluatorLike, labels: Labels) -> tuple[dict, str | None]:
    if not isinstance(cfg, Mapping):
        return {}, "each configuration must be an object"
    x = {}
    for s in ev.space.sites:
        lab = cfg.get(s.id)
        if lab not in labels.from_label[s.id]:
            return {}, f"site {s.id}: {lab!r} is not one of {labels.choices(s.id)}"
        x[s.id] = labels.from_label[s.id][lab]
    extra = set(cfg) - set(x)
    if extra:
        return {}, f"unknown site ids: {sorted(extra)}"
    return x, None


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
    x, problem = _map_config(cfg, ev, labels)
    return x, rationale, problem


def _parse_many(text: str, ev: EvaluatorLike, labels: Labels) -> tuple[list[dict], str, str | None, int]:
    """(geçerli öneriler, gerekçe, sorun, geçersiz öneri sayısı). Geçersiz öğeler atlanır."""
    try:
        data = json.loads(text)
        items, rationale = data["configs"], str(data.get("rationale", ""))
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], "", "response was not valid JSON with keys 'rationale' and 'configs'", 1
    if not isinstance(items, list) or not items:
        return [], rationale, "'configs' must be a non-empty array", 1
    out, problems = [], []
    for cfg in items:
        x, problem = _map_config(cfg, ev, labels)
        if problem is None:
            out.append(x)
        else:
            problems.append(problem)
    if not out:
        return [], rationale, "no valid configuration: " + "; ".join(problems[:3]), len(problems)
    return out, rationale, None, len(problems)


class RandomClient:
    """Maliyet tahmini için: şemaya uyan rastgele yanıtlar üretir, API'ye gitmez.

    Gerçek LLM'den daha sık tekrar önerebilir; bu yüzden yeniden sorma sayısı üst sınır gibi yorumlanmalı.
    """
    model = "random-estimator"
    settings: dict = {}

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def _config(self, schema: dict) -> dict:
        return {sid: self._rng.choice(p["enum"]) for sid, p in schema["properties"].items()}

    def complete(self, system: str, prompt: str, schema: dict, *, temperature: float, seed: int) -> LLMReply:
        props = schema["properties"]
        if "config" in props:
            data = {"rationale": "", "config": self._config(props["config"])}
        else:
            k = props["configs"]["maxItems"]
            data = {"rationale": "", "configs": [self._config(props["configs"]["items"]) for _ in range(k)]}
        return LLMReply(json.dumps(data))
