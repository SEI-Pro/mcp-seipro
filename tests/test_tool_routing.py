"""Routing harness for the MCP tool layer.

The ~120 tools are thin wrappers that resolve the composite backend via
`_backend(ctx)` and delegate to exactly one contract operation. The failure mode
this guards against is a copy-paste routing bug — a tool delegating to the wrong
op or dropping/swapping an argument (e.g. `sei_excluir_bloco_interno` calling
`excluir_blocos_assinatura`, or `sei_historico_atribuicoes` whose op is actually
`listar_historico_atribuicoes`). None of that is visible to ruff.

Each tool module imports `_backend` into its own namespace, so we monkeypatch
`<module>._backend` to return a recording fake, invoke the tool on its main path,
and assert: (1) exactly one backend op was called, (2) it was the expected op,
(3) every identifying argument was forwarded, and (4) the tool returns a string.

No live SEI, no FastMCP server, no network.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any

import pytest
from helpers import aconst

from todos.backends.base import SEIBackend
from todos.mcp_app import mcp
from todos.responses import (
    ListaAtividades,
    ListaDocumentos,
    ProcessoDetalhe,
    ResultadoPesquisaProcessos,
)
from todos.server import _wrap_pesquisa
from todos.tools import documentos, processos

if TYPE_CHECKING:
    from collections.abc import Callable

# Import every tool module + todos.server so the @mcp.tool functions exist as
# plain coroutines and all 126 tools (including the 6 in server.py) are
# registered — making test_all_mcp_tools_have_routing_entry deterministic
# regardless of pytest collection order with test_tool_count.py.
_MODULES = [
    "todos.server",  # registers the 6 orquestração tools in server.py
    "todos.tools.acompanhamento",
    "todos.tools.assinatura",
    "todos.tools.blocos_assinatura",
    "todos.tools.blocos_internos",
    "todos.tools.catalogos",
    "todos.tools.configuracao",
    "todos.tools.credenciamento",
    "todos.tools.documentos",
    "todos.tools.marcadores",
    "todos.tools.processos",
    "todos.tools.unidades",
]
for _m in _MODULES:
    importlib.import_module(_m)


_LIST_RETURNING_OPS: frozenset[str] = frozenset(
    {
        "listar_processos_bloco_interno",
        "listar_documentos_bloco_assinatura",
    }
)


class RecordingBackend:
    """Duck-typed stand-in for the composite backend that records every op call.

    Tools delegate to it by name (`backend.consultar_processo(...)`); `__getattr__`
    returns a coroutine that records the call and yields a canned dict. `name` is a
    real attribute so it is never intercepted.

    Only operations listed in ``_RECORDING_BACKEND_OPS`` are accepted; unknown
    names raise ``AttributeError`` immediately so typos in the routing table are
    caught before any assertion runs.

    Operations in ``_LIST_RETURNING_OPS`` always return ``[]`` so tools that slice
    the result don't crash with ``KeyError`` during routing tests.
    """

    name = "fake"

    def __init__(self, result: Any = None) -> None:
        """Initialise the backend with an optional canned result."""
        self.calls: list[tuple[str, tuple, dict]] = []
        self._result = {"ok": True} if result is None else result

    def __getattr__(self, op: str) -> Callable[..., Any]:
        """Return a coroutine that records the call, or raise for unknown ops."""
        if op not in _RECORDING_BACKEND_OPS:
            msg = f"Unknown backend operation: {op!r}"
            raise AttributeError(msg)

        async def _op(*args: object, **kwargs: object) -> Any:
            self.calls.append((op, args, kwargs))
            if op in _LIST_RETURNING_OPS:
                return []
            return dict(self._result) if isinstance(self._result, dict) else self._result

        return _op


# Each route fixes how one tool must dispatch. Fields in order: module suffix,
# tool name, kwargs to call it with, the backend op it must reach, and the
# identifying argument values that must be forwarded to that op.
_ROUTES: list[tuple[str, str, dict, str, list[object]]] = [
    # --- acompanhamento ---
    (
        "acompanhamento",
        "sei_remover_acompanhamento",
        {"processo": "P1"},
        "remover_acompanhamento",
        ["P1"],
    ),
    (
        "acompanhamento",
        "sei_criar_grupo_acompanhamento",
        {"nome": "Grp"},
        "criar_grupo_acompanhamento",
        ["Grp"],
    ),
    (
        "acompanhamento",
        "sei_excluir_grupo_acompanhamento",
        {"ids_grupos": "1,2"},
        "excluir_grupo_acompanhamento",
        ["1,2"],
    ),
    (
        "acompanhamento",
        "sei_listar_grupos_acompanhamento",
        {"filtro": "fil"},
        "listar_grupos_acompanhamento",
        ["fil"],
    ),
    (
        "acompanhamento",
        "sei_acompanhar_processo",
        {"processo": "P", "grupo": "G", "observacao": "Ob"},
        "acompanhar_processo",
        ["P", "G", "Ob"],
    ),
    (
        "acompanhamento",
        "sei_alterar_acompanhamento",
        {"processo": "P", "grupo": "G", "observacao": "Ob"},
        "alterar_acompanhamento",
        ["P", "G", "Ob"],
    ),
    # --- marcadores (plural-op copy-paste hazards) ---
    (
        "marcadores",
        "sei_criar_marcador",
        {"nome": "Nm", "id_cor": "3"},
        "criar_marcador",
        ["Nm", "3"],
    ),
    ("marcadores", "sei_excluir_marcador", {"ids_marcadores": "9"}, "excluir_marcadores", ["9"]),
    (
        "marcadores",
        "sei_marcar_processo",
        {"processo": "P", "marcador": "M", "texto": "T"},
        "marcar_processo",
        ["P", "M", "T"],
    ),
    (
        "marcadores",
        "sei_desmarcar_processo",
        {"processo": "P", "marcador": "M"},
        "desmarcar_processo",
        ["P", "M"],
    ),
    ("marcadores", "sei_pesquisar_marcadores", {"filtro": "fil"}, "pesquisar_marcadores", ["fil"]),
    (
        "marcadores",
        "sei_consultar_marcador_processo",
        {"processo": "P"},
        "consultar_marcador_processo",
        ["P"],
    ),
    (
        "marcadores",
        "sei_historico_marcador_processo",
        {"processo": "P"},
        "historico_marcador_processo",
        ["P"],
    ),
    (
        "marcadores",
        "sei_desativar_marcador",
        {"ids_marcadores": "9"},
        "desativar_marcadores",
        ["9"],
    ),
    ("marcadores", "sei_reativar_marcador", {"ids_marcadores": "9"}, "reativar_marcadores", ["9"]),
    # --- assinatura ---
    (
        "assinatura",
        "sei_assinar_documento",
        {"id_documento": "D", "cargo": "C", "orgao": "O"},
        "assinar_documento",
        ["D", "C", "O"],
    ),
    ("assinatura", "sei_listar_assinaturas", {"id_documento": "D"}, "listar_assinaturas", ["D"]),
    (
        "assinatura",
        "sei_assinar_bloco",
        {"id_bloco": "B", "cargo": "C"},
        "assinar_bloco",
        ["B", "C"],
    ),
    (
        "assinatura",
        "sei_assinar_documentos_bloco",
        {"documentos": "D1,D2", "cargo": "C"},
        "assinar_documentos_bloco",
        ["D1,D2", "C"],
    ),
    ("assinatura", "sei_dar_ciencia", {"referencia": "R"}, "dar_ciencia", ["R"]),
    ("assinatura", "sei_listar_ciencias", {"referencia": "R"}, "listar_ciencias", ["R"]),
    # --- blocos_assinatura (excluir/concluir → plural ops) ---
    (
        "blocos_assinatura",
        "sei_criar_bloco_assinatura",
        {"descricao": "D"},
        "criar_bloco_assinatura",
        ["D"],
    ),
    (
        "blocos_assinatura",
        "sei_incluir_documento_bloco_assinatura",
        {"id_bloco": "B", "documentos": "X"},
        "incluir_documento_bloco_assinatura",
        ["B", "X"],
    ),
    (
        "blocos_assinatura",
        "sei_disponibilizar_bloco_assinatura",
        {"id_bloco": "B"},
        "disponibilizar_bloco_assinatura",
        ["B"],
    ),
    (
        "blocos_assinatura",
        "sei_cancelar_disponibilizacao_bloco",
        {"id_bloco": "B"},
        "cancelar_disponibilizacao_bloco_assinatura",
        ["B"],
    ),
    (
        "blocos_assinatura",
        "sei_pesquisar_blocos_assinatura",
        {"filtro": "fil"},
        "pesquisar_blocos_assinatura",
        ["fil"],
    ),
    (
        "blocos_assinatura",
        "sei_listar_documentos_bloco_assinatura",
        {"id_bloco": "B"},
        "listar_documentos_bloco_assinatura",
        ["B"],
    ),
    (
        "blocos_assinatura",
        "sei_retirar_documentos_bloco_assinatura",
        {"id_bloco": "B", "documentos": "X"},
        "retirar_documentos_bloco_assinatura",
        ["B", "X"],
    ),
    (
        "blocos_assinatura",
        "sei_alterar_bloco_assinatura",
        {"id_bloco": "B", "descricao": "D"},
        "alterar_bloco_assinatura",
        ["B", "D"],
    ),
    (
        "blocos_assinatura",
        "sei_excluir_bloco_assinatura",
        {"ids_blocos": "1"},
        "excluir_blocos_assinatura",
        ["1"],
    ),
    (
        "blocos_assinatura",
        "sei_concluir_bloco_assinatura",
        {"ids_blocos": "1"},
        "concluir_blocos_assinatura",
        ["1"],
    ),
    (
        "blocos_assinatura",
        "sei_reabrir_bloco_assinatura",
        {"id_bloco": "B"},
        "reabrir_bloco_assinatura",
        ["B"],
    ),
    (
        "blocos_assinatura",
        "sei_retornar_bloco_assinatura",
        {"id_bloco": "B"},
        "retornar_bloco_assinatura",
        ["B"],
    ),
    (
        "blocos_assinatura",
        "sei_anotar_documento_bloco_assinatura",
        {"id_bloco": "B", "documento": "D", "descricao": "X"},
        "anotar_documento_bloco_assinatura",
        ["B", "D", "X"],
    ),
    (
        "blocos_assinatura",
        "sei_alterar_anotacao_bloco_assinatura",
        {"id_bloco": "B", "documento": "D", "descricao": "X"},
        "alterar_anotacao_bloco_assinatura",
        ["B", "D", "X"],
    ),
    # --- blocos_internos (mirror ops — easy to cross-wire with blocos_assinatura) ---
    (
        "blocos_internos",
        "sei_criar_bloco_interno",
        {"descricao": "D"},
        "criar_bloco_interno",
        ["D"],
    ),
    (
        "blocos_internos",
        "sei_incluir_processo_bloco_interno",
        {"id_bloco": "B", "processos": "P"},
        "incluir_processo_bloco_interno",
        ["B", "P"],
    ),
    (
        "blocos_internos",
        "sei_retirar_processo_bloco_interno",
        {"id_bloco": "B", "processos": "P"},
        "retirar_processo_bloco_interno",
        ["B", "P"],
    ),
    (
        "blocos_internos",
        "sei_listar_processos_bloco_interno",
        {"id_bloco": "B"},
        "listar_processos_bloco_interno",
        ["B"],
    ),
    (
        "blocos_internos",
        "sei_alterar_bloco_interno",
        {"id_bloco": "B", "descricao": "D"},
        "alterar_bloco_interno",
        ["B", "D"],
    ),
    (
        "blocos_internos",
        "sei_excluir_bloco_interno",
        {"ids_blocos": "1"},
        "excluir_blocos_internos",
        ["1"],
    ),
    (
        "blocos_internos",
        "sei_concluir_bloco_interno",
        {"ids_blocos": "1"},
        "concluir_blocos_internos",
        ["1"],
    ),
    (
        "blocos_internos",
        "sei_reabrir_bloco_interno",
        {"id_bloco": "B"},
        "reabrir_bloco_interno",
        ["B"],
    ),
    (
        "blocos_internos",
        "sei_anotar_processo_bloco_interno",
        {"id_bloco": "B", "processo": "P", "descricao": "X"},
        "anotar_processo_bloco_interno",
        ["B", "P", "X"],
    ),
    (
        "blocos_internos",
        "sei_alterar_anotacao_bloco_interno",
        {"id_bloco": "B", "processo": "P", "descricao": "X"},
        "alterar_anotacao_bloco_interno",
        ["B", "P", "X"],
    ),
    # --- credenciamento ---
    (
        "credenciamento",
        "sei_listar_credenciamentos",
        {"processo": "P"},
        "listar_credenciamentos",
        ["P"],
    ),
    (
        "credenciamento",
        "sei_conceder_credenciamento",
        {"processo": "P", "id_usuario": "U"},
        "conceder_credenciamento",
        ["P", "U"],
    ),
    (
        "credenciamento",
        "sei_renunciar_credenciamento",
        {"processo": "P"},
        "renunciar_credenciamento",
        ["P"],
    ),
    (
        "credenciamento",
        "sei_cassar_credenciamento",
        {"processo": "P", "id_usuario": "U"},
        "cassar_credenciamento",
        ["P", "U"],
    ),
    # --- catalogos ---
    (
        "catalogos",
        "sei_pesquisar_hipoteses_legais",
        {"filtro": "fil"},
        "pesquisar_hipoteses_legais",
        ["fil"],
    ),
    (
        "catalogos",
        "sei_pesquisar_tipos_processo",
        {"filtro": "fil"},
        "pesquisar_tipos_processo",
        ["fil"],
    ),
    (
        "catalogos",
        "sei_pesquisar_tipos_documento",
        {"filtro": "fil"},
        "pesquisar_tipos_documento",
        ["fil"],
    ),
    (
        "catalogos",
        "sei_pesquisar_tipos_documento_externo",
        {"filtro": "fil"},
        "pesquisar_tipos_documento_externo",
        ["fil"],
    ),
    (
        "catalogos",
        "sei_pesquisar_tipos_conferencia",
        {"filtro": "fil"},
        "pesquisar_tipos_conferencia",
        ["fil"],
    ),
    ("catalogos", "sei_pesquisar_assuntos", {"filtro": "fil"}, "pesquisar_assuntos", ["fil"]),
    ("catalogos", "sei_pesquisar_contatos", {"filtro": "fil"}, "pesquisar_contatos", ["fil"]),
    ("catalogos", "sei_criar_contato", {"nome": "Nm"}, "criar_contato", ["Nm"]),
    (
        "catalogos",
        "sei_pesquisar_textos_padrao",
        {"filtro": "fil"},
        "pesquisar_textos_padrao",
        ["fil"],
    ),
    (
        "catalogos",
        "sei_sugestao_assuntos_processo",
        {"id_tipo_processo": "T"},
        "sugestao_assuntos_processo",
        ["T"],
    ),
    # --- processos (read + write; note the op-name mismatch on historico) ---
    ("processos", "sei_arvore_processo", {"protocolo_formatado": "PF"}, "arvore_processo", ["PF"]),
    (
        "processos",
        "sei_listar_documentos",
        {"protocolo_formatado": "PF"},
        "listar_documentos",
        ["PF"],
    ),
    (
        "processos",
        "sei_listar_unidades_processo",
        {"processo": "P"},
        "listar_unidades_processo",
        ["P"],
    ),
    ("processos", "sei_listar_interessados", {"processo": "P"}, "listar_interessados", ["P"]),
    ("processos", "sei_listar_sobrestamentos", {"processo": "P"}, "listar_sobrestamentos", ["P"]),
    ("processos", "sei_consultar_atribuicao", {"processo": "P"}, "consultar_atribuicao", ["P"]),
    (
        "processos",
        "sei_historico_atribuicoes",
        {"processo": "P"},
        "listar_historico_atribuicoes",
        ["P"],
    ),
    ("processos", "sei_verificar_acesso", {"processo": "P"}, "verificar_acesso", ["P"]),
    ("processos", "sei_listar_relacionamentos", {"processo": "P"}, "listar_relacionamentos", ["P"]),
    ("processos", "sei_listar_atividades", {"processo": "P"}, "listar_atividades", ["P"]),
    ("processos", "sei_concluir_processo", {"numero_processo": "P"}, "concluir_processo", ["P"]),
    ("processos", "sei_reabrir_processo", {"processo": "P"}, "reabrir_processo", ["P"]),
    ("processos", "sei_receber_processo", {"processo": "P"}, "receber_processo", ["P"]),
    ("processos", "sei_remover_atribuicao", {"processo": "P"}, "remover_atribuicao", ["P"]),
    ("processos", "sei_remover_sobrestamento", {"processo": "P"}, "remover_sobrestamento", ["P"]),
    (
        "processos",
        "sei_registrar_andamento",
        {"processo": "P", "descricao": "D"},
        "registrar_andamento",
        ["P", "D"],
    ),
    (
        "processos",
        "sei_criar_anotacao",
        {"processo": "P", "descricao": "D"},
        "criar_anotacao",
        ["P", "D"],
    ),
    ("processos", "sei_remover_anotacao", {"processo": "P"}, "remover_anotacao", ["P"]),
    (
        "processos",
        "sei_criar_observacao",
        {"processo": "P", "descricao": "D"},
        "criar_observacao",
        ["P", "D"],
    ),
    (
        "processos",
        "sei_executar_acao",
        {"processo": "P", "acao": "A", "confirmar": True},
        "executar_acao",
        ["P", "A"],
    ),
    # --- documentos (migrated onto the composite contract) ---
    ("documentos", "sei_listar_secoes", {"id_documento": "D"}, "listar_secoes", ["D"]),
    (
        "documentos",
        "sei_sugestao_assuntos_documento",
        {"id_serie": "S"},
        "sugestao_assuntos_documento",
        ["S"],
    ),
    (
        "documentos",
        "sei_listar_blocos_documento",
        {"id_documento": "D"},
        "listar_blocos_documento",
        ["D"],
    ),
    (
        "documentos",
        "sei_alterar_documento_interno",
        {"id_documento": "D", "descricao": "Desc"},
        "alterar_documento_interno",
        ["D", "Desc"],
    ),
    (
        "documentos",
        "sei_alterar_documento_externo",
        {"id_documento": "D", "descricao": "Desc"},
        "alterar_documento_externo",
        ["D", "Desc"],
    ),
    (
        "documentos",
        "sei_criar_documento_externo",
        {"processo": "PF", "id_serie": "S", "arquivo_path": "doc.pdf"},
        "criar_documento_externo",
        ["PF"],
    ),
    (
        "documentos",
        "sei_consultar_documento_externo",
        {"id_documento": "D", "processo": "PF"},
        "consultar_documento_externo",
        ["D", "PF"],
    ),
    (
        "documentos",
        "sei_incluir_documento_externo",
        {"processo": "PF", "arquivo_base64": "eA==", "nome_arquivo": "x.pdf", "id_serie": "S"},
        "criar_documento_externo",
        ["PF"],
    ),
    # --- unidades ---
    ("unidades", "sei_pesquisar_unidades", {"filtro": "fil"}, "pesquisar_unidades", ["fil"]),
    ("unidades", "sei_pesquisar_usuarios", {"filtro": "fil"}, "pesquisar_usuarios", ["fil"]),
    (
        "unidades",
        "sei_pesquisar_outras_unidades",
        {"filtro": "fil"},
        "pesquisar_outras_unidades",
        ["fil"],
    ),
    ("unidades", "sei_listar_contextos", {"id_orgao": "Org"}, "listar_contextos", ["Org"]),
]

# Whitelist of backend operation names that RecordingBackend will accept.
# Derived from the _ROUTES table plus the extra ops used in hand-written tests.
_RECORDING_BACKEND_OPS: frozenset[str] = frozenset(
    # Operations exercised by the parametrised _ROUTES table.
    {op for *_, op, _ in _ROUTES}
    # Extra operations called in hand-written tests below.
    | {
        "consultar_processo",  # test_consultar_processo_routes_and_keeps_public_payload
        "alterar_secoes",  # test_editar_secao_reads_then_writes_via_composite
        "criar_documento_interno",  # test_criar_documento_routes_to_composite + test_criar_documento_shaped_output_preserves_doc_ids
        "criar_processo",  # test_criar_processo_shaped_output_preserves_ids
        "alterar_processo",  # test_alterar_processo_shaped_output
        "criar_documento_externo",  # test_incluir_documento_externo_shaped_output
        "requer_id_serie",  # test_criar_documento_routes_to_composite + test_criar_documento_shaped_output_preserves_doc_ids
        "resolver_documento",  # test_criar_documento_routes_to_composite + read/baixar tests
    }
)


def _flatten(args: tuple, kwargs: dict) -> list[object]:
    return [*args, *kwargs.values()]


@pytest.mark.parametrize("route", _ROUTES, ids=[f"{r[0]}.{r[1]}" for r in _ROUTES])
def test_tool_routes_to_expected_op(
    monkeypatch: pytest.MonkeyPatch,
    route: tuple[str, str, dict, str, list[object]],
) -> None:
    module_suffix, tool_name, call_kwargs, expected_op, sentinels = route
    module = importlib.import_module(f"todos.tools.{module_suffix}")
    fake = RecordingBackend()
    monkeypatch.setattr(module, "_backend", aconst(fake))

    tool = getattr(module, tool_name)
    result = asyncio.run(tool(ctx=None, **call_kwargs))

    assert len(fake.calls) == 1, f"{tool_name} made {len(fake.calls)} backend calls, expected 1"
    op, args, kwargs = fake.calls[0]
    assert op == expected_op, f"{tool_name} routed to {op!r}, expected {expected_op!r}"
    forwarded = _flatten(args, kwargs)
    for sentinel in sentinels:
        assert sentinel in forwarded, (
            f"{tool_name} dropped argument {sentinel!r} (forwarded {forwarded})"
        )
    assert result is not None


def test_consultar_processo_routes_and_keeps_public_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ctx is positional-required here; the hybrid tool also runs the access gate
    # on the merged result, so verify a public payload routes cleanly.
    fake = RecordingBackend({"IdProcedimento": "42", "nivelAcesso": "0"})
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_consultar_processo("50300.000123/2025-00", ctx=None))
    assert fake.calls[0][0] == "consultar_processo"
    assert "50300.000123/2025-00" in _flatten(fake.calls[0][1], fake.calls[0][2])
    assert isinstance(result, ProcessoDetalhe)
    assert result.id_procedimento == "42"


def test_executar_acao_dry_run_does_not_touch_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default confirmar=False must short-circuit to a dry-run preview, never
    # delegating a potentially irreversible action to the backend.
    fake = RecordingBackend()
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_executar_acao("P", "procedimento_concluir", ctx=None))
    assert fake.calls == []
    assert "dry_run" in result


def test_editar_secao_reads_then_writes_via_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    # Migrated tool: must fetch current sections then submit the full set, both
    # through the composite (no direct REST client).
    fake = RecordingBackend({"secoes": [], "ultimaVersaoDocumento": "3"})
    monkeypatch.setattr(documentos, "_backend", aconst(fake))

    asyncio.run(
        documentos.sei_editar_secao("D", [{"idSecaoModelo": "1", "conteudo": "x"}], ctx=None)
    )
    ops = [c[0] for c in fake.calls]
    assert ops == ["listar_secoes", "alterar_secoes"]


def test_criar_documento_routes_to_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    # Migrated hybrid create: routes through backend.criar_documento_interno
    # regardless of REST/web; the processo and a NovoDocumentoInterno are passed.
    fake = RecordingBackend()
    monkeypatch.setattr(documentos, "_backend", aconst(fake))

    asyncio.run(documentos.sei_criar_documento("PF", id_serie="S", descricao="d", ctx=None))
    op, args, _ = fake.calls[-1]
    assert op == "criar_documento_interno"
    assert args[0] == "PF"
    assert args[1].id_serie == "S"


class _ReadBackend(SEIBackend):
    """Fake whose document reads return typed payloads (str/bytes), recording ops."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        self.calls.append("consultar_documento_interno")
        return {"nivelAcesso": "0"}

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        self.calls.append("consultar_documento_externo")
        return {"nivelAcesso": "0"}

    async def visualizar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> str:
        del id_documento, processo
        self.calls.append("visualizar_documento_interno")
        return "<p>OLA MUNDO</p>"

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        del id_documento, processo
        self.calls.append("baixar_anexo")
        return b"%PDF-1.4 conteudo"

    async def resolver_documento(self, referencia: str) -> tuple[str, str]:
        return referencia, "auto"


