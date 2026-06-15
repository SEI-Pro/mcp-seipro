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

from todos.backends.base import SEIBackend
from todos.tools import processos

if TYPE_CHECKING:
    from collections.abc import Callable

# Import every tool module so the @mcp.tool functions exist as plain coroutines.
_MODULES = [
    "todos.tools.acompanhamento",
    "todos.tools.assinatura",
    "todos.tools.blocos_assinatura",
    "todos.tools.blocos_internos",
    "todos.tools.catalogos",
    "todos.tools.credenciamento",
    "todos.tools.marcadores",
    "todos.tools.processos",
    "todos.tools.unidades",
]
for _m in _MODULES:
    importlib.import_module(_m)


class RecordingBackend:
    """Duck-typed stand-in for the composite backend that records every op call.

    Tools delegate to it by name (`backend.consultar_processo(...)`); `__getattr__`
    returns a coroutine that records the call and yields a canned dict. `name` is a
    real attribute so it is never intercepted.
    """

    name = "fake"

    def __init__(self, result: Any = None) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._result = {"ok": True} if result is None else result

    def __getattr__(self, op: str) -> Callable[..., Any]:
        async def _op(*args: object, **kwargs: object) -> Any:
            self.calls.append((op, args, kwargs))
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
    monkeypatch.setattr(module, "_backend", lambda _ctx: fake)

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
    assert isinstance(result, str)


def test_consultar_processo_routes_and_keeps_public_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ctx is positional-required here; the hybrid tool also runs the access gate
    # on the merged result, so verify a public payload routes cleanly.
    fake = RecordingBackend({"IdProcedimento": "42", "nivelAcesso": "0"})
    monkeypatch.setattr(processos, "_backend", lambda _ctx: fake)

    result = asyncio.run(processos.sei_consultar_processo("50300.000123/2025-00", ctx=None))
    assert fake.calls[0][0] == "consultar_processo"
    assert "50300.000123/2025-00" in _flatten(fake.calls[0][1], fake.calls[0][2])
    assert "42" in result


def test_executar_acao_dry_run_does_not_touch_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default confirmar=False must short-circuit to a dry-run preview, never
    # delegating a potentially irreversible action to the backend.
    fake = RecordingBackend()
    monkeypatch.setattr(processos, "_backend", lambda _ctx: fake)

    result = asyncio.run(processos.sei_executar_acao("P", "procedimento_concluir", ctx=None))
    assert fake.calls == []
    assert "dry_run" in result


def test_no_route_targets_a_nonexistent_contract_op() -> None:
    # Every expected_op in the table must be a real operation on the contract,
    # so the routing assertions can't be satisfied by a typo on both sides.
    contract = {n for n in dir(SEIBackend) if not n.startswith("_")}
    unknown = sorted({op for *_, op, _ in _ROUTES} - contract)
    assert not unknown, f"table references ops absent from the contract: {unknown}"
