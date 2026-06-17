"""Hierarquia base de exceções do servidor SEI.

Categorias amplas que toda tool captura via `except (SEIError, …)`. Os erros
**específicos de cada domínio** (ex.: documento já assinado, processo concluído)
NÃO ficam aqui: cada arquivo de domínio do backend define os seus, fazendo
subclass da categoria apropriada abaixo e levantando-os com
`try/except … raise XxxError(...) from e` no método que conhece o contexto.

Todas as subclasses carregam mensagem legível por humanos — nunca exponha stack
traces httpx ou strings técnicas diretamente ao agente.
"""

from __future__ import annotations

import json

from fastmcp.exceptions import ToolError


class SEIError(ToolError):
    """Erro base do servidor SEI.

    Subclasse de `ToolError`: o FastMCP entrega a mensagem de um `ToolError`
    diretamente ao agente (exceções comuns são mascaradas como "internal error").
    Por isso as tools simplesmente deixam o `SEIError` propagar — sem
    `_to_tool_error`, sem tradução e sem reembrulho. A orientação acionável vem
    na mensagem que o cliente/scraper coloca ao levantar o erro (o SEI já devolve
    um texto explicativo); quem só precisa da categoria captura por TIPO
    (`except SEIAuthError`, `SEINotFoundError`, …).

    Os atributos `error_code`, `recoverable`, `suggested_next_tool` e
    `suggested_args` enriquecem o objeto para logging server-side e adoção futura
    de structuredContent (RFC 0008). Enquanto isso, `_render` embute a continuação
    na string da mensagem — único canal que sobrevive ao transporte MCP (o SDK
    serializa apenas `str(exception)` em `CallToolResult.content[0].text`).
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "",
        recoverable: bool = False,
        suggested_next_tool: str | None = None,
        suggested_args: dict | None = None,
    ) -> None:
        """Inicializa com mensagem e metadados de recuperação opcionais."""
        self.error_code = error_code
        self.recoverable = recoverable
        self.suggested_next_tool = suggested_next_tool
        self.suggested_args = suggested_args or {}
        super().__init__(self._render(message))

    def _render(self, message: str) -> str:
        """Embute a continuação na string da mensagem se houver sugestão de próxima tool."""
        if not self.suggested_next_tool:
            return message
        args = json.dumps(self.suggested_args, ensure_ascii=False, separators=(",", ":"))
        return (
            f"{message}\n\nPróximo passo sugerido: chame `{self.suggested_next_tool}` com {args}."
        )


class SEIAuthError(SEIError):
    """Sessão expirada, login recusado, 401/403."""


class SEICredenciaisError(SEIAuthError):
    """Credenciais rejeitadas pelo SEI (usuário/senha incorretos).

    Diferente de SEIAuthError genérico: indica especificamente que o servidor
    recebeu as credenciais mas as recusou — senha errada, usuário inválido ou
    órgão incorreto. Carrega orientação acionável sobre onde e como corrigir.
    """


class SEICaptchaError(SEIAuthError):
    """Login bloqueado por CAPTCHA ou 2FA — o scraper não prossegue (auth manual)."""


class SEINotFoundError(SEIError):
    """Processo ou documento não existe no SEI."""


class SEIPermissionError(SEIError):
    """Acesso negado — documento restrito/sigiloso sem credenciamento."""


class SEIConnectionError(SEIError):
    """Falha de rede, timeout, instância inacessível."""


class SEIParseError(SEIError):
    """HTML da resposta não tem a estrutura esperada."""


class SEIValidationError(SEIError):
    """Parâmetros inválidos detectados antes de qualquer chamada HTTP."""


class SEINotImplementedError(SEIError):
    """Operação não suportada pelo backend ativo (ex: REST-only sem mod-wssei)."""


# ── Erros de domínio específicos ─────────────────────────────────────────────
# Levantados NA ORIGEM (cliente/scraper, ao ler a resposta do SEI) via
# `erro_do_sei`. Não há re-tradução a jusante: quem trata captura por TIPO.


class SEIDocumentoNaoAutorizadoError(SEIPermissionError):
    """Acesso ao documento negado pelo SEI — id interno vs número SEI, ou permissão."""


class SEIDocumentoAssinadoError(SEIValidationError):
    """Documento assinado e travado para edição (processo já lido/enviado)."""


class SEIProcessoEmOutraUnidadeError(SEIValidationError):
    """Processo aberto/tramitando em outra unidade."""


def erro_do_sei(contexto: str, mensagem: str | None) -> SEIError:
    """Cria o `SEIError` específico para uma mensagem de erro do SEI.

    Classifica a mensagem UMA vez, na origem (onde o cliente/scraper lê a
    resposta do SEI), e devolve o tipo específico para ser levantado ali —
    `raise erro_do_sei(...)`. Não é tradução a jusante: ninguém recaptura um
    `SEIError` para re-tipá-lo. Mensagens sem condição conhecida viram `SEIError`.
    """
    texto = (mensagem or "").strip()
    low = texto.lower()
    detalhe = f"{contexto}: {texto}" if texto else contexto
    if "não autorizado" in low or "nao autorizado" in low or "acesso negado" in low:
        return SEIDocumentoNaoAutorizadoError(detalhe)
    if "assinad" in low:
        return SEIDocumentoAssinadoError(detalhe)
    if "aberto" in low and "unidade" in low:
        return SEIProcessoEmOutraUnidadeError(detalhe)
    return SEIError(detalhe)
