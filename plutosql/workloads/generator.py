"""Parametreli iş yükü üreteci (ADR-004).

Üretilen blok bir aşama zinciridir:

    SRC ──aşama 1──► T1 ──aşama 2──► T2 … ──(son aşama UPSERT ise)──► TK (grp bazında toplam)

- INSERT aşaması: girdi tablosunu (seçicilik filtresiyle) satır satır okur, aşamaya atanmış her referans
  tablosundan bir arama yapar (P2), bir değer hesaplar ve çıktı tablosuna ekler (P1).
- UPSERT aşaması (yalnızca son aşama): girdiyi grp'ye göre toplar ve hedefi günceller / ekler (P1, upsert).

Kontrol edilen faktörler:
- referans tablosu: satır sayısı, anahtar tipi, satır genişliği (pad), anahtar yayılımı (spread: kaynak
  satırlarının referans tablosunun ne kadarına eriştiği) → önbelleğin okuma / bellek ödünleşimi (H3)
- aşama sayısı, seçicilik, aynı referans tablosunun birden çok aşamada aranması → kuplaj (H2)
- kaynak satır sayısı → ölçek

Veri ORA_HASH ile deterministiktir; her arama anahtarı referans tablosunda bulunur (NO_DATA_FOUND yok).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .package import WorkloadPackage


@dataclass(frozen=True)
class RefSpec:
    rows: int
    spread: float = 1.0                 # kaynak anahtarlarının kapsadığı referans oranı (0, 1]
    key_type: str = "NUMBER"            # NUMBER | VARCHAR2
    pad: int = 0                        # satır genişliği (bayt) — tablonun blok sayısını büyütür


@dataclass(frozen=True)
class StageSpec:
    kind: str = "INSERT"                # INSERT | UPSERT (yalnızca son aşama)
    lookups: tuple[int, ...] = ()       # aranan referans tablolarının indeksleri
    selectivity: int = 100              # girdi satırlarının yüzde kaçı işlenir (MOD(id,100) < s)


@dataclass(frozen=True)
class GeneratedSpec:
    name: str
    description: str
    n_src: int
    refs: tuple[RefSpec, ...]
    stages: tuple[StageSpec, ...]
    n_groups: int = 200
    seed_fraction: int = 50             # UPSERT hedefinde önceden var olan grp yüzdesi
    salt: int = 0                       # aynı yapıda farklı veri

    def validate(self) -> None:
        if not self.stages:
            raise ValueError("en az bir aşama gerekli")
        for i, st in enumerate(self.stages):
            if st.kind not in ("INSERT", "UPSERT"):
                raise ValueError(f"aşama {i + 1}: bilinmeyen tür {st.kind}")
            if st.kind == "UPSERT" and (i != len(self.stages) - 1 or st.lookups):
                raise ValueError("UPSERT yalnızca son aşama olabilir ve arama içeremez")
            if not 0 < st.selectivity <= 100:
                raise ValueError("seçicilik 1..100 olmalı")
            for r in st.lookups:
                if not 0 <= r < len(self.refs):
                    raise ValueError(f"aşama {i + 1}: geçersiz referans indeksi {r}")
            if len(set(st.lookups)) != len(st.lookups):
                raise ValueError(f"aşama {i + 1}: aynı referans iki kez aranamaz")
        for j, ref in enumerate(self.refs):
            if not 0 < ref.spread <= 1 or ref.rows < 1 or ref.key_type not in ("NUMBER", "VARCHAR2"):
                raise ValueError(f"referans {j + 1}: geçersiz tanım")

    def canonical_size(self) -> int:
        """Beklenen |𝒳/~|: INSERT aşaması (L arama) → 4·2^L + 1 (SET_BASED soğurur); UPSERT → 5."""
        n = 1
        for st in self.stages:
            n *= 5 if st.kind == "UPSERT" else 4 * 2 ** len(st.lookups) + 1
        return n


# ================================================================ üretim

def generate(spec: GeneratedSpec) -> WorkloadPackage:
    spec.validate()
    p = spec.name.upper()
    src, refs = f"{p}_SRC", [f"{p}_R{j + 1}" for j in range(len(spec.refs))]
    outs = [f"{p}_T{i + 1}" for i in range(len(spec.stages))]
    upsert_last = spec.stages[-1].kind == "UPSERT"
    seeded = {outs[-1]: f"{outs[-1]}_SEED"} if upsert_last else {}

    return WorkloadPackage(
        name=spec.name, prefix=p, description=spec.description,
        block=_block(spec, src, refs, outs),
        schema_sql=_schema(spec, src, refs, outs, seeded),
        data_sql=_data(spec, src, refs, outs, seeded),
        mutable=outs, immutable=[src, *refs], seeded=seeded,
        binds={}, spec=_spec_dict(spec),
    )


def _spec_dict(spec: GeneratedSpec) -> dict:
    d = asdict(spec)
    d["expected_canonical_size"] = spec.canonical_size()
    return d


def _key_type(ref: RefSpec) -> str:
    return "NUMBER(9)" if ref.key_type == "NUMBER" else "VARCHAR2(12)"


def _row_columns(spec: GeneratedSpec) -> list[tuple[str, str]]:
    cols = [("ID", "NUMBER(12) NOT NULL"), ("GRP", "NUMBER(9) NOT NULL")]
    cols += [(f"K{j + 1}", _key_type(ref)) for j, ref in enumerate(spec.refs)]
    cols += [("AMOUNT", "NUMBER(18,2)")]
    return cols


def _schema(spec, src, refs, outs, seeded) -> str:
    stmts = []
    row_cols = _row_columns(spec)

    def row_table(name: str) -> str:
        body = ",\n".join(f"  {c:<8} {t}" for c, t in row_cols)
        return f"CREATE TABLE {name} (\n{body},\n  CONSTRAINT {name}_PK PRIMARY KEY (ID)\n)"

    stmts.append(row_table(src))
    for name, ref in zip(refs, spec.refs):
        pad = f",\n  PAD      VARCHAR2({ref.pad})" if ref.pad else ""
        stmts.append(f"CREATE TABLE {name} (\n  K        {_key_type(ref)} NOT NULL,\n"
                     f"  V        NUMBER(12,6){pad},\n  CONSTRAINT {name}_PK PRIMARY KEY (K)\n)")
    for st, name in zip(spec.stages, outs):
        if st.kind == "INSERT":
            stmts.append(row_table(name))
        else:
            stmts.append(f"CREATE TABLE {name} (\n  GRP      NUMBER(9) NOT NULL,\n  TOTAL    NUMBER(20,2),\n"
                         f"  CNT      NUMBER(12),\n  CONSTRAINT {name}_PK PRIMARY KEY (GRP)\n)")
    for table, seed in seeded.items():
        stmts.append(f"CREATE TABLE {seed} AS SELECT * FROM {table} WHERE 1 = 0")
    return "-- Üretilmiş iş yükü şeması: " + spec.name + "\n" + "\n/\n".join(stmts) + "\n/\n"


def _key_expr(ref: RefSpec, n: str) -> str:
    """Sayısal anahtar n (1..rows) için kolon değeri."""
    return n if ref.key_type == "NUMBER" else f"'K' || TO_CHAR({n})"


def _data(spec, src, refs, outs, seeded) -> str:
    h = lambda expr, s: f"ORA_HASH({expr}, 4294967295, {s + 1000 * spec.salt})"   # noqa: E731
    stmts = []
    for j, (name, ref) in enumerate(zip(refs, spec.refs)):
        pad = f", RPAD('x', {ref.pad}, 'x')" if ref.pad else ""
        cols = "K, V" + (", PAD" if ref.pad else "")
        stmts.append(
            f"INSERT /*+ APPEND */ INTO {name} ({cols})\n"
            f"SELECT {_key_expr(ref, 'LEVEL')}, ROUND(0.5 + MOD({h('LEVEL', 10 + j)}, 1000000) / 1000000, 6){pad}\n"
            f"  FROM dual CONNECT BY LEVEL <= {ref.rows}")
        stmts.append("COMMIT")

    keys = []
    for j, ref in enumerate(spec.refs):
        span = max(1, round(ref.rows * ref.spread))
        keys.append(_key_expr(ref, f"1 + MOD({h('LEVEL', 20 + j)}, {span})"))
    key_cols = "".join(f", K{j + 1}" for j in range(len(spec.refs)))
    key_vals = "".join(f",\n       {k}" for k in keys)
    stmts.append(
        f"INSERT /*+ APPEND */ INTO {src} (ID, GRP{key_cols}, AMOUNT)\n"
        f"SELECT LEVEL,\n       1 + MOD({h('LEVEL', 1)}, {spec.n_groups}){key_vals},\n"
        f"       ROUND(MOD({h('LEVEL', 2)}, 1000000) / 100, 2)\n"
        f"  FROM dual CONNECT BY LEVEL <= {spec.n_src}\n ORDER BY 1")
    stmts.append("COMMIT")

    for table, seed in seeded.items():
        stmts.append(
            f"INSERT /*+ APPEND */ INTO {seed} (GRP, TOTAL, CNT)\n"
            f"SELECT LEVEL, ROUND(MOD({h('LEVEL', 3)}, 100000) / 100, 2), 0\n"
            f"  FROM dual\n WHERE MOD({h('LEVEL', 4)}, 100) < {spec.seed_fraction}\n"
            f"CONNECT BY LEVEL <= {spec.n_groups}")
        stmts.append("COMMIT")
    return "-- Üretilmiş iş yükü verisi (deterministik): " + spec.name + "\n" + "\n/\n".join(stmts) + "\n/\n"


def _block(spec: GeneratedSpec, src, refs, outs) -> str:
    key_cols = [f"k{j + 1}" for j in range(len(spec.refs))]
    decl, body = [], []
    for i, st in enumerate(spec.stages, start=1):
        inp = src if i == 1 else outs[i - 2]
        c, r = f"c_s{i}", f"r_s{i}"
        where = "" if st.selectivity == 100 else f"\n     WHERE MOD(id, 100) < {st.selectivity}"
        if st.kind == "INSERT":
            cols = ", ".join(["id", "grp", *key_cols, "amount"])
            decl.append(f"  CURSOR {c} IS\n    SELECT {cols}\n      FROM {inp}{where};")
        else:
            decl.append(f"  CURSOR {c} IS\n    SELECT grp, SUM(amount) AS total, COUNT(*) AS cnt\n"
                        f"      FROM {inp}{where}\n     GROUP BY grp;")
        decl.append(f"  {r} {c}%ROWTYPE;")
        for j in st.lookups:
            decl.append(f"  l_s{i}_v{j + 1} {refs[j]}.v%TYPE;")
        if st.kind == "INSERT":
            decl.append(f"  l_s{i}_amt NUMBER;")

        body.append(f"  -- Aşama {i}: {inp} → {outs[i - 1]} ({st.kind}"
                    + (f", arama: {', '.join(refs[j] for j in st.lookups)}" if st.lookups else "") + ")")
        body.append(f"  OPEN {c};\n  LOOP\n    FETCH {c} INTO {r};\n    EXIT WHEN {c}%NOTFOUND;\n")
        if st.kind == "INSERT":
            for j in st.lookups:
                body.append(f"    SELECT v INTO l_s{i}_v{j + 1} FROM {refs[j]} WHERE k = {r}.k{j + 1};")
            factor = " * ".join(f"l_s{i}_v{j + 1}" for j in st.lookups) or "1.01"
            body.append(f"    l_s{i}_amt := ROUND({r}.amount * {factor}, 2);\n")
            vals = ", ".join([f"{r}.id", f"{r}.grp", *[f"{r}.{k}" for k in key_cols], f"l_s{i}_amt"])
            body.append(f"    INSERT INTO {outs[i - 1]} ({', '.join(['id', 'grp', *key_cols, 'amount'])})\n"
                        f"    VALUES ({vals});")
            body.append("    l_rows := l_rows + 1;")
        else:
            t = outs[i - 1]
            body.append(f"    UPDATE {t} t\n       SET t.total = t.total + {r}.total, t.cnt = t.cnt + {r}.cnt\n"
                        f"     WHERE t.grp = {r}.grp;\n")
            body.append(f"    IF SQL%ROWCOUNT = 0 THEN\n      INSERT INTO {t} (grp, total, cnt)\n"
                        f"      VALUES ({r}.grp, {r}.total, {r}.cnt);\n    END IF;")
        body.append(f"  END LOOP;\n  CLOSE {c};\n")

    return (f"-- Üretilmiş iş yükü: {spec.name}\n-- {spec.description}\n"
            "DECLARE\n" + "\n".join(decl) + "\n  l_rows PLS_INTEGER := 0;\nBEGIN\n"
            + "\n".join(body) + "  COMMIT;\nEND;\n/\n")


def static_catalog(spec: GeneratedSpec):
    """Üretilen şemanın StaticCatalog karşılığı (veritabanı olmadan site analizi için)."""
    from plutosql.transformer.catalog import StaticCatalog

    p = spec.name.upper()
    row = {c: t for c, t in _row_columns(spec)}
    tables = {f"{p}_SRC": row}
    for j, ref in enumerate(spec.refs):
        cols = {"K": f"{_key_type(ref)} NOT NULL", "V": "NUMBER(12,6)"}
        if ref.pad:
            cols["PAD"] = f"VARCHAR2({ref.pad})"
        tables[f"{p}_R{j + 1}"] = cols
    for i, st in enumerate(spec.stages, start=1):
        tables[f"{p}_T{i}"] = row if st.kind == "INSERT" else {
            "GRP": "NUMBER(9) NOT NULL", "TOTAL": "NUMBER(20,2)", "CNT": "NUMBER(12)"}
    return StaticCatalog(tables)
