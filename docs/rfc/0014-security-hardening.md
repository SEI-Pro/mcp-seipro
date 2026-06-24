# RFC 0014 — Security Hardening do servidor todos MCP

**Status:** 🔴 Proposta (aguardando implementação)
**Data:** 2026-06-23
**Autor:** auditoria automatizada + revisão manual

---

## 1. Contexto

Auditoria de segurança do código-fonte do `todos` identificou 11 vulnerabilidades.
Todas estão no próprio código do `todos`; nenhuma depende de correção no SEI.

Este RFC delimita a responsabilidade, prioriza as correções e define a
implementação esperada para cada uma.

---

## 2. Delimitação: todos vs. SEI

### 2.1 Problemas do `todos` (este RFC)

| # | Severidade | Local | Descrição curta |
|---|-----------|-------|-----------------|
| 1 | **CRÍTICA** | `auth.py:435` | XSS — `{session}` não escapado na página de login |
| 2 | **CRÍTICA** | `auth.py:491` | XSS — `{redirect_uri}` não escapado na página de sucesso |
| 3 | **ALTA** | `access_control.py:268` | `SEI_RISCOS_EXTRA` injetado como HTML cru sem escape |
| 4 | **ALTA** | `sei_client.py:150` | Propriedade pública `senha` expõe password em texto plano |
| 5 | **ALTA** | `sei_client.py:292` | Payload de autenticação (inclui senha) logado em `ERROR` |
| 6 | **ALTA** | `tools/documentos.py:754` | `arquivo_path` sem validação de caminho em modo stdio |
| 7 | **MÉDIA** | `auth.py:448` | SSRF — `sei_url`/`sei_web_url` aceitos sem validação de destino |
| 8 | **MÉDIA** | `auth.py` | Registro de cliente OAuth aberto (sem autenticação) |
| 9 | **MÉDIA** | `auth.py:59` | Token sem revogação; TTL de 30 dias |
| 10 | **BAIXA** | `access_control.py:46` | `SEI_PERMITIR_RESTRITOS=true` ignora gate sem log de auditoria |
| 11 | **BAIXA** | `sei_web_client.py` | TLS desabilitável via `SEI_VERIFY_SSL=false` sem aviso no startup |

### 2.2 Limitações do SEI (fora do escopo do `todos`)

Estes itens dependem da infraestrutura do SEI; o `todos` não pode corrigi-los,
apenas mitigá-los ou documentá-los:

- **Autenticação básica usuario/senha** — o mod-wssei v2 e o frontend PHP não
  oferecem 2FA/MFA. O `todos` não pode forçar o SEI a adotá-lo.
- **Sessão baseada em `infra_hash`** — a validade da sessão web depende do tempo
  de vida do cookie SIP do PHP; o `todos` apenas reutiliza o hash enquanto válido.
- **TLS no próprio servidor SEI** — instâncias podem operar em HTTP puro; o
  `todos` expõe `SEI_VERIFY_SSL=false` para compatibilidade, mas a insegurança
  é da instância SEI, não do `todos`.
- **Ausência de rate-limit nas APIs do SEI** — o SEI não implementa throttling
  por token; o `todos` não tem como impô-lo ao servidor de destino.
- **Expiração de sessão não sinalizada** — o PHP do SEI retorna HTTP 200 com
  redirect para login ao invés de 401; o `todos` já detecta esse padrão e
  re-autentica.

---

## 3. Correções: especificação por prioridade

### P0 — Injeção de HTML (um-liner cada; implementar imediatamente)

#### 3.1 XSS na página de login OAuth (`auth.py:435`)

**Problema:** `session` vem de `request.query_params` e é inserido diretamente
no template HTML sem escape. Um atacante que controle o parâmetro `?session=`
pode injetar JavaScript executado no navegador do usuário.

```python
# ANTES (vulnerável)
session = request.query_params.get("session", "")
return HTMLResponse(_LOGIN_HTML.replace("{session}", session))

# DEPOIS
from html import escape as _html_escape  # já importado
return HTMLResponse(_LOGIN_HTML.replace("{session}", _html_escape(session)))
```