def test_ler_documento_gates_then_reads_via_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    # Web-only mode (no auto-resolution): gate consults then content is read,
    # both through the composite. Public doc → content released.
    backend = _ReadBackend()
    monkeypatch.setattr(documentos, "_backend", aconst(backend))

    out = asyncio.run(
        documentos.sei_ler_documento("D", tipo_documento="I", processo="PF", ctx=None)
    )
    assert backend.calls == ["consultar_documento_interno", "visualizar_documento_interno"]
    assert "OLA MUNDO" in out


def test_baixar_anexo_gates_then_downloads_via_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _ReadBackend()
    monkeypatch.setattr(documentos, "_backend", aconst(backend))

    out = asyncio.run(documentos.sei_baixar_anexo("D", processo="PF", ctx=None))
    assert backend.calls == ["consultar_documento_externo", "baixar_anexo"]
    assert "base64" in out


def test_no_route_targets_a_nonexistent_contract_op() -> None:
    # Every expected_op in the table must be a real operation on the contract,
    # so the routing assertions can't be satisfied by a typo on both sides.
    contract = {n for n in dir(SEIBackend) if not n.startswith("_")}
    unknown = sorted({op for *_, op, _ in _ROUTES} - contract)
    assert not unknown, f"table references ops absent from the contract: {unknown}"


