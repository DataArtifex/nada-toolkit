#!/usr/bin/env bash
set -euo pipefail

# Use the active environment when available (monorepo/shared-env workflows).
# Fall back to project-local uv behavior when no environment is active.
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  exec uv run --active "$@"
fi

exec uv run "$@"
