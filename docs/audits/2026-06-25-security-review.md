# Security Review — 2026-06-25

Revisão de segurança do estado atual da codebase `todos` (SEI MCP Server).

---

## CRÍTICO

### C-001 · TLS Verification Bypass habilitado via variável de ambiente

**Arquivos:** `src/todos/mcp_app.py:68-72`, `src/todos/sei_web_client.py:317-326`, `src/todos/sei_client.py:121-132`

`SEI_VERIFY_SSL=false` desabilita a verificação TLS em todos os clientes HTTP (REST e web scraper). Um `WARNING` no startup não é suficiente como controle. Um atacante na rede pode fazer MITM e interceptar credenciais, tokens de sessão e documentos.

**Ação:** Remover a opção de desabilitar TLS. Se o problema motivador for um certificado corporativo, aceitar `SEI_CA_BUNDLE=/path/to/ca.pem` em vez de um bypass booleano.

---

### C-002 · Credencial persistida temporariamente em cache SQLite

**Arquivo:** `src/todos/auth.py:566`

Em modo HTTP (OAuth), a senha (`sei_senha`) é submetida no formulário e armazenada no cache de auth codes (SQLite) durante a janela de 5 minutos. Um atacante com acesso ao arquivo de banco de dados nessa janela extrai a senha em texto claro.

**Ação:** Nunca persistir a senha. Trocar o mecanismo de repasse de credencial: em vez de colocar a senha no auth code, usar um nonce de sessão que o servidor já autenticado pode trocar internamente.

---

### C-003 · DNS Rebinding pode contornar o guard SSRF

**Arquivo:** `src/todos/auth.py:83-103`

O guard verifica se o IP pertence a ranges bloqueados (loopback, RFC-1918, link-local), mas comenta explicitamente que hostnames não são resolvidos: `# hostname DNS — não validamos aqui`. Um atacante controla um DNS que responde `8.8.8.8` na validação e `127.0.0.1` na requisição real, acessando serviços internos.

**Ação:** Resolver o hostname no momento da validação e bloquear o IP resolvido; ou re-validar o IP efetivo após a conexão via socket antes de processar a resposta.

---

## ALTO

### H-001 · Mensagens de erro expõem informações internas

**Arquivos:** `src/todos/mcp_app.py:393-397`, `src/todos/auth.py:618-619`

Mensagens como `"URL inesperada após login: {final_url}"` e exceções brutas retornadas em `sei_status` expõem estrutura interna (endpoints, cadeias de redirect) que auxiliam reconhecimento.

**Ação:** Logar detalhes completos internamente (`logger.error`) e retornar mensagem genérica ao chamador.

---

### H-002 · Estado de sessão não limpo completamente em retry de login

**Arquivo:** `src/todos/sei_web_client.py:586-593`

Quando credenciais são rejeitadas e o código tenta nova senha via keyring, campos de estado (`_inbox_url`, `_form_action`, `_trabalhar_links`) da tentativa anterior não são zerados. Reuso de estado parcialmente autenticado pode causar mistura de sessões em cenários de múltiplas credenciais.

**Ação:** Invocar um método `_reset_session_state()` que limpa todos os campos de estado antes de qualquer retry de login.

---

### H-003 · Correspondência de protocolo usa `in` em vez de igualdade estrita

**Arquivo:** `src/todos/sei_web_client.py:1020-1028`

`proto_norm in txt` é containment match: o protocolo `"00100"` pode casar com `"50100"` em texto livre. O usuário pode receber o processo errado sem aviso.

**Ação:** Usar `proto_norm == txt.strip()` ou regex com word boundary (`r"\b{proto_norm}\b"`).

---

## MÉDIO

### M-001 · Auth codes sem rate limiting nas tentativas de resgate

**Arquivo:** `src/todos/auth.py:283-329`

Auth codes com TTL de 5 minutos sem limitação de tentativas de adivinhação. Um atacante com acesso ao endpoint pode tentar exaustão, especialmente se o espaço for reduzido por implementação futura.

**Ação:** Adicionar rate limiting por IP/client_id no endpoint de resgate de auth code.

---

### M-002 · ReDoS em regex de parsing de label de documento