def test_all_routed_tools_exist_in_mcp() -> None:
    """Every tool name in _ROUTES must be a tool registered with the MCP server.

    This catches stale entries — when a tool is renamed or removed, the _ROUTES
    table must be updated too.  New tools that are NOT in _ROUTES are permitted
    (they may be complex tools with dedicated hand-written tests).
    """
    registered = {t.name for t in asyncio.run(mcp.list_tools())}
    routed = {tool_name for _, tool_name, *_ in _ROUTES}

    # Sanity: the table is non-empty (guards against an accidental wipe).
    assert routed, "_ROUTES table is empty"

    unknown = sorted(routed - registered)
    assert not unknown, f"_ROUTES references tool names not registered in the MCP server: {unknown}"


# Tools intentionally absent from _ROUTES because they are covered by explicit
# hand-written tests above or have bespoke multi-op orchestration that makes
# the single-op routing table inapplicable.  Every tool not in _ROUTES MUST be
# listed here with a short explanation; the test below enforces this invariant.
_TOOLS_WITHOUT_ROUTING: frozenset[str] = frozenset(
    {
        # --- hand-written tests in this file (multi-op or stateful) ---
        "sei_consultar_processo",  # test_consultar_processo_routes_and_keeps_public_payload
        "sei_editar_secao",  # test_editar_secao_reads_then_writes_via_composite
        "sei_criar_documento",  # test_criar_documento_routes_to_composite
        "sei_ler_documento",  # test_ler_documento_gates_then_reads_via_composite
        "sei_baixar_anexo",  # test_baixar_anexo_gates_then_downloads_via_composite
        "sei_executar_acao",  # test_executar_acao_dry_run_does_not_touch_backend
        # --- multi-op orchestration (compose several backend ops) ---
        "sei_cancelar_assinatura",  # compõe listar_secoes + alterar_secoes
        "sei_marcar_nao_lido",  # compõe unidade_atual + enviar_processo
        "sei_criar_processo",  # compõe pesquisar_tipos + criar_processo + escolher_tipo
        "sei_alterar_processo",  # lê dados atuais depois altera (multi-step)
        "sei_gerar_pdf_processo",  # gerar_pdf_processo com streaming de progresso
        "sei_gerar_zip_processo",  # gerar_zip_processo com streaming de progresso
        "sei_gerar_referencia",  # orquestra resolução de doc + gera HTML âncora
        # --- utility: no backend routing (pure static data) ---
        "sei_estilos",  # retorna SEI_STYLES de memória, sem backend
        # --- parameterless / pagination-only delegates ---
        # (nenhum sentinel identificável; a single-arg sentinel check seria vazia)
        "sei_unidade_atual",  # backend.unidade_atual() sem args obrigatórios
        "sei_listar_unidades",  # backend.listar_unidades() sem args obrigatórios
        "sei_trocar_unidade",  # tem rota no composite (sessão + sync) — teste unitário próprio
        "sei_versao",  # backend.versao() sem args obrigatórios
        "sei_listar_orgaos",  # backend.listar_orgaos() sem args obrigatórios
        "sei_listar_assinantes",  # backend.listar_assinantes() sem args obrigatórios
        "sei_listar_orgaos_assinante",  # backend.listar_orgaos_assinante() sem args
        "sei_parametros_upload",  # backend.parametros_upload() sem args obrigatórios
        "sei_listar_processos",  # backend.listar_processos(pagina=, ...) — args opcionais
        "sei_listar_meus_acompanhamentos",  # backend.listar_meus_acompanhamentos(...)
        "sei_listar_acompanhamentos_unidade",  # backend.listar_acompanhamentos_unidade(...)
        "sei_listar_usuarios",  # backend.listar_usuarios(filtro=, ...) — args opcionais
        "sei_listar_grupos_modelos",  # backend.listar_grupos_modelos() sem args
        "sei_listar_modelos",  # backend.listar_modelos(id_grupo=) — ver catalogos.py
        # --- tools de orquestração em server.py (multi-estratégia, REST-only) ---
        "sei_buscar_documento",  # multi-estratégia Solr + árvore web
        "sei_resumo_processos",  # agregação REST por tipo/responsável/situação
        "sei_pesquisar_processos",  # pesquisa Solr + paginação REST
        "sei_enviar_processo",  # resolve sigla→id + tramita (multi-step)
        "sei_atribuir_processo",  # resolve nome→id + atribui (multi-step)
        "sei_sobrestar_processo",  # compõe verificação + sobrestamento
        # --- configuracao.py: keyring + descoberta (RFC 0010) ---
        "sei_detectar_formato_protocolo",  # orquestra listar_processos + inferência + keyring
        "sei_redefinir_formato_protocolo",  # remove entrada keyring (sem op de backend)
    }
)


