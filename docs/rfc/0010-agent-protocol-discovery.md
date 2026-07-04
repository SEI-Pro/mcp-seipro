# RFC 0010 — Descoberta Automática do Formato de Protocolo pelo Agente

**Status**: ❌ Revogada (2026-07-04) — substituída por regex fixo com fallback permissivo, sem descoberta nem keyring. Ver `changelog/0.6.14.md` e RFC 0017.  
**Atualizado**: 2026-06-18  
**Data**: 2026-06-18  
**Autores**: Franklin Baldo (com Claude Code)  
**RFCs relacionados**: RFC 0002 (armazenamento seguro via keyring), RFC 0008 (structured output / schema constraints)

> **Nota de revogação**: a infraestrutura descrita neste RFC (keyring com
> `ThreadPoolExecutor`+timeout, cache global por host, tools
> `sei_detectar_formato_protocolo`/`sei_redefinir_formato_protocolo`) foi
> removida — desproporcional ao problema real, que afeta só 3 das ~28 tools
> que recebem `protocolo_formatado`. A validação agora é um regex fixo com
> dois formatos conhecidos (SEI administrativo e CNJ) tentados em sequência,
> com fallback permissivo obrigatório para formatos não mapeados. Ver
> `src/todos/tools/configuracao.py` e `changelog/0.6.14.md`. O texto abaixo é
> mantido só como histórico de design.

---

## 1. Problema

O número de processo SEI segue o padrão `NNNNN.NNNNNN/YYYY-NN`, mas o comprimento do prefixo de órgão (os `N` iniciais) **varia entre instâncias**:

| Instância | Prefixo | Exemplo |
|---|---|---|
| ANTAQ (federal) | 5 dígitos | `50302.001234/2024-01` |
| SEI-RO (estadual) | 4 dígitos | `0007.001234/2024-01` |
| Outros órgãos | 4–6 dígitos | — |

O RFC 0008 introduziu `Annotated[str, Field(pattern=...)]` para rejeitar entradas malformadas em tempo de chamada de tool. O padrão ficou configurável via `SEI_PROTOCOLO_PATTERN` (env var, definida no wizard de setup), com fallback para `str` sem restrição quando não configurado.

Isso resolve o problema de _validação_, mas não o de _descoberta_: o usuário ainda precisa saber o regex correto da sua instância antes de configurar o MCP server. Para usuários não técnicos esse passo é uma barreira de adoção.

**Requisito de UX**: o agente (Claude, Cursor, etc.) que usa o MCP server deveria ser capaz de descobrir o formato automaticamente, armazenar a configuração de forma persistente e aplicá-la nas sessões seguintes — sem que o usuário precise conhecer o regex.

---

## 2. Proposta

### 2.1 Visão geral

```
Primeira sessão:
  agente chama sei_detectar_formato_protocolo()
    → lê processos reais da caixa de entrada web
    → infere regex a partir dos números encontrados
    → salva no keyring: key="SEI_PROTOCOLO_PATTERN@<host>"
    → retorna o padrão descoberto

Sessões seguintes:
  bootstrap.py / sei_backend.py
    → lê keyring["SEI_PROTOCOLO_PATTERN@<host>"] antes da env var
    → instancia _ProtocoloFormatado com o padrão persistido
    → tool calls validam automaticamente sem nova descoberta
```

### 2.2 Nova tool: `sei_detectar_formato_protocolo`

```python
async def sei_detectar_formato_protocolo() -> dict:
    """Detecta o formato do número de processo desta instância SEI.

    Lê os processos reais da caixa de entrada, extrai os números de protocolo,
    infere o regex e persiste no keyring para uso automático nas sessões futuras.
    Retorna o padrão detectado e o número de amostras usadas.
    """
```

**Algoritmo de inferência:**

