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
    Por isso as tools podem simplesmente deixar o `SEIError` propagar — não há
    mais `_to_tool_error`. Onde uma orientação acionável importa, criamos um erro
    específico do cenário (ex.: `DocumentoAssinadoError`) carregando a mensagem.
    """


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
