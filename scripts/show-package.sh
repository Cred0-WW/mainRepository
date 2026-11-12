#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3}"
export NEXUS_HOST="${NEXUS_HOST:-nexus}"
"$PYTHON_BIN" scripts/package_ci.py tools
"$PYTHON_BIN" scripts/package_ci.py build
"$PYTHON_BIN" scripts/package_ci.py package
"$PYTHON_BIN" scripts/package_ci.py verify
