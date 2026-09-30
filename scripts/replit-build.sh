#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python -m pip install --disable-pip-version-check -e "$repo_root/backend"
npm --prefix "$repo_root/frontend" ci --no-audit --no-fund
npm --prefix "$repo_root/frontend" run build

test -f "$repo_root/frontend/dist/index.html"