def test_all_mcp_tools_have_routing_entry() -> None:
    """Every registered MCP tool must appear in _ROUTES or _TOOLS_WITHOUT_ROUTING.

    This is the inverse of ``test_all_routed_tools_exist_in_mcp``: it prevents
    a developer from adding a new tool without either adding a routing entry in
    ``_ROUTES`` or explicitly listing it in ``_TOOLS_WITHOUT_ROUTING`` with a
    comment explaining why routing validation does not apply.
    """
    registered = {t.name for t in asyncio.run(mcp.list_tools())}
    routed = {tool_name for _, tool_name, *_ in _ROUTES}

    missing = sorted(registered - routed - _TOOLS_WITHOUT_ROUTING)
    assert not missing, (
        f"Ferramentas sem entrada em _ROUTES nem em _TOOLS_WITHOUT_ROUTING: {missing}\n"
        "Adicione uma rota em _ROUTES ou registre a ferramenta em _TOOLS_WITHOUT_ROUTING "
        "com um comentário explicando por que a validação de roteamento não se aplica."
    )


# ---------------------------------------------------------------------------
# Phase 3 — write response shaping
# ---------------------------------------------------------------------------


def test_criar_processo_shaped_output_preserves_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_criar_processo must return shaped RespostaEscrita preserving id+protocolo."""
    fake = RecordingBackend(
        {"IdProcedimento": "99", "ProtocoloProcedimentoFormatado": "50300.000001/2026-01"}
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_criar_processo("123", ctx=None))
    assert result.acao == "criar_processo"
    assert result.status == "ok"
    assert result.id_procedimento == "99"
    assert result.protocolo == "50300.000001/2026-01"


def test_alterar_processo_shaped_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_alterar_processo must return shaped RespostaEscrita with protocolo."""
    fake = RecordingBackend({"ProtocoloFormatado": "50300.000002/2026-02"})
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_alterar_processo("50300.000002/2026-02", ctx=None))
    assert result.acao == "alterar_processo"
    assert result.protocolo == "50300.000002/2026-02"


