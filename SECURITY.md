# Política de Segurança — mcp-seipro

## Escopo

Este servidor MCP conversa com o SEI (Sistema Eletrônico de Informações) em
nome do usuário. Ele lê e escreve dados de processos, documentos e usuários,
autentica em duas frentes (API REST mod-wssei v2 e sessão web SIP) e, no modo
HTTP remoto, opera um provider OAuth 2.1 com tokens criptografados que
carregam as credenciais SEI no payload.

Isso quer dizer que uma falha aqui pode expor:

- **credenciais do órgão** (SEI_USUARIO/SEI_SENHA, tokens de terceiros no
  header `SEI_EXTRA_HEADERS`, cookies `SEI_CF_CLEARANCE`);
- **dados restritos** de processos administrativos, inclusive quando
  `SEI_PERMITIR_RESTRITOS=true` for aceito;
- **rede interna** do órgão que hospedar o modo HTTP, via SSRF na URL do SEI
  informada por usuário público.

O repositório já implementa defesas específicas para esses vetores em
`src/mcp_seipro/seguranca.py` (validação de host, bloqueio de IPs privados,
segredos ligados ao host de origem) e `src/mcp_seipro/auth.py` (tokens
AES-256-GCM, rotação de refresh com queima em reuso, teto absoluto de sessão,
revogação). Os testes em `tests/test_seguranca_http.py` documentam os vetores
já cobertos.

## Versões suportadas

Recebem correção de segurança:

| Versão | Suportada |
|--------|-----------|
| 0.6.x  | ✅        |
| < 0.6  | ❌ (formato antigo de token com senha legível) |

Tokens no formato pré-0.6 (`base64 + HMAC`) **não são aceitos** — o servidor
os rejeita mesmo em instâncias já atualizadas.

## Como reportar uma vulnerabilidade

Prefira o canal privado do GitHub:

1. Abra <https://github.com/sei-pro/mcp-seipro/security/advisories/new>.
2. Descreva o vetor, o impacto e um passo a passo mínimo de reprodução.
3. Se possível, aponte o commit ou o trecho de `src/mcp_seipro/*` envolvido.

Peço confirmação de recebimento em até **7 dias corridos** e um primeiro
diagnóstico em até **30 dias**. Correções críticas (RCE, vazamento de
credenciais, bypass de OAuth) recebem prioridade e podem ser lançadas fora
do calendário normal de release.

## O que **não** é vulnerabilidade

Antes de reportar, considere:

- **DNS rebinding sem `SEI_ALLOWED_HOSTS` configurado** — o próprio README
  recomenda `SEI_ALLOWED_HOSTS` em produção; sem ele, a defesa contra
  rebinding é parcial por escolha operacional, não por falha do código.
- **SSRF para IP público arbitrário sem `SEI_ALLOWED_HOSTS`** — mesmo caso.
- **Vazamento em log local do modo stdio** — logs locais são responsabilidade
  do operador; o modo stdio não é multiusuário.
- **Comportamento do SEI upstream** (mod-wssei, SIP, WAF) — reportar ao
  [`pengovbr/mod-wssei`](https://github.com/pengovbr/mod-wssei) ou ao órgão
  responsável pela instância.

## Boas práticas para quem opera o servidor

- Em modo HTTP, gere `JWT_SECRET` com pelo menos 32 bytes de entropia
  (`python -c 'import secrets; print(secrets.token_hex(48))'`) e trate-o
  como segredo de produção.
- Restrinja `SEI_ALLOWED_HOSTS` ao(s) domínio(s) do seu órgão. Padrão como
  `*.gov.br` já é largo demais para produção multi-inquilino.
- Nunca coloque as variáveis `SEI_SENHA`, `JWT_SECRET`, `SEI_EXTRA_HEADERS`
  ou `SEI_CF_CLEARANCE` em `.mcp.json` versionado em git.
- No SEI de produção com dados de pessoa natural, mantenha
  `SEI_PERMITIR_RESTRITOS=false` (padrão). O flag existe para operação
  administrativa consciente, não como default.

## Divulgação coordenada

Vulnerabilidades corrigidas são divulgadas via GitHub Security Advisory
após o release contendo a correção. Peço um período razoável (**até 90
dias** a partir do reporte) antes de qualquer publicação.
