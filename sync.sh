#!/usr/bin/env bash
# Preview publication changes; --apply asserts approval of that preview.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$REPO/scripts/workflow.py" sync "$@"
