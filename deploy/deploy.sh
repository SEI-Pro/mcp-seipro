#!/usr/bin/env bash
# Publica o mcp-seipro: extrai um commit do /home/pedro/mcp-seipro, reconstrói a imagem e
# reinicia o app. Rodar na VPS como root:  /srv/mcp-seipro/deploy/deploy.sh [commit]  (padrão: HEAD)
#
# Mesmo desenho do deploy do legis/lastro/bia: se o build falhar nada muda; a imagem em uso
# fica como mcp-seipro-app:anterior para voltar em segundos (ver mensagem de falha abaixo).
#
# A fonte destes arquivos é deploy/ no repositório. A cada publicação, o compose e os
# scripts do commit são copiados para /srv/mcp-seipro/deploy, para o que roda não se descolar
# do que está versionado. Exceção: este próprio script (o bash o lê enquanto executa) —
# se ele mudou, a publicação avisa e ele é copiado à mão.
set -euo pipefail
repo=/home/pedro/mcp-seipro
base=/srv/mcp-seipro
C="$base/deploy/compose.sh"
G=(git -c safe.directory="$repo" -C "$repo")
commit="${1:-HEAD}"
sha="$("${G[@]}" rev-parse --short "$commit")"

rm -rf "$base/app.novo" && mkdir -p "$base/app.novo"
"${G[@]}" archive --format=tar "$sha" | tar -x -C "$base/app.novo"
echo "$sha" > "$base/app.novo/COMMIT"
chown -R mcpseipro:mcpseipro "$base/app.novo"

id_de() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }
anterior_velha="$(id_de mcp-seipro-app:anterior)"
[ -n "$(id_de mcp-seipro-app:atual)" ] && docker tag mcp-seipro-app:atual mcp-seipro-app:anterior
if ! docker build --build-arg INSTALL_BROWSER=true -t mcp-seipro-app:atual "$base/app.novo"; then
  [ -n "$anterior_velha" ] && docker tag "$anterior_velha" mcp-seipro-app:anterior
  echo "ERRO: build falhou; nada mudou ($base/app.novo fica para inspeção)" >&2
  exit 1
fi

rm -rf "$base/app.anterior"
[ -d "$base/app" ] && mv "$base/app" "$base/app.anterior"
mv "$base/app.novo" "$base/app"
install -o mcpseipro -g mcpseipro -m 640 "$base/app/deploy/docker-compose.yml" "$base/deploy/"
install -o mcpseipro -g mcpseipro -m 750 "$base/app/deploy/compose.sh" "$base/deploy/"
if ! cmp -s "$base/app/deploy/deploy.sh" "$base/deploy/deploy.sh"; then
  echo "AVISO: deploy.sh do commit difere do instalado; copie à mão:" >&2
  echo "  install -o mcpseipro -g mcpseipro -m 750 $base/app/deploy/deploy.sh $base/deploy/" >&2
fi
if ! "$C" up -d --no-build --wait --wait-timeout 180 app; then
  "$C" ps
  cat >&2 <<FALHA
ERRO: app não ficou healthy em 180 s (versão $sha). Logs: $C logs --since 10m app
Volta rápida (sem build):
  cd $base && rm -rf app.novo && mv app app.novo && mv app.anterior app
  docker tag mcp-seipro-app:anterior mcp-seipro-app:atual && $C up -d --no-build app
FALHA
  exit 1
fi
"$C" ps
atual="$(id_de mcp-seipro-app:atual)"; anterior="$(id_de mcp-seipro-app:anterior)"
if [ -n "$anterior_velha" ] && [ "$anterior_velha" != "$atual" ] && [ "$anterior_velha" != "$anterior" ]; then
  docker image rm "$anterior_velha" || true
fi
echo "publicado: $sha"
