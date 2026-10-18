#!/bin/bash
# Kütüphanenin exhaustive ground truth ölçümü. Kesilirse yeniden çalıştırın: ölçüm deposu kaldığı yerden devam eder.
# Kullanım: bash tools/run_library.sh [iş yükü ...]   (verilmezse kütüphanedeki tümü)
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
[ -x "$PY" ] || PY=.venv/bin/python
export PYTHONIOENCODING=utf-8
mkdir -p results/logs
WORKLOADS=("$@")
[ ${#WORKLOADS[@]} -eq 0 ] && WORKLOADS=($("$PY" -c "from plutosql.workloads.package import available; print(' '.join(available()))"))
for w in "${WORKLOADS[@]}"; do
  echo "$(date '+%F %T') başladı: $w"
  if "$PY" -m plutosql.workloads setup "$w" > "results/logs/$w.setup.log" 2>&1 \
     && "$PY" -u -m plutosql.harness --workload "$w" exhaustive > "results/logs/$w.log" 2>&1; then
    echo "$(date '+%F %T') bitti: $w ($(grep -c '' "results/logs/$w.log") satır log)"
  else
    echo "$(date '+%F %T') HATA: $w (bkz. results/logs/$w*.log)"
  fi
done
echo "$(date '+%F %T') tümü tamamlandı"
