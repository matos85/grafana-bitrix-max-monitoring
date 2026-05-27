#!/bin/sh
set -eu

GRAFANA_URL="${GRAFANA_URL:-http://grafana:3000}"
ADMIN_USER="${GF_ADMIN_USER:-admin}"
ADMIN_PASS="${GF_ADMIN_PASSWORD:-changeme}"
VIEWER_USER="${GF_VIEWER_USER:-viewer}"
VIEWER_PASS="${GF_VIEWER_PASSWORD:-viewer}"
VIEWER_EMAIL="${GF_VIEWER_EMAIL:-viewer@local}"

fix_prometheus_datasource_uid() {
  ds_json=$(curl -sf -u "${auth}" "${GRAFANA_URL}/api/datasources/name/Prometheus" 2>/dev/null) || return 0
  ds_id=$(printf '%s' "${ds_json}" | grep -o '"id":[0-9]*' | head -1 | grep -o '[0-9]*')
  current_uid=$(printf '%s' "${ds_json}" | grep -o '"uid":"[^"]*"' | head -1 | sed 's/"uid":"//;s/"$//')
  if [ -z "${ds_id}" ] || [ "${current_uid}" = "prometheus" ]; then
    echo "Prometheus datasource uid OK (${current_uid:-prometheus})"
    return 0
  fi
  curl -sf -u "${auth}" -H "Content-Type: application/json" \
    -X PUT "${GRAFANA_URL}/api/datasources/${ds_id}" \
    -d "$(printf '%s' "${ds_json}" | sed 's/"uid":"[^"]*"/"uid":"prometheus"/')" \
    && echo "Prometheus datasource uid fixed: ${current_uid} -> prometheus" \
    || echo "Warning: could not update Prometheus datasource uid"
}

echo "Waiting for Grafana at ${GRAFANA_URL}..."
for _ in $(seq 1 60); do
  if curl -sf "${GRAFANA_URL}/api/health" -o /dev/null; then
    break
  fi
  sleep 2
done
sleep 2

auth="${ADMIN_USER}:${ADMIN_PASS}"

fix_prometheus_datasource_uid

curl -sf -u "${auth}" -H "Content-Type: application/json" \
  -X PUT "${GRAFANA_URL}/api/org/preferences" \
  -d '{"language":"ru-RU","homeDashboardUID":"home"}' \
  && echo "Org preferences: ru-RU, home=home" \
  || echo "Org preferences update skipped"

curl -sf -u "${auth}" -H "Content-Type: application/json" \
  -X POST "${GRAFANA_URL}/api/admin/users" \
  -d "{\"name\":\"Monitoring Viewer\",\"email\":\"${VIEWER_EMAIL}\",\"login\":\"${VIEWER_USER}\",\"password\":\"${VIEWER_PASS}\",\"OrgId\":1}" \
  && echo "Created user: ${VIEWER_USER}" \
  || echo "User ${VIEWER_USER} already exists or create skipped"

users_json=$(curl -sf -u "${auth}" "${GRAFANA_URL}/api/org/users")
user_id=$(printf '%s' "${users_json}" | tr ',' '\n' | grep -B3 "\"login\":\"${VIEWER_USER}\"" | grep '"userId"' | head -1 | grep -o '[0-9][0-9]*')

if [ -n "${user_id}" ]; then
  curl -sf -u "${auth}" -H "Content-Type: application/json" \
    -X PATCH "${GRAFANA_URL}/api/org/users/${user_id}" \
    -d '{"role":"Viewer"}' \
    && echo "Role Viewer set for ${VIEWER_USER} (id=${user_id})"
else
  echo "Warning: could not find ${VIEWER_USER} in org users"
fi

echo "Local Grafana accounts (from .env):"
echo "  ${ADMIN_USER} / ${ADMIN_PASS}"
echo "  ${VIEWER_USER} / ${VIEWER_PASS}"
