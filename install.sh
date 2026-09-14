#!/usr/bin/env bash
# Bootstrap real local editing sources and non-destructive host links.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$REPO/scripts/workflow.py" install "$@"