1. Chamar `sei_listar_processos` (ou o método interno equivalente) para obter ≥ 10 números de protocolo reais.
2. Parsear cada número com o regex canônico `^(\d+)\.(\d+)/(\d{4})-(\d{2})$`.
3. Calcular `min_len` e `max_len` do grupo de prefixo entre todas as amostras.
4. Se `min_len == max_len`: padrão fixo `^\d{N}\.…`
5. Se divergência ≤ 1 dígito: padrão de intervalo `^\d{N,M}\.…`
6. Validar com `TypeAdapter(Annotated[str, Field(pattern=inferido)])` (motor Pydantic/Rust).
7. Persistir no keyring e retornar resultado estruturado.

**Resposta:**

```json
{
  "padrao": "^\\d{5}\\.\\d{6}/\\d{4}-\\d{2}$",
  "amostras": 15,
  "prefixo_min": 5,
  "prefixo_max": 5,
  "persistido": true,
  "chave_keyring": "SEI_PROTOCOLO_PATTERN@sei.antaq.gov.br"
}
```

### 2.3 Resolução de configuração (ordem de precedência)

```
keyring["SEI_PROTOCOLO_PATTERN@<host>"]   ← persiste entre sessões, escrito pela tool
  ↓ (ausente ou timeout)
os.environ["SEI_PROTOCOLO_PATTERN"]       ← configurado manualmente / wizard
  ↓ (ausente ou vazio)
str  (sem restrição)                       ← fallback seguro atual
```

A leitura do keyring ocorre **uma única vez por processo**, no startup, com timeout defensivo de 2 s via `concurrent.futures.ThreadPoolExecutor` (mesmo padrão usado em RFC 0002 para `SEI_SENHA`).

### 2.4 Leitura defensiva no startup

```python
# src/todos/tools/processos.py  (módulo-level, executado no import)
import concurrent.futures, keyring, urllib.parse

def _read_keyring_pattern(host: str) -> str:
    key = f"SEI_PROTOCOLO_PATTERN@{host}"
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(keyring.get_password, "todos-mcp", key)
            return future.result(timeout=2.0) or ""
    except Exception:  # noqa-free: timeout, KeyringError, etc.
        return ""

_host = urllib.parse.urlparse(os.environ.get("SEI_WEB_URL", "")).netloc
_SEI_PROTOCOLO_PATTERN = (
    _read_keyring_pattern(_host)
    or os.environ.get("SEI_PROTOCOLO_PATTERN", "")
)
_ProtocoloFormatado = (
    Annotated[str, Field(pattern=_SEI_PROTOCOLO_PATTERN)]
    if _SEI_PROTOCOLO_PATTERN
    else str
)
```

> **Nota sobre import-time e FastMCP**: `_ProtocoloFormatado` precisa ser avaliado no import porque FastMCP introspecta as anotações de tipo no momento em que o módulo é carregado. A leitura defensiva com timeout garante que uma falha de keyring não impeça o servidor de iniciar.

### 2.5 Tool para limpar/redefinir o padrão

```python
async def sei_redefinir_formato_protocolo() -> dict:
    """Remove o padrão de protocolo persistido no keyring.

    Use quando a instância SEI for migrada ou o padrão detectado estiver incorreto.
    Na próxima chamada a sei_detectar_formato_protocolo() a descoberta recomeça do zero.
    """
```

---

## 3. Justificativas de design

### 3.1 Keyring como store de configuração derivada

RFC 0002 usa keyring para _credenciais_ (segredo fornecido pelo usuário). Este RFC propõe usá-lo para _configuração derivada_ (inferida automaticamente). Ambos são candidatos naturais ao keyring porque:

- São específicos ao host/usuário (não devem ser versionados no repo).
- Precisam sobreviver ao restart do processo (não podem ficar só em memória).
- Não devem exigir edição manual de arquivos de configuração pelo usuário.

A chave `SEI_PROTOCOLO_PATTERN@<host>` é não-sensível (é um regex, não uma senha), portanto a escrita no keyring é uma conveniência, não um requisito de segurança.

