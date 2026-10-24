#!/usr/bin/env bash
# docker compose do mcp-seipro com o arquivo certo: compose.sh ps, compose.sh logs app...
set -euo pipefail
exec docker compose --project-name mcp-seipro --project-directory /srv/mcp-seipro/deploy \
  -f /srv/mcp-seipro/deploy/docker-compose.yml "$@"
