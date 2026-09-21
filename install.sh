#!/usr/bin/env bash
# ODC v4 — one-shot installer.
#
# Creates a venv, installs the package + the LLM providers + the web
# tools, copies .env.example to .env if missing. Nothing fancy.
#
# Usage:
#   ./install.sh                  # core + web + anthropic + openai
#   ./install.sh --no-openai      # core + web + anthropic
#   ./install.sh --local          # core + web + ollama (no API keys)
#   ./install.sh --full           # everything including browser + reach
set -euo pipefail

INSTALL_OPENAI=1
INSTALL_ANTHROPIC=1
INSTALL_OLLAMA=0
INSTALL_WEB=1
INSTALL_BROWSER=0
INSTALL_REACH=0
INSTALL_DEV=1

for arg in "$@"; do
  case "$arg" in
    --no-openai)     INSTALL_OPENAI=0 ;;
    --no-anthropic)  INSTALL_ANTHROPIC=0 ;;
    --no-web)        INSTALL_WEB=0 ;;
    --no-dev)        INSTALL_DEV=0 ;;
    --local)         INSTALL_OPENAI=0; INSTALL_ANTHROPIC=0; INSTALL_OLLAMA=1 ;;
    --full)          INSTALL_BROWSER=1; INSTALL_REACH=1; INSTALL_OLLAMA=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# Pick a python.
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found; install Python 3.10+ first" >&2
  exit 1
fi

VENV="${VENV:-.venv}"
if [ ! -d "$VENV" ]; then
  echo "→ creating venv at $VENV"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip wheel setuptools >/dev/null

EXTRAS=()
[ "$INSTALL_WEB" -eq 1 ]      && EXTRAS+=("web")
[ "$INSTALL_ANTHROPIC" -eq 1 ] && EXTRAS+=("anthropic")
[ "$INSTALL_OPENAI" -eq 1 ]   && EXTRAS+=("openai")
[ "$INSTALL_BROWSER" -eq 1 ]  && EXTRAS+=("browser")
[ "$INSTALL_REACH" -eq 1 ]    && EXTRAS+=("reach")
[ "$INSTALL_DEV" -eq 1 ]      && EXTRAS+=("dev")

EXTRA_STR=""
if [ ${#EXTRAS[@]} -gt 0 ]; then
  EXTRA_STR="[${EXTRAS[0]}$(printf ',%s' "${EXTRAS[@]:1}")]"
fi

echo "→ installing odc$EXTRA_STR"
pip install -e ".$EXTRA_STR"

if [ ! -f .env ]; then
  echo "→ creating .env from .env.example (fill in your keys)"
  cp .env.example .env
fi

echo
echo "✅ install complete. Next steps:"
echo "   source $VENV/bin/activate"
echo "   odc doctor                 # check config + keys"
echo "   odc 'what is 2+2?'         # one-shot task"
echo "   odc repl                   # interactive REPL"
