#!/usr/bin/env bash
# Espelha os 6 jobs de .github/workflows/ci.yml, na mesma ordem, para rodar
# localmente antes de dar push — motivado por uma sessão que só descobriu via
# CI (depois de já ter empurrado) que faltava `ruff format --check` (RFC 0019
# §2.5, mesma conveniência do `pink/scripts/run_ci.sh`).
#
# Não espelha o job "Version bump": ele só roda em PR e compara __version__
# contra o branch base via `github.base_ref` do GitHub Actions — não há um
# "branch base" genérico fora desse contexto.
set -euo pipefail

run() {
  echo "▶ $*"
  "$@"
}

run uv run --group dev ruff check src/
run uv run --group dev ruff format --check src/
run uv run --group dev ty check src/
run uv run --group dev vulture src/ --min-confidence 80
run uv run --group dev bandit -r src/ -ll --skip B101,B104 -x src/todos/sei_styles.py
run uv run --group dev pytest tests/ -v
run uv run --group dev pip-audit --ignore-vuln CVE-2025-69872

echo "✓ Todos os checks do CI passaram localmente."
