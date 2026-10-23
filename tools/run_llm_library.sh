#!/bin/bash
# Kütüphanede LLM deneyleri (ground truth üzerinde tekrar oynatma; veritabanı gerekmez).
# Kapsam: 5 tohum, tam geçmiş, k = 5 öneri + izdüşüm, düşünme kapalı (LOW); iki koşul: adlar + ipucu, opak.
# Düşünme açıkken uzun geçmişli istemler aşırı düşünmeye (504/zaman aşımı) yol açtığı için kapalıdır.
# Kullanım: bash tools/run_llm_library.sh <iş yükü> [<iş yükü> ...]
# Aynı iş yükünü iki ayrı süreçte çalıştırmayın (ikisi aynı LLM önbellek dosyasına yazar).
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
[ -x "$PY" ] || PY=.venv/bin/python
export PYTHONIOENCODING=utf-8
# Bütçe oranları ve tohum sayısı ortam değişkeniyle: RHOS="0.1" SEEDS=10 bash tools/run_llm_library.sh ...
# Tohumlar 0..SEEDS-1; önbellekteki tohumlar API'ye gitmeden yeniden oynatılır.
read -r -a RHO_LIST <<< "${RHOS:-0.1 0.2}"
COMMON=(--algorithms llm --rhos "${RHO_LIST[@]}" --seeds "${SEEDS:-10}" --llm-history full --llm-proposals 5 --llm-project --llm-thinking-level LOW)
mkdir -p results/logs
for w in "$@"; do
  d=$(dirname "$(ls results/"$w"/*/space.json | head -1)")
  # Koşullar ortam değişkeniyle: CONDS="blind" bash tools/run_llm_library.sh ... (varsayılan: named opaque)
  for cond in ${CONDS:-named opaque}; do
    extra=(); [ "$cond" = opaque ] && extra=(--llm-opaque-labels)
    [ "$cond" = blind ] && extra=(--llm-history blind)
    echo "$(date '+%F %T') başladı: $w / $cond"
    if "$PY" -u -m plutosql.search compare --results "$d" "${COMMON[@]}" "${extra[@]}" \
         > "results/logs/llm_${w}_${cond}.log" 2>&1; then
      echo "$(date '+%F %T') bitti: $w / $cond ($(grep 'API çağrısı' "results/logs/llm_${w}_${cond}.log"))"
    else
      echo "$(date '+%F %T') HATA: $w / $cond (bkz. results/logs/llm_${w}_${cond}.log)"
    fi
  done
done
echo "$(date '+%F %T') tümü tamamlandı"
