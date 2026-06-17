"""Synthetic backends for eval tests.

FixtureBackend implements the SEIBackend interface with pre-defined data
matching the golden.xml expected answers for fixture process 50300.001234/2025-00.

FakeRESTClient provides the SEIClient interface for REST-only tools
(sei_resumo_processos uses _get_client directly, not _backend).
"""

from __future__ import annotations

from todos.exceptions import SEINotFoundError

FIXTURE_PROTOCOLO = "50300.001234/2025-00"
FIXTURE_ID_PROCEDIMENTO = "1234567"
FIXTURE_DOC_NUMERO_SEI = "2843449"

# 12 documents, newest-first (web scraper order).
# Doc 0: Despacho/GPF  → Q1 answer (last=Despacho)
# Doc 1: Ofício/GPF    → Q2 answer (most recent Ofício from GPF) + Q9 (id=2843449)
_FIXTURE_DOCUMENTOS: list[dict] = [
    {
        "id": "2874369",
        "numero_sei": "2874369",
        "tipo_documento": "Despacho",
        "nome_composto": "Despacho GPF 2874369",
        "sigla_unidade": "GPF",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": FIXTURE_DOC_NUMERO_SEI,
        "numero_sei": FIXTURE_DOC_NUMERO_SEI,
        "tipo_documento": "Ofício",
        "nome_composto": f"Ofício GPF {FIXTURE_DOC_NUMERO_SEI}",
        "sigla_unidade": "GPF",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2830001",
        "numero_sei": "2830001",
        "tipo_documento": "Memorando",
        "nome_composto": "Memorando GRP 2830001",
        "sigla_unidade": "GRP",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2815000",
        "numero_sei": "2815000",
        "tipo_documento": "Ofício",
        "nome_composto": "Ofício GRP 2815000",
        "sigla_unidade": "GRP",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2800100",
        "numero_sei": "2800100",
        "tipo_documento": "Despacho",
        "nome_composto": "Despacho GRP 2800100",
        "sigla_unidade": "GRP",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2790050",
        "numero_sei": "2790050",
        "tipo_documento": "Ofício",
        "nome_composto": "Ofício GPF 2790050",
        "sigla_unidade": "GPF",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2780000",
        "numero_sei": "2780000",
        "tipo_documento": "Nota Técnica",
        "nome_composto": "Nota Técnica GPF 2780000",
        "sigla_unidade": "GPF",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2770000",
        "numero_sei": "2770000",
        "tipo_documento": "Despacho",
        "nome_composto": "Despacho GPF 2770000",
        "sigla_unidade": "GPF",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2760000",
        "numero_sei": "2760000",
        "tipo_documento": "Ofício",
        "nome_composto": "Ofício GRP 2760000",
        "sigla_unidade": "GRP",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2750000",
        "numero_sei": "2750000",
        "tipo_documento": "Memorando",
        "nome_composto": "Memorando GPF 2750000",
        "sigla_unidade": "GPF",
        "assinado": False,
        "cancelado": False,
    },
    {
        "id": "2740000",
        "numero_sei": "2740000",
        "tipo_documento": "Despacho",
        "nome_composto": "Despacho GRP 2740000",
        "sigla_unidade": "GRP",
        "assinado": True,
        "cancelado": False,
    },
    {
        "id": "2730000",
        "numero_sei": "2730000",
        "tipo_documento": "Ofício",
        "nome_composto": "Ofício GPF 2730000",
        "sigla_unidade": "GPF",
        "assinado": True,
        "cancelado": False,
    },
]

# Andamentos for listar_atividades.
# Must have: 1 sobrestamento + 3 envios (tramitação) → Q4 + Q7.
_FIXTURE_ANDAMENTOS: list[dict] = [
    {
        "descricao": "Processo enviado para GPF",
        "tipo": "Tramitação",
        "dataHora": "26/05/2025 15:30",
        "unidade": "GRP",
        "usuario": "ana.lima",
    },
    {
        "descricao": "Processo enviado para GRP",
        "tipo": "Tramitação",
        "dataHora": "20/05/2025 09:00",
        "unidade": "GPF",
        "usuario": "joao.silva",
    },
    {
        "descricao": "Processo sobrestado: aguardando manifestação da área técnica",
        "tipo": "Sobrestamento",
        "dataHora": "10/05/2025 14:00",
        "unidade": "GPF",
        "usuario": "joao.silva",
    },
    {
        "descricao": "Processo dessobrestado",
        "tipo": "Dessobrestamento",
        "dataHora": "15/05/2025 11:00",
        "unidade": "GPF",
        "usuario": "joao.silva",
    },
    {
        "descricao": "Processo enviado para DRF",
        "tipo": "Tramitação",
        "dataHora": "02/04/2025 10:00",
        "unidade": "GPF",
        "usuario": "maria.costa",
    },
    {
        "descricao": "Andamento registrado: documentação recebida e analisada",
        "tipo": "Andamento",
        "dataHora": "28/03/2025 16:00",
        "unidade": "GPF",
        "usuario": "joao.silva",
    },
    {
        "descricao": "Processo recebido",
        "tipo": "Recebimento",
        "dataHora": "25/03/2025 08:30",
        "unidade": "GPF",
        "usuario": "maria.costa",
    },
    {
        "descricao": "Processo autuado",
        "tipo": "Autuação",
        "dataHora": "20/03/2025 09:00",
        "unidade": "GPF",
        "usuario": "admin",
    },
]

