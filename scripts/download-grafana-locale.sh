#!/bin/sh
# Скачивает ru-RU/grafana.json для монтирования в Grafana.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/grafana/locales/ru-RU/grafana.json"
URL="${GRAFANA_LOCALE_URL:-https://raw.githubusercontent.com/grafana/grafana/main/public/locales/ru-RU/grafana.json}"

mkdir -p "$(dirname "$OUT")"
curl -fsSL "$URL" -o "$OUT"
echo "Saved $(wc -c <"$OUT") bytes -> $OUT"
