"""Escolha explícita de backend por chamada (sem fallback automático).

``CompositeBackend`` costumava decidir sozinho, por operação, se tentava REST
primeiro ou web primeiro, caindo silenciosamente para o outro em caso de
falha. Isso escondia timeouts/erros reais atrás de uma mensagem genérica e
tirava do usuário a decisão de qual fonte de dados usar.

``requires_backend`` expõe um parâmetro ``backend: Literal["rest", "web"]``
obrigatório no schema MCP de cada tool decorada, sem precisar editar o corpo
de cada função: a escolha é gravada no ``Context`` da chamada via
``ctx.set_state(..., serializable=False)`` — o mecanismo de estado
request-scoped que o próprio FastMCP já expõe para isso — e ``_backend(ctx)``
(em ``mcp_app.py``) a lê de volta para devolver o backend cru correspondente,
sem tentar o outro em caso de falha. ``serializable=False`` é essencial: sem
ele, o valor persistiria na sessão inteira (todas as chamadas seguintes),
em vez de valer só para a chamada atual.
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Literal

from fastmcp import Context
from fastmcp.utilities.types import find_kwarg_by_type

from todos.exceptions import SEIError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import FunctionType

BackendChoice = Literal["rest", "web"]

_STATE_KEY = "todos.backend_choice"


async def get_backend_choice(ctx: Context | None) -> BackendChoice:
    """Lê a escolha de backend gravada por ``requires_backend`` para esta chamada.

    Levanta ``SEIError`` se não houver escolha registrada — ou porque a tool
    não foi decorada com ``@requires_backend``, ou porque ``ctx`` é ``None``
    (chamada direta fora do protocolo MCP, sem contexto de requisição).
    """
    if ctx is None:
        msg = "get_backend_choice: contexto MCP não disponível."
        raise SEIError(msg)
    escolha = await ctx.get_state(_STATE_KEY)
    if escolha not in ("rest", "web"):
        msg = (
            "get_backend_choice: nenhuma escolha de backend na chamada atual — "
            "a tool precisa do decorator @requires_backend (todos.backends.choice)."
        )
        raise SEIError(msg)
    return escolha


def requires_backend(fn: FunctionType) -> Callable[..., Awaitable[object]]:
    """Adiciona ``backend: Literal["rest", "web"]`` ao schema MCP da tool.

    O corpo de *fn* permanece inalterado — nenhuma chamada existente a
    ``_backend(ctx)`` precisa mudar. A escolha chega via
    ``get_backend_choice(ctx)`` dentro de ``_backend``, não como argumento
    posicional/nomeado de *fn*.
    """
    sig = inspect.signature(fn)
    ctx_param = find_kwarg_by_type(fn, Context)
    if ctx_param is None:
        msg = f"requires_backend: {fn.__qualname__} não tem parâmetro Context — não pode gravar a escolha de backend."
        raise TypeError(msg)

    novo_param = inspect.Parameter(
        "backend",
        kind=inspect.Parameter.KEYWORD_ONLY,
        annotation=BackendChoice,
    )
    nova_sig = sig.replace(parameters=[*sig.parameters.values(), novo_param])

    @functools.wraps(fn)
    async def wrapper(*args: object, backend: BackendChoice, **kwargs: object) -> object:
        bound = sig.bind_partial(*args, **kwargs)
        ctx = bound.arguments.get(ctx_param)
        if ctx is None:
            msg = (
                f"{fn.__qualname__}: contexto MCP não disponível para gravar a escolha de backend."
            )
            raise SEIError(msg)
        await ctx.set_state(_STATE_KEY, backend, serializable=False)
        return await fn(*args, **kwargs)

    wrapper.__dict__["__signature__"] = nova_sig
    wrapper.__annotations__ = {**fn.__annotations__, "backend": BackendChoice}
    return wrapper