# 4 tipos de processo with "Fiscalização" → Q3
_FIXTURE_TIPOS_FISCALIZACAO: list[dict] = [
    {"id": "101", "nome": "Fiscalização: Rotina"},
    {"id": "102", "nome": "Fiscalização: Especial"},
    {"id": "103", "nome": "Fiscalização: Preventiva"},
    {"id": "104", "nome": "Fiscalização: Emergencial"},
]

# 20 processos for resumo_processos: 7 Pessoal: Férias + 13 others → Q8
_FIXTURE_PROCESSOS_CAIXA: list[dict] = [
    {
        "idProcedimento": str(9000000 + i),
        "protocoloFormatado": f"50300.00{9000 + i:04d}/2025-00",
        "atributos": {"tipoProcesso": "Pessoal: Férias", "usuarioAtribuido": ""},
        "status": {
            "nivelAcessoGlobal": "0",
            "processoEmTramitacao": "S",
            "processoSobrestado": "N",
        },
    }
    for i in range(7)
] + [
    {
        "idProcedimento": str(8000000 + i),
        "protocoloFormatado": f"50300.00{8000 + i:04d}/2025-00",
        "atributos": {
            "tipoProcesso": [
                "Administrativo: Geral",
                "Fiscalização: Rotina",
                "Jurídico: Contrato",
            ][i % 3],
            "usuarioAtribuido": "",
        },
        "status": {
            "nivelAcessoGlobal": "0",
            "processoEmTramitacao": "S",
            "processoSobrestado": "N",
        },
    }
    for i in range(13)
]


