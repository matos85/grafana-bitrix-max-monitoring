#!/bin/sh
# Отправка в GitHub. Учётные данные — из .github-credentials (не коммитится).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CREDS="${ROOT}/.github-credentials"
REMOTE="${GITHUB_REMOTE:-https://github.com/matos85/grafana-bitrix-max-monitoring.git}"
BRANCH="${GITHUB_BRANCH:-main}"

if [ ! -f "${CREDS}" ]; then
  echo "Файл ${CREDS} не найден."
  echo "  cp .github-credentials.example .github-credentials"
  echo "  # укажите GITHUB_USER и GITHUB_TOKEN (Personal Access Token)"
  exit 1
fi

# shellcheck disable=SC1090
. "${CREDS}"

USER="${GITHUB_USER:-}"
TOKEN="${GITHUB_TOKEN:-}"

if [ -z "${USER}" ] || [ -z "${TOKEN}" ]; then
  echo "В .github-credentials задайте GITHUB_USER и GITHUB_TOKEN"
  exit 1
fi

cd "${ROOT}"
git remote set-url origin "${REMOTE}"
echo "Push ${BRANCH} → ${REMOTE} (user: ${USER})"
git -c "http.extraHeader=Authorization: Basic $(printf '%s' "${USER}:${TOKEN}" | base64 | tr -d '\n')" \
  push -u origin "${BRANCH}"