#### 3.2 XSS na página de sucesso OAuth (`auth.py:491`)

**Problema:** `redirect_uri` é construída a partir de dados do fluxo OAuth e
inserida no HTML sem escape. Um `redirect_uri` com `"` ou `<` quebraria o
atributo HTML e permitiria injeção.

```python
# ANTES (vulnerável)
page = _SUCCESS_HTML.replace("{redirect_uri}", str(redirect_uri)).replace(...)

# DEPOIS
page = _SUCCESS_HTML.replace("{redirect_uri}", _html_escape(str(redirect_uri))).replace(...)
```

#### 3.3 `SEI_RISCOS_EXTRA` injetado como HTML (`access_control.py:268`)

**Problema:** `SEI_RISCOS_EXTRA` é lida do ambiente e os itens (separados por
`|`) são inseridos como `<li>{r}</li>` sem escape. Uma var de ambiente maliciosa
ou acidentalmente mal-formada pode injetar HTML no disclaimer enviado ao modelo.

```python
# ANTES (em envelopar_html)
riscos_html = "".join(f"<li>{r}</li>" for r in disclaimer["riscos"])

# DEPOIS
import html
riscos_html = "".join(f"<li>{html.escape(r)}</li>" for r in disclaimer["riscos"])
```

Mesmo raciocínio se aplica a `hl_html` quando `hipotese_legal` vem da API do SEI:

```python
# ANTES
hl_html = f"<p><strong>Hipótese legal:</strong> {hl}</p>" if hl else ""

# DEPOIS
hl_html = f"<p><strong>Hipótese legal:</strong> {html.escape(hl)}</p>" if hl else ""
```

---

### P1 — Vazamento de credenciais

#### 3.4 Propriedade pública `senha` em `SEIClient` (`sei_client.py:150`)

**Problema:** A propriedade pública `senha` retorna a senha em texto plano,
ficando disponível para qualquer código que tenha referência ao cliente.
Isso é especialmente perigoso quando o cliente está no pool de sessões
compartilhado em `mcp_app.py`.

**Correção:** Remover a propriedade `senha` do `SEIClient`. O único consumidor
legítimo é `sei_assinar_documento` (que precisa da senha para gerar credenciais
PKI). Injetar a senha diretamente via parâmetro no momento do uso.

```python
# Remover de sei_client.py:
@property
def senha(self) -> str:
    """Senha do usuário (necessária para assinar documentos)."""
    return self._senha  # expõe credencial a qualquer chamador

# Nos locais que usam client.senha: passar self._senha internamente
# (acesso dentro da própria classe) ou receber via parâmetro de método.
```

Se o acesso interno à classe ainda for necessário, usar `self._senha`
diretamente — não expor como propriedade pública.

#### 3.5 Payload de autenticação logado em ERROR (`sei_client.py:268,292`)

**Problema:** O payload de autenticação REST inclui `"senha": self._senha`.
Se o log capturar uma linha de `ERROR`, a senha aparece em texto plano nos logs.

```python
# ANTES (sei_client.py ~268)
payload = {"login": self._usuario, "senha": self._senha, ...}
# ... request fails ...
logger.error("Payload de autenticação sem campo 'token': %r", payload)

# DEPOIS — logar apenas as chaves, nunca os valores
logger.error(
    "Payload de autenticação sem campo 'token': chaves=%r",
    list(payload.keys()),
)
```

#### 3.6 `arquivo_path` sem validação de caminho em modo stdio (`tools/documentos.py:754`)

**Problema:** Em modo stdio o parâmetro `arquivo_path` é aceito sem validação.
Um modelo comprometido ou teste malicioso poderia solicitar
`arquivo_path="/etc/passwd"` e exfiltrar o conteúdo como "documento SEI".

