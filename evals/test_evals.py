"""Eval tests for the todos MCP server (RFC 0007 §4.5).

T1: Single-turn tool-selection smoke tests.
    Verifies the model picks the correct tool for 20 discriminating prompts.
    Fast (~seconds). Runs in CI whenever ANTHROPIC_API_KEY is available.

T2: Multi-call QA tests from golden.xml.
    Full agent loop — model calls tools freely, final answer checked against
    expected substring (case-insensitive, semicolon-separated parts).
    Slow (~minutes). Runs on workflow_dispatch or PRs touching tool files.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import anthropic
import defusedxml.ElementTree as DefusedET
import pytest
from fastmcp.client import Client, FastMCPTransport

from evals.runner import run_agent

_SKIP_REASON = "ANTHROPIC_API_KEY not set"
requires_api = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason=_SKIP_REASON,
)

# ---------------------------------------------------------------------------
# T1 — tool-selection smoke (20 cases)
# ---------------------------------------------------------------------------

#: (question, expected_tool) — each case is unambiguous: only one tool fits.
T1_CASES: list[tuple[str, str]] = [
    (
        "Quantos documentos tem o processo 50300.001234/2025-00?",
        "sei_arvore_processo",
    ),
    (
        "Qual o tipo processual do processo 50300.001234/2025-00?",
        "sei_consultar_processo",
    ),
    (
        "Pesquise tipos de processo com 'Fiscalização' no nome",
        "sei_pesquisar_tipos_processo",
    ),
    (
        "Pesquise tipos de documento com 'Memorando' no nome",
        "sei_pesquisar_tipos_documento",
    ),
    (
        "Em quais unidades está aberto o processo 50300.001234/2025-00?",
        "sei_listar_unidades_processo",
    ),
    (
        "Pesquise unidades do SEI com 'Gerência' no nome",
        "sei_pesquisar_unidades",
    ),
    (
        "Leia o conteúdo do documento com id 2843449",
        "sei_ler_documento",
    ),
    (
        "Em qual processo está o documento SEI 2843449?",
        "sei_buscar_documento",
    ),
    (
        "Quantos processos de cada tipo estão na caixa da minha unidade?",
        "sei_resumo_processos",
    ),
    (
        "Liste os processos abertos na minha unidade",
        "sei_listar_processos",
    ),
    (
        "Quais são os interessados do processo 50300.001234/2025-00?",
        "sei_listar_interessados",
    ),
    (
        "Mostre o histórico de andamentos do processo 50300.001234/2025-00",
        "sei_listar_atividades",
    ),
    (
        "Envie o processo 50300.001234/2025-00 para a unidade GPF",
        "sei_enviar_processo",
    ),
    (
        "Crie um Despacho no processo 50300.001234/2025-00",
        "sei_criar_documento",
    ),
    (
        "Conclua o processo 50300.001234/2025-00",
        "sei_concluir_processo",
    ),
    (
        "Liste o histórico de sobrestamentos do processo 50300.001234/2025-00",
        "sei_listar_sobrestamentos",
    ),
    (
        "Quem está atribuído ao processo 50300.001234/2025-00?",
        "sei_consultar_atribuicao",
    ),
    (
        "Atribua o processo 50300.001234/2025-00 ao usuário 'joao.silva'",
        "sei_atribuir_processo",
    ),
    (
        "Liste os marcadores disponíveis no SEI",
        "sei_pesquisar_marcadores",
    ),
    (
        "Assine o documento 2843449 com o cargo de Analista",
        "sei_assinar_documento",
    ),
]


def _load_golden_xml() -> list[tuple[str, str]]:
    """Load QA pairs from golden.xml, return list of (question, answer)."""
    path = Path(__file__).parent / "golden.xml"
    root = DefusedET.parse(path).getroot()
    pairs = []
    for qa in root.findall("qa_pair"):
        q_elem = qa.find("question")
        a_elem = qa.find("answer")
        if q_elem is not None and a_elem is not None and q_elem.text and a_elem.text:
            pairs.append((q_elem.text.strip(), a_elem.text.strip()))
    return pairs


try:
    _GOLDEN_QA = _load_golden_xml()
except FileNotFoundError:
    _GOLDEN_QA = []


# ---------------------------------------------------------------------------
# T1 tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
@requires_api
@pytest.mark.parametrize(("question", "expected_tool"), T1_CASES, ids=[c[1] for c in T1_CASES])
def test_t1_tool_selection(question: str, expected_tool: str, patched_mcp) -> None:
    """Verify the model selects the correct tool for a single-turn prompt."""

    async def _run() -> None:
        async with Client(FastMCPTransport(patched_mcp)) as c:
            mcp_tools = await c.list_tools()

        tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in mcp_tools
        ]

        sdk_client = anthropic.Anthropic()
        resp = sdk_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            tools=tools,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": question}],
        )
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        assert tool_uses, f"No tool call made for: {question!r}"
        chosen = tool_uses[0].name
        assert chosen == expected_tool, f"Expected {expected_tool!r}, got {chosen!r}\nQ: {question}"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# T2 tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
@requires_api
@pytest.mark.parametrize(
    ("question", "answer"),
    _GOLDEN_QA,
    ids=[f"qa{i + 1}" for i in range(len(_GOLDEN_QA))],
)
def test_t2_qa_pair(question: str, answer: str, patched_mcp) -> None:
    """Multi-call agent loop — answer must contain all expected substrings."""
    response = asyncio.run(run_agent(question, patched_mcp))
    response_lower = response.lower()
    parts = [p.strip().lower() for p in answer.split(";") if p.strip()]
    missing = [p for p in parts if p not in response_lower]
    assert not missing, (
        f"Missing parts {missing!r} in agent response.\n"
        f"Q: {question}\n"
        f"Expected (each part): {parts}\n"
        f"Got: {response[:800]}"
    )