def test_criar_documento_shaped_output_preserves_doc_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_criar_documento must return shaped RespostaEscrita preserving doc id+numero_sei."""
    fake = RecordingBackend({"idDocumento": "D42", "protocoloDocumentoFormatado": "2843449"})
    monkeypatch.setattr(documentos, "_backend", aconst(fake))

    result = asyncio.run(documentos.sei_criar_documento("PF", id_serie="S", ctx=None))
    assert result.acao == "criar_documento"
    assert result.id_documento == "D42"
    assert result.numero_sei == "2843449"


def test_incluir_documento_externo_shaped_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_incluir_documento_externo must return shaped RespostaEscrita."""
    fake = RecordingBackend({"idDocumento": "D77", "protocoloDocumentoFormatado": "2877777"})
    monkeypatch.setattr(documentos, "_backend", aconst(fake))

    result = asyncio.run(
        documentos.sei_incluir_documento_externo(
            "50300.000001/2026-01",
            arquivo_base64="dGVzdA==",
            nome_arquivo="test.pdf",
            id_serie="5",
            ctx=None,
        )
    )
    assert result.acao == "incluir_documento_externo"
    assert result.id_documento == "D77"
    assert result.numero_sei == "2877777"


# ---------------------------------------------------------------------------
# Fase 1 (RFC 0008) — read response shaping (ListaDocumentos)
# ---------------------------------------------------------------------------


