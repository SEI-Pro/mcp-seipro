# Deploy do mcp-seipro (instância oficial)

Produção em **https://mcp.seipro.io**, na VPS Hostinger KVM4 (`ssh hostinger-kvm4`,
187.127.54.251, Campinas). Saiu do Railway em 2026-10-24.

**Não rode `railway up` para o projeto `mcp-seipro` do Railway** (conta Lab2Code): o serviço
de lá virou o proxy de transição (`deploy/railway-proxy/`), e um upload do código o
substituiria por um segundo servidor com outro `JWT_SECRET`. O guia de Railway do README da
raiz é para quem quer hospedar uma instância própria.

## Arranjo

| Item | Onde |
| --- | --- |
| Usuário de sistema | `mcpseipro` (grupo `docker`) |
| Segredo | `/srv/mcp-seipro/env/app.env` (600): `JWT_SECRET` e as variáveis do modo HTTP |
| Código em produção | `/srv/mcp-seipro/app` (commit em `COMMIT`); o anterior em `app.anterior` |
| Clone de onde sai o código | `/home/pedro/mcp-seipro` (clone https do repositório público) |
| Compose e scripts | `/srv/mcp-seipro/deploy`, copiados de `deploy/` a cada publicação |
| Pilha | projeto compose `mcp-seipro`: um contêiner (1280 MB) em `127.0.0.1:8140` |
| Borda | Caddy, vhost em `Caddyfile.mcp-seipro` (sem Authelia: OAuth próprio) |
| DNS | `seipro.io` na Cloudflare (desde 2026-10-24): `mcp` é `A 187.127.54.251`, sem proxy |

Sem banco, sem volume e sem backup: o estado OAuth (clientes registrados e sessões) vai
selado nos próprios tokens com chave derivada do `JWT_SECRET` (ver `src/mcp_seipro/auth.py`).
**Trocar o `JWT_SECRET` desconecta todos os usuários**, que precisam reconectar o conector.
Foi trocado na migração, porque o do Railway vazou no incidente de 21/10/2026.

A imagem leva o Chromium do Playwright (`INSTALL_BROWSER=true`): com `SEI_TRANSPORT=auto` o
servidor escala para o browser quando o SEI está atrás do desafio da Cloudflare. No Railway o
pico em 30 dias foi 884 MB, daí o teto de 1280 MB e o `shm_size` de 256 MB.

## Operação (na VPS, como root)

```
sudo -u pedro git -C /home/pedro/mcp-seipro pull --ff-only   # traz o commit novo
/srv/mcp-seipro/deploy/deploy.sh [commit]                   # publica (padrão: HEAD do clone)
/srv/mcp-seipro/deploy/compose.sh ps | logs app             # docker compose com o arquivo certo
```

O healthcheck usa `GET /`, que responde 200 sem login. O Caddy substitui o
`X-Forwarded-For` e o uvicorn confia nele (`FORWARDED_ALLOW_IPS=*`; a porta só escuta em
127.0.0.1), então o log do app mostra o IP real do cliente.

Mudou o Caddyfile? `systemctl restart caddy` — o `reload` falha calado nesta VPS
(`admin off`) e deixa a configuração antiga no ar.

## Transição no Railway

`railway-proxy/` roda no serviço `mcp-seipro` do Railway desde 2026-10-24 (publicado com
`railway up . --path-as-root` de dentro da pasta). Com Host `mcp.seipro.io` (quem ainda tem o
CNAME antigo em cache) repassa tudo para a VPS pelo nome auxiliar `mcp-seipro.claudinha.tech`;
com qualquer outro Host (`mcp-seipro-production.up.railway.app`) responde 308 para
`https://mcp.seipro.io`. Proxy e não redirect porque cliente MCP e `POST /token` não seguem
redirect de forma confiável.