**Correção:** Restringir `arquivo_path` a extensões conhecidas de documento
e, opcionalmente, a um diretório de base configurável via `SEI_UPLOAD_DIR`.

```python
_EXTENSOES_PERMITIDAS = {".pdf", ".doc", ".docx", ".xls", ".xlsx",
                          ".odt", ".ods", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

def _validar_arquivo_path(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    if p.suffix.lower() not in _EXTENSOES_PERMITIDAS:
        msg = f"Extensão não permitida: {p.suffix}. Permitidas: {sorted(_EXTENSOES_PERMITIDAS)}"
        raise SEIValidationError(msg)
    upload_dir = os.environ.get("SEI_UPLOAD_DIR", "")
    if upload_dir:
        base = Path(upload_dir).expanduser().resolve()
        try:
            p.relative_to(base)
        except ValueError as e:
            msg = f"arquivo_path deve estar dentro de SEI_UPLOAD_DIR ({base})."
            raise SEIValidationError(msg) from e
    return p
```

---

### P2 — Superfície de ataque OAuth

#### 3.7 SSRF via `sei_url`/`sei_web_url` (`auth.py:448`)

**Problema:** O formulário de login OAuth aceita qualquer URL para o SEI sem
validação do destino. Um atacante que induza um usuário a submeter o formulário
com `sei_url=http://169.254.169.254/latest/meta-data/` pode usar o servidor
`todos` como proxy para acessar redes internas.

**Mitigação recomendada:** Bloquear URLs com endereços IP privados/loopback
e esquemas não-HTTPS.

```python
import ipaddress
from urllib.parse import urlparse

_ESQUEMAS_PERMITIDOS = {"https"}
_REDES_BLOQUEADAS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / IMDS AWS
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

def _validar_url_sei(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ESQUEMAS_PERMITIDOS:
        msg = f"Esquema não permitido: {parsed.scheme!r}. Use HTTPS."
        raise SEIValidationError(msg)
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        for net in _REDES_BLOQUEADAS:
            if addr in net:
                msg = f"Endereço IP bloqueado: {host}"
                raise SEIValidationError(msg)
    except ValueError:
        pass  # hostname DNS — não resolve aqui para evitar DNS rebinding
```

**Nota:** `SEI_VERIFY_SSL=false` continua sendo uma opção legítima para instâncias
com certificado auto-assinado (SEI-RO etc.) — mas deve emitir `logger.warning` no
startup, não ser silencioso.

#### 3.8 Registro de cliente OAuth aberto (`auth.py`)

**Problema:** O endpoint `/register` aceita qualquer client sem autenticação.
Isso permite que qualquer entidade registre clientes e tente usar o fluxo OAuth.

**Mitigação:** A spec MCP exige suporte a dynamic client registration para
interoperabilidade com Claude.ai e outros hosts. Contudo, é possível adicionar
uma camada de validação:

- Aceitar apenas `redirect_uri` com esquema `https://` (exceto `localhost` em dev).
- Logar cada registro com o IP do chamador (`request.client.host`).
- Opcionalmente, exigir `REGISTRATION_TOKEN` no header para restringir a clientes
  pré-aprovados (quebra a interoperabilidade genérica — avaliar o tradeoff).

#### 3.9 Token sem revogação; TTL de 30 dias (`auth.py:59`)

**Problema:** `TOKEN_TTL = 86400 * 30` emite tokens válidos por 30 dias sem
possibilidade de revogação. Se um token vazar, o servidor não tem como invalidá-lo.

**Correção:** Implementar lista de revogação em SQLite (mesma infra do `CatalogCache`).

```python
async def revoke_token(token: str) -> None:
    cache = get_catalog_cache()
    # Armazena assinatura truncada (64 chars) com TTL = TOKEN_TTL
    sig = token.split(".")[-1][:64]
    await cache.set({"module": "auth"}, f"revoked:{sig}", {"at": time.time()}, ttl=TOKEN_TTL)

async def is_token_revoked(token: str) -> bool:
    cache = get_catalog_cache()
    sig = token.split(".")[-1][:64]
    return await cache.get({"module": "auth"}, f"revoked:{sig}") is not None
```