def test_arvore_processo_returns_lista_documentos(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_arvore_processo (include_raw=False) returns ListaDocumentos model."""
    fake = RecordingBackend(
        {
            "documentos": [
                {
                    "id": "D1",
                    "numero_sei": "001",
                    "tipo_documento": "Despacho",
                    "nome_composto": "Despacho GPF 001",
                    "sigla_unidade": "GPF",
                },
            ],
            "total_documentos": 1,
        }
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_arvore_processo("50300.000123/2025-00"))
    assert isinstance(result, ListaDocumentos)
    assert result.processo == "50300.000123/2025-00"
    assert result.total_documentos == 1
    assert result.documentos[0].id == "D1"


def test_arvore_processo_include_raw_returns_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_arvore_processo (include_raw=True) returns raw JSON string."""
    fake = RecordingBackend({"documentos": [], "total_documentos": 0})
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_arvore_processo("50300.000123/2025-00", include_raw=True))
    assert isinstance(result, str)


def test_listar_documentos_returns_lista_documentos(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_listar_documentos (include_raw=False) returns ListaDocumentos model."""
    fake = RecordingBackend(
        {
            "documentos": [
                {
                    "id": "D2",
                    "numero_sei": "002",
                    "tipo_documento": "Ofício",
                    "nome_composto": "Ofício GPF 002",
                    "sigla_unidade": "GPF",
                },
            ],
            "total_documentos": 1,
        }
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_listar_documentos("50300.000123/2025-00"))
    assert isinstance(result, ListaDocumentos)
    assert result.processo == "50300.000123/2025-00"
    assert result.total_documentos == 1
    assert result.documentos[0].tipo_documento == "Ofício"


def test_listar_documentos_include_raw_returns_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_listar_documentos (include_raw=True) returns raw JSON string."""
    fake = RecordingBackend({"documentos": [], "total_documentos": 0})
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_listar_documentos("50300.000123/2025-00", include_raw=True))
    assert isinstance(result, str)


