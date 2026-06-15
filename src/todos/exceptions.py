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
    """


class SEIAuthError(SEIError):
    """Sessão expirada, login recusado, 401/403."""


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