Chamar `is_token_revoked` dentro de `verify_token` antes de retornar o `AccessToken`.

---

### P3 — Auditoria e observabilidade

#### 3.10 `SEI_PERMITIR_RESTRITOS=true` sem log de auditoria (`access_control.py:46`)

**Problema:** Quando `SEI_PERMITIR_RESTRITOS=true`, o gate de acesso é pulado
silenciosamente. Não há registro de qual documento restrito foi acessado, por
qual sessão, quando.

**Correção:** Adicionar `logger.warning` no caminho de bypass:

```python
def avaliar_acesso(...) -> tuple[Decisao, dict | None]:
    ...
    if confirmou or env_permite_restritos():
        if env_permite_restritos():
            logger.warning(
                "SEI_PERMITIR_RESTRITOS: acesso irrestrito a %s (nivel=%s)",
                alvo, nivel,
            )
        return "liberar", construir_disclaimer_acompanhante(nivel, hipotese_legal, alvo)
```

#### 3.11 TLS desabilitável sem aviso (`sei_web_client.py`, `sei_client.py`)

**Problema:** `SEI_VERIFY_SSL=false` desabilita verificação TLS sem nenhum aviso
no startup. Em produção isso pode passar despercebido.

**Correção:** Em `mcp_app.py`, no lifespan, emitir `logger.warning` se a variável
estiver ativa:

```python
if os.environ.get("SEI_VERIFY_SSL", "true").lower() in ("false", "0", "no"):
    logger.warning(
        "SEI_VERIFY_SSL=false: verificação TLS desabilitada. "
        "Não use em produção com dados sensíveis."
    )
```

---

## 4. O que NÃO muda

- **Senha SEI em SQLite durante OAuth (5 min):** A senha precisa sobreviver ao
  `_store_auth_code → exchange_authorization_code` (dois requests separados).
  O TTL de 5 minutos e o uso único do auth code já limitam a janela de exposição.
  Criptografar com `JWT_SECRET` seria possível mas aumenta a complexidade para
  ganho marginal dado o TTL curto. Mantém-se documentado como risco aceito.

- **Design single-tenant da senha (`SEI_SENHA` env var):** O servidor Railway/HTTP
  é single-tenant por design — uma instância, um usuário. Múltiplos usuários exigem
  múltiplas instâncias. Isso é uma decisão arquitetural documentada no README, não
  uma vulnerabilidade do código.

---

## 5. Plano de implementação

| Fase | Issues | Esforço | Impacto |
|------|--------|---------|---------|
| **Fase 1** | #1, #2, #3 (XSS + HTML injection) | ~15 min | Elimina injeção de código |
| **Fase 2** | #4, #5 (credenciais em log/property) | ~1h | Elimina vazamento de credencial |
| **Fase 3** | #6 (arquivo_path) | ~1h | Elimina path traversal em stdio |
| **Fase 4** | #10, #11 (auditoria/TLS warn) | ~30 min | Observabilidade |
| **Fase 5** | #7, #8, #9 (OAuth hardening) | ~3h | Reduz superfície OAuth |

Fases 1–4 não têm breaking changes e podem ser implementadas em um único PR.
Fase 5 requer teste de ponta-a-ponta do fluxo OAuth.

---

## 6. Testes esperados

- `tests/test_auth_xss.py` — verificar que `?session=<script>alert(1)</script>`
  aparece escapado no HTML retornado.
- `tests/test_access_control_html.py` — verificar que `SEI_RISCOS_EXTRA` com
  `<b>risco</b>` aparece como `&lt;b&gt;risco&lt;/b&gt;` no HTML.
- `tests/test_documentos_path.py` — verificar que `arquivo_path=/etc/passwd`
  lança `SEIValidationError` em modo stdio.
- `tests/test_auth_revoke.py` — verificar que token revogado retorna 401.
