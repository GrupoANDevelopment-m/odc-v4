#!/usr/bin/env bash
# ODC v4 — Docker entrypoint
set -euo pipefail

cmd="${1:-server}"

case "$cmd" in
    server|daemon)
        echo "Starting ODC v4 server..."
        exec python -m odc.server
        ;;
    cli)
        shift
        exec python -m odc.cli "$@"
        ;;
    repl)
        exec python -m odc.repl
        ;;
    test)
        shift
        exec python -m pytest "$@"
        ;;
    audit)
        exec python -m pytest tests/test_cognitive_suite.py tests/test_audit_adversarial.py tests/test_deep_audit.py -q -p no:cacheprovider
        ;;
    shell)
        exec bash
        ;;
    *)
        exec "$cmd" "$@"
        ;;
esac