### 3.2 Por que não salvar no arquivo de config MCP?

O `mcp_config.json` (Claude Desktop) e o `claude_mcp_settings.json` (VS Code) são gerenciados pelo cliente MCP, não pelo servidor. O server não tem acesso de escrita a esses arquivos em runtime, especialmente no modelo stdio onde o servidor é um subprocesso do cliente.

### 3.3 Por que não usar um arquivo local (ex: `~/.todos/config.json`)?

Keyring resolve o problema de localização cross-platform (`$HOME` vs `%APPDATA%` vs `$XDG_CONFIG_HOME`) sem lógica adicional. Já está como dependência (RFC 0002). Não há motivo para introduzir uma segunda camada de persistência.

### 3.4 Fallback seguro sem keyring

Em ambientes sem keyring funcional (containers, CI, Railway), a leitura retorna `""` após timeout, e a cadeia cai para `SEI_PROTOCOLO_PATTERN` env var ou `str` sem restrição — exatamente o comportamento atual. A feature é aditiva; não quebra nada.

---

## 4. Plano de implementação

| Fase | Mudança | Arquivo(s) |
|---|---|---|
| 1 | Leitura keyring no startup com fallback em cadeia | `src/todos/tools/processos.py` |
| 2 | Nova tool `sei_detectar_formato_protocolo` | `src/todos/tools/processos.py` |
| 3 | Nova tool `sei_redefinir_formato_protocolo` | `src/todos/tools/processos.py` |
| 4 | Atualizar `manifest.json` com as 2 novas tools | `manifest.json` |
| 5 | Testes unitários do algoritmo de inferência | `tests/test_parsers.py` |
| 6 | Hint de domínio sugerindo uso da tool na primeira sessão | `src/todos/hints.py` |

**Dependências de fase**: 1 deve preceder 2 (2 escreve o valor que 1 lê). 3–6 são independentes entre si.

---

## 5. Considerações de UX para o agente

A hint sugerida para `_DEFAULT_HINTS` (Fase 6):

```
"Na primeira sessão com uma nova instância SEI, chame sei_detectar_formato_protocolo para configurar automaticamente a validação de números de processo."
```

Fluxo esperado de uma primeira sessão:

1. Agente lista processos → recebe lista com `protocolo_formatado` sem validação (str).
2. Agente (ou instrução de sistema) chama `sei_detectar_formato_protocolo`.
3. Tool infere padrão, persiste, retorna confirmação.
4. Nas sessões seguintes, o padrão é carregado no startup → entradas malformadas são rejeitadas em tempo de chamada.

---

## 6. Riscos e mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Caixa de entrada vazia na primeira sessão | Baixa | Fallback: listar processos de outra unidade; ou retornar erro orientando o usuário a abrir um processo antes |
| Instância com prefixos inconsistentes (migração de sistema) | Muito baixa | Algoritmo usa intervalo quando `min ≠ max`; `sei_redefinir_formato_protocolo` permite redescoberta |
| keyring bloqueado (GUI prompt no macOS sem contexto gráfico) | Média em ambientes headless | Timeout de 2 s garante degradação graciosa |
| Padrão inferido incorreto (amostra pequena) | Baixa | Exige ≥ 10 amostras; alerta se < 10; `sei_redefinir_formato_protocolo` permite redescoberta |
| Mudança de formato após upgrade do SEI | Muito baixa | `sei_redefinir_formato_protocolo` + nova descoberta |

---

## 7. Não está no escopo deste RFC

- Descoberta automática de outros parâmetros de instância (ex: tipos de processo padrão).
- Sincronização entre múltiplos clientes/máquinas do mesmo usuário.
- Interface gráfica no wizard para acionar a descoberta interativamente (pode ser adicionado ao `setup_wizard.py` em RFC futuro).