class FixtureBackend:
    """Synthetic SEI backend for eval tests.

    Returns pre-defined data matching the golden.xml expected answers
    for fixture process 50300.001234/2025-00.
    """

    name = "fixture"
    has_rest = True

    async def consultar_processo(self, _processo: str) -> dict:
        """Return fixture processo data."""
        return {
            "IdProcedimento": FIXTURE_ID_PROCEDIMENTO,
            "ProtocoloProcedimentoFormatado": FIXTURE_PROTOCOLO,
            "NomeTipoProcedimento": "Fiscalização: Rotina",
            "especificacao": "Fiscalização de rotina",
            "nivelAcesso": "Público",
            "interessados": [
                {"nome": "Departamento Jurídico"},
                {"nome": "Diretoria de Fiscalização"},
            ],
            "total_documentos": 12,
            "documentos": [],
        }

    async def arvore_processo(self, _processo: str) -> dict:
        """Return fixture document tree."""
        return {
            "documentos": list(_FIXTURE_DOCUMENTOS),
            "total_documentos": 12,
        }

    async def listar_documentos(self, processo: str) -> dict:
        """Return fixture document list (same as arvore)."""
        return await self.arvore_processo(processo)

    async def listar_atividades(self, _processo: str) -> dict:
        """Return fixture andamentos."""
        return {
            "processo": {"protocolo": FIXTURE_PROTOCOLO},
            "andamentos": list(_FIXTURE_ANDAMENTOS),
            "total_andamentos": len(_FIXTURE_ANDAMENTOS),
        }

    async def listar_unidades_processo(self, _processo: str) -> list[dict]:
        """Return fixture open units."""
        return [
            {"id_unidade": "101", "sigla": "GPF", "nome": "Gerência de Fiscalização"},
            {
                "id_unidade": "102",
                "sigla": "GRP",
                "nome": "Gerência de Relacionamento com o Público",
            },
        ]

    async def listar_interessados(self, _processo: str) -> list[dict]:
        """Return fixture interessados."""
        return [
            {"id": "501", "nome": "Departamento Jurídico"},
            {"id": "502", "nome": "Diretoria de Fiscalização"},
        ]

    async def listar_sobrestamentos(self, _processo: str) -> list[dict]:
        """Return fixture sobrestamentos."""
        return [
            {
                "data": "10/05/2025",
                "motivo": "aguardando manifestação da área técnica",
                "tipo": "sobrestamento",
            },
            {
                "data": "15/05/2025",
                "motivo": "",
                "tipo": "dessobrestamento",
            },
        ]

    async def consultar_atribuicao(self, _processo: str) -> dict:
        """Return fixture atribuição."""
        return {"login": "joao.silva", "nome": "João da Silva"}

    async def listar_historico_atribuicoes(self, _processo: str) -> dict:
        """Return fixture atribuição history."""
        return {
            "atual": "joao.silva",
            "anterior": "maria.costa",
            "atribuidos": ["joao.silva", "maria.costa"],
            "historico": [],
        }

    async def buscar_documento(self, numero_sei: str, _processo: str = "") -> dict:
        """Return fixture document lookup."""
        return {
            "id": numero_sei,
            "numero_sei": numero_sei,
            "tipo_documento": "Ofício",
            "nome_composto": f"Ofício GPF {numero_sei}",
            "sigla_unidade": "GPF",
            "processo": FIXTURE_PROTOCOLO,
            "id_procedimento": FIXTURE_ID_PROCEDIMENTO,
        }

    async def pesquisar_tipos_processo(
        self,
        filtro: str = "",
        _limit: int = 50,
        _pagina: int = 0,
        _cursor: str = "",
    ) -> dict:
        """Return fixture tipos de processo filtered by name."""
        filtro_lower = filtro.lower()
        if (
            filtro_lower
            and "fiscalização" not in filtro_lower
            and "fiscalizacao" not in filtro_lower
        ):
            return {"tipos": [], "total": 0, "proximo_cursor": None}
        tipos = list(_FIXTURE_TIPOS_FISCALIZACAO)
        return {"tipos": tipos, "total": len(tipos), "proximo_cursor": None}

    async def pesquisar_tipos_documento(
        self,
        _filtro: str = "",
        _limit: int = 50,
        _pagina: int = 0,
        _cursor: str = "",
    ) -> dict:
        """Return empty tipos de documento for eval fixture."""
        return {"tipos": [], "total": 0, "proximo_cursor": None}

    async def verificar_acesso(self, _processo: str) -> dict:
        """Return fixture access check."""
        return {"tem_acesso": True, "nivel_acesso": "Público"}

    async def listar_relacionamentos(self, _processo: str) -> list[dict]:
        """Return empty related processes list."""
        return []

    async def unidade_atual(self) -> dict:
        """Return fixture current unit."""
        return {"id_unidade": "101", "sigla": "GPF", "nome": "Gerência de Fiscalização"}

    async def listar_unidades(self) -> list[dict]:
        """Return fixture units list."""
        return [
            {"id_unidade": "101", "sigla": "GPF", "nome": "Gerência de Fiscalização"},
            {"id_unidade": "102", "sigla": "GRP", "nome": "Gerência de Relacionamento"},
        ]

    async def pesquisar_unidades(
        self,
        _filtro: str = "",
        _limit: int = 50,
        _pagina: int = 0,
    ) -> dict:
        """Return fixture units search."""
        return {
            "unidades": [{"id_unidade": "101", "sigla": "GPF", "nome": "Gerência de Fiscalização"}],
            "total": 1,
        }

    async def pesquisar_marcadores(self, _filtro: str = "") -> list[dict]:
        """Return fixture marcadores."""
        return [
            {"id": "1", "nome": "Urgente", "cor": "Vermelho"},
            {"id": "2", "nome": "Em análise", "cor": "Amarelo"},
        ]

    async def pesquisar_usuarios(
        self,
        _filtro: str = "",
        _id_orgao: str = "",
        _limit: int = 50,
        _pagina: int = 0,
        _cursor: str = "",
    ) -> dict:
        """Return fixture usuarios search."""
        return {
            "usuarios": [{"id": "u1", "login": "joao.silva", "nome": "João da Silva"}],
            "total": 1,
        }

    async def listar_usuarios(
        self,
        _filtro: str = "",
        *,
        _apenas_unidade: bool = True,
    ) -> dict:
        """Return fixture user list."""
        return {
            "usuarios": [{"id": "u1", "login": "joao.silva", "nome": "João da Silva"}],
            "total": 1,
        }

    def __getattr__(self, op: str):
        """Fallback for operations not implemented in the fixture."""

        async def _not_impl(*_args: object, **_kwargs: object) -> dict:
            msg = f"Operação '{op}' não disponível no ambiente de eval (fixture)."
            raise SEINotFoundError(msg)

        return _not_impl


class FakeRESTClient:
    """Fake REST client for sei_resumo_processos.

    sei_resumo_processos in server.py calls _get_client(ctx) directly (REST-only tool).
    This fake implements listar_processos with fixture data.
    """

    base_url = "http://fake-sei.test/api/v2"

    async def listar_processos(self, _filtros: object = None) -> dict:
        """Return fixture process list for resumo_processos aggregation."""
        return {
            "processos": list(_FIXTURE_PROCESSOS_CAIXA),
            "tem_proxima": False,
            "total": len(_FIXTURE_PROCESSOS_CAIXA),
        }
