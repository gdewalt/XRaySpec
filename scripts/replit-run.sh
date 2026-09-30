#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/backend"

required=(
  XRAY_DATABASE_URL
  XRAY_SUPABASE_PROJECT_URL
  XRAY_SUPABASE_ANON_KEY
  XRAY_SUPABASE_SERVICE_KEY
  XRAY_ALLOWED_EMAILS
)

missing=()
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done

if (( ${#missing[@]} )); then
  printf 'Missing required deployment secrets: %s\n' "${missing[*]}" >&2
  exit 1
fi

alembic upgrade head

port="${PORT:-3000}"
export XRAY_FRONTEND_DIST_DIR="${XRAY_FRONTEND_DIST_DIR:-$repo_root/frontend/dist}"

python -m app.worker.run &
worker_pid=$!

uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$port" \
  --proxy-headers \
  --forwarded-allow-ips='*' &
api_pid=$!

shutdown() {
  kill "$api_pid" "$worker_pid" 2>/dev/null || true
  wait "$api_pid" "$worker_pid" 2>/dev/null || true
}
trap shutdown EXIT INT TERM

wait -n "$api_pid" "$worker_pid"
status=$?
echo "A required process exited; stopping deployment." >&2
exit "$status"

