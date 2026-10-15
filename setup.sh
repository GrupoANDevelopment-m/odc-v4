#!/usr/bin/env bash
# ODC v4 — one-command install
# Usage: ./setup.sh

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

banner() {
    echo ""
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}============================================================${NC}"
    echo ""
}

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; }

# === Detect ===
banner "ODC v4 setup"

# Python
if ! command -v python3 &> /dev/null; then
    err "python3 not found. Install Python 3.11+ first."
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ $(echo "$PY_VERSION < 3.11" | bc -l) == "1" ]]; then
    err "Python 3.11+ required, got $PY_VERSION"
    exit 1
fi
ok "Python $PY_VERSION"

# git
if ! command -v git &> /dev/null; then
    err "git not found"
    exit 1
fi
ok "git $(git --version | awk '{print $3}')"

# git-lfs
if ! command -v git-lfs &> /dev/null; then
    warn "git-lfs not found. Install it for large file support."
    warn "  macOS: brew install git-lfs"
    warn "  Linux: apt install git-lfs"
else
    git lfs install
    ok "git-lfs $(git-lfs --version | awk '{print $NF}')"
fi

# === Virtualenv ===
banner "Setting up virtual environment"

if [[ ! -d .venv ]]; then
    python3 -m venv .venv
    ok "Created .venv"
else
    ok ".venv already exists"
fi

# shellcheck source=/dev/null
source .venv/bin/activate
ok "Activated .venv ($(which python))"

# === Dependencies ===
banner "Installing dependencies"
pip install --upgrade pip setuptools wheel --quiet
ok "pip upgraded"

pip install -e ".[dev,test]" --quiet
ok "ODC v4 installed (editable)"

# === Playwright ===
banner "Installing Playwright browsers"
if command -v playwright &> /dev/null || python -m playwright --version &> /dev/null; then
    python -m playwright install chromium --with-deps
    ok "Chromium installed"
else
    warn "playwright not available, skipping browser install"
fi

# === .env ===
banner "Setting up .env"
if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        ok "Created .env from .env.example"
        warn "Edit .env and add your API keys before running tests"
    else
        warn ".env.example not found, skipping"
    fi
else
    ok ".env already exists"
fi

# === Pre-commit ===
banner "Setting up pre-commit"
if [[ -f .pre-commit-config.yaml ]]; then
    if command -v pre-commit &> /dev/null; then
        pre-commit install || warn "pre-commit install failed (non-fatal)"
        ok "pre-commit hooks installed"
    else
        warn "pre-commit not installed, skipping"
    fi
fi

# === Verify ===
banner "Verifying install"
python -c "import odc; print(f'ODC v4 {odc.__version__ if hasattr(odc, \"__version__\") else \"installed\"}')" 2>&1 || warn "odc import failed"

# === Final ===
banner "Setup complete"

cat <<'EOF'
Next steps:

  1. Edit .env and add your API keys (NVIDIA_API_KEY, etc)
  2. Run tests:    make test
  3. Run audit:    make audit
  4. Start server: make run
  5. Open web UI:  http://localhost:8000

For help: make help
EOF