**Arquivo:** `src/todos/sei_web_client.py:1159-1160`

```python
re.sub(r"\s+N[ºo°]?\s+\S+.*$", "", tipo_text)
```

`.*$` sem âncora de tamanho pode causar backtracking catastrófico em inputs malformados vindos do servidor SEI.

**Ação:** Adicionar limite de tamanho antes da operação (`tipo_text[:200]`) e/ou usar `re.sub(..., count=1)`.

---

### M-003 · Parsing HTML sem limite de tamanho (potencial DoS)

**Arquivos:** `src/todos/sei_web_client.py:34`, `src/todos/html_utils.py:98`

BeautifulSoup recebe o HTML completo da resposta sem checar tamanho. Um servidor SEI comprometido poderia retornar um payload gigante causando consumo de memória.

**Ação:** Verificar `len(content)` antes de parsear e recusar se exceder limite razoável (ex.: 50 MB).

---

### M-004 · Condição de corrida no consumo de auth code

**Arquivo:** `src/todos/auth.py:321-329`

O auth code é removido do dict em memória com lock, mas se o processo reinicia entre consumo e emissão do token (crash, restart), o código foi consumido sem token emitido. O cliente que fizer retry receberá erro 400.

**Ação:** Persistir o estado de resgate em banco antes de emitir o token, com idempotency key `(client_id, code)`.

---

## BAIXO

### L-001 · Headers de segurança HTTP ausentes nas páginas de login

**Arquivo:** `src/todos/auth.py:524-593`

Respostas HTML do servidor OAuth local não incluem `X-Frame-Options`, `X-Content-Type-Options`, `Content-Security-Policy`. Risco de clickjacking ou CSS injection no ambiente de deploy.

**Ação:** Adicionar middleware que injeta os headers em todas as respostas HTTP do servidor.

---

### L-002 · keyring_user logado em falha de lookup de keyring

**Arquivos:** `src/todos/sei_web_client.py:431-434`, `src/todos/sei_client.py:265-269`

O identificador `usuario@hostname` é incluído na mensagem de log ao falhar o lookup do keyring. Em sistemas com logging centralizado, isso enumera usuários configurados.

**Ação:** Logar apenas `"keyring lookup failed"` sem o identificador do usuário.

---

### L-003 · Cache SQLite não criptografado

**Arquivo:** `src/todos/catalog_cache.py`

Auth codes, tokens e respostas cacheadas ficam em SQLite sem criptografia. Acesso ao sistema de arquivos expõe todos os dados.

**Ação:** Usar caminho em diretório com permissões restritas (`chmod 700`) e documentar o requisito. Criptografia em repouso (SQLCipher) é opcional mas recomendada para deployments multi-tenant.

---

### L-004 · OCR de PDF sem timeout global

**Arquivo:** `src/todos/html_utils.py:284, 297-304`

O loop de OCR processa até `MAX_OCR_PAGES` páginas sem timeout total. Um PDF malicioso ou muito grande pode travar o servidor indefinidamente.

**Ação:** Envolver a operação em `asyncio.wait_for(...)` com timeout configurável (padrão: 60s).

---

## INFO (não exploráveis no estado atual)

- **I-001** Path traversal: upload de arquivos valida via `Path.is_file()` antes de ler — ok.
- **I-002** Injeção de comando: nenhum `subprocess` com input não-sanitizado encontrado — ok.
- **I-003** XSS: HTML gerado usa `html.escape()` consistentemente em `access_control.py` e `auth.py` — ok.
- **I-004** Validação de filtro: parâmetro `filtro` em `server.py:112` validado contra regex `_RE_FILTRO_VALIDO` antes de usar — ok.
- **I-005** Log level: `TODOS_LOG_LEVEL=DEBUG` em produção exporia payloads; controlar via deploy config.

---

## Resumo

| Severidade | Qtd |
|------------|-----|
| Crítico    | 3   |
| Alto       | 3   |
| Médio      | 4   |
| Baixo      | 4   |
| Info       | 5   |

**Prioridade imediata:** C-001 (remover bypass TLS), C-002 (não persistir senha), C-003 (fix DNS rebinding no guard SSRF).