def test_consultar_processo_returns_processo_detalhe(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_consultar_processo (include_raw=False) returns ProcessoDetalhe model."""
    fake = RecordingBackend(
        {
            "IdProcedimento": "P123",
            "ProtocoloProcedimentoFormatado": "50300.000123/2025-00",
            "NomeTipoProcedimento": "Requerimento",
            "especificacao": "Teste",
            "nivelAcesso": "0",
            "interessados": ["Maria"],
            "total_documentos": 2,
        }
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_consultar_processo("50300.000123/2025-00", ctx=None))
    assert isinstance(result, ProcessoDetalhe)
    assert result.protocolo == "50300.000123/2025-00"
    assert result.id_procedimento == "P123"
    assert result.tipo == "Requerimento"
    assert result.total_documentos == 2
    assert result.interessados == ["Maria"]
    assert len(result.next_actions) == 1
    assert result.next_actions[0].tool == "sei_arvore_processo"


def test_consultar_processo_include_raw_returns_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_consultar_processo (include_raw=True) returns raw JSON string."""
    fake = RecordingBackend({"IdProcedimento": "P1", "nivelAcesso": "0"})
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(
        processos.sei_consultar_processo("50300.000123/2025-00", ctx=None, include_raw=True)
    )
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Fase 1 (RFC 0008) — sei_pesquisar_processos → ResultadoPesquisaProcessos
# ---------------------------------------------------------------------------


def test_wrap_pesquisa_returns_resultado_pesquisa_processos() -> None:
    """_wrap_pesquisa (include_raw=False) returns ResultadoPesquisaProcessos model."""
    result = {
        "processos": [
            {"ProtocoloFormatado": "50300.000123/2025-00", "NomeTipoProcedimento": "Requerimento"}
        ],
        "pagina_atual": 0,
        "itens_pagina": 1,
        "total_itens": 1,
        "tem_proxima": False,
        "fonte": "rest",
    }
    shaped = _wrap_pesquisa(result, include_raw=False, pagina=0, limit=50, cursor_extra={})
    assert isinstance(shaped, ResultadoPesquisaProcessos)
    assert shaped.total_itens == 1
    assert shaped.fonte == "rest"
    assert len(shaped.processos) == 1
    assert shaped.proximo_cursor is None


def test_wrap_pesquisa_include_raw_returns_str() -> None:
    """_wrap_pesquisa (include_raw=True) returns raw JSON string."""
    result = {"processos": [], "total_itens": 0, "tem_proxima": False}
    raw = _wrap_pesquisa(result, include_raw=True, pagina=0, limit=50, cursor_extra={})
    assert isinstance(raw, str)


def test_wrap_pesquisa_sets_proximo_cursor_when_has_more() -> None:
    """_wrap_pesquisa emits proximo_cursor when more pages exist."""
    result = {
        "processos": [{"p": i} for i in range(50)],
        "pagina_atual": 0,
        "itens_pagina": 50,
        "total_itens": 100,
        "tem_proxima": True,
    }
    shaped = _wrap_pesquisa(result, include_raw=False, pagina=0, limit=50, cursor_extra={})
    assert isinstance(shaped, ResultadoPesquisaProcessos)
    assert shaped.proximo_cursor is not None
    assert len(shaped.next_actions) == 1


# ---------------------------------------------------------------------------
# Fase 1 (RFC 0008) — sei_listar_atividades → ListaAtividades
# ---------------------------------------------------------------------------


def test_listar_atividades_returns_lista_atividades(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_listar_atividades (include_raw=False) returns ListaAtividades model."""
    fake = RecordingBackend(
        {
            "processo": {"protocolo": "50300.000123/2025-00", "id_procedimento": "P1"},
            "total_andamentos": 2,
            "andamentos": [
                {
                    "data_hora": "19/06/2026 10:00:00",
                    "unidade": "GPF",
                    "usuario": "fulano",
                    "descricao": "Processo criado",
                },
                {
                    "data_hora": "19/06/2026 11:00:00",
                    "unidade": "GPF",
                    "usuario": "fulano",
                    "descricao": "Documento assinado",
                },
            ],
        }
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_listar_atividades("50300.000123/2025-00"))
    assert isinstance(result, ListaAtividades)
    assert result.processo.protocolo == "50300.000123/2025-00"
    assert result.processo.id_procedimento == "P1"
    assert result.total_andamentos == 2
    assert len(result.andamentos) == 2
    assert result.andamentos[0].descricao == "Processo criado"
    assert not result.truncado


def test_listar_atividades_include_raw_returns_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_listar_atividades (include_raw=True) returns raw JSON string."""
    fake = RecordingBackend(
        {
            "processo": {"protocolo": "50300.000123/2025-00", "id_procedimento": "P1"},
            "total_andamentos": 0,
            "andamentos": [],
        }
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_listar_atividades("50300.000123/2025-00", include_raw=True))
    assert isinstance(result, str)


def test_listar_atividades_truncado_when_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_listar_atividades sets truncado=True when total exceeds 50."""
    fake = RecordingBackend(
        {
            "processo": {"protocolo": "50300.000123/2025-00", "id_procedimento": "P1"},
            "total_andamentos": 51,
            "andamentos": [
                {
                    "data_hora": f"19/06/2026 {i:02d}:00:00",
                    "unidade": "GPF",
                    "usuario": "u",
                    "descricao": f"Ação {i}",
                }
                for i in range(51)
            ],
        }
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_listar_atividades("50300.000123/2025-00"))
    assert isinstance(result, ListaAtividades)
    assert result.truncado
    assert len(result.andamentos) == 50  # capped at _ATIVIDADES_LIMIT


def test_listar_atividades_ordem_asc(monkeypatch: pytest.MonkeyPatch) -> None:
    """sei_listar_atividades with ordem='asc' reverses the list."""
    fake = RecordingBackend(
        {
            "processo": {"protocolo": "50300.000123/2025-00", "id_procedimento": "P1"},
            "total_andamentos": 2,
            "andamentos": [
                {
                    "data_hora": "19/06/2026 11:00:00",
                    "unidade": "",
                    "usuario": "",
                    "descricao": "B",
                },
                {
                    "data_hora": "19/06/2026 10:00:00",
                    "unidade": "",
                    "usuario": "",
                    "descricao": "A",
                },
            ],
        }
    )
    monkeypatch.setattr(processos, "_backend", aconst(fake))

    result = asyncio.run(processos.sei_listar_atividades("50300.000123/2025-00", ordem="asc"))
    assert isinstance(result, ListaAtividades)
    # After reverse, A (older) should come first
    assert result.andamentos[0].descricao == "A"
    assert result.andamentos[1].descricao == "B"
