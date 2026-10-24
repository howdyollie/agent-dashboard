#!/usr/bin/env bash
# Runs the whole suite. Uses jsonschema for manifest validation only if it is installed.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 tests/test_all.py "$@"
