"""Mixin REST — leitura e escrita de processos."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import httpx

from todos.backends.models import EnvioProcesso, FiltroListagemProcessos
from todos.backends.rest._session import _RestMixin
from todos.exceptions import SEIError, SEINotFoundError, SEIValidationError

if TYPE_CHECKING:
    from todos.backends.models import (
        FiltrosPesquisaProcessos,
        NovoProcesso,
    )

logger = logging.getLogger(__name__)

_RE_ID_NUMERICO = re.compile(r"^\d+$")
_MAX_ERROS_LEN = 500


class ProcessosRest(_RestMixin):
    """Operações REST de leitura e escrita de processos."""

    async def consultar_processo(self, processo: str) -> dict:
        """Consulta os dados completos de um processo pelo protocolo formatado."""
        return await self._rest.consultar_processo_completo(processo)

    async def listar_documentos(self, processo: str) -> list[dict]:
        """Lista os documentos de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.listar_documentos(id_proc)

    async def listar_processos(
        self, pagina: int = 0, apenas_meus: str = "", tipo: str = "", filtro: str = ""
    ) -> dict:
        """Lista os processos abertos na unidade atual."""
        return await self._rest.listar_processos(
            FiltroListagemProcessos(
                pagina=pagina, apenas_meus=apenas_meus, tipo=tipo, filtro=filtro
            )
        )

    async def pesquisar_processos(self, filtros: FiltrosPesquisaProcessos) -> dict:
        """Pesquisa processos por texto e filtros estruturados."""
        return await self._rest.pesquisar_processos(filtros)

    async def listar_atividades(self, processo: str, tipo_historico: str = "R") -> dict:
        """Lista o histórico de andamentos de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.listar_atividades(id_proc)

    async def listar_unidades_processo(self, processo: str) -> list[dict]:
        """Lista as unidades onde o processo está aberto."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.listar_unidades_processo(id_proc)

    async def listar_interessados(self, processo: str) -> list[dict]:
        """Lista os interessados de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.listar_interessados(id_proc)

    async def listar_sobrestamentos(self, processo: str) -> list[dict]:
        """Lista o histórico de sobrestamentos de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.listar_sobrestamentos(id_proc)

    async def verificar_acesso(self, processo: str) -> dict:
        """Verifica se o usuário tem acesso a um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.verificar_acesso(id_proc)

    async def listar_relacionamentos(self, processo: str) -> list[dict]:
        """Lista os processos relacionados a um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.listar_relacionamentos(id_proc)

    async def consultar_atribuicao(self, processo: str) -> dict:
        """Retorna o usuário atualmente atribuído ao processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.consultar_atribuicao(id_proc)

    async def criar_processo(self, dados: NovoProcesso) -> dict:
        """Cria um novo processo."""
        return await self._rest.criar_processo(dados)

    async def alterar_processo(
        self,
        processo: str,
        especificacao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        observacao: str = "",
    ) -> dict:
        """Altera metadados de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.alterar_processo(
            id_procedimento=id_proc,
            especificacao=especificacao,
            nivel_acesso=nivel_acesso,
            hipotese_legal=hipotese_legal,
            observacao=observacao,
        )

    async def enviar_processo(self, processo: str, dados: EnvioProcesso) -> dict:
        """Tramita um processo para uma ou mais unidades (resolve sigla→id via REST)."""
        destinos = [d.strip() for d in dados.unidades_destino.split(",") if d.strip()]
        if not destinos:
            msg = "unidades_destino não pode ser vazio."
            raise SEIValidationError(msg)
        ids_resolvidos: list[str] = []
        for destino in destinos:
            if _RE_ID_NUMERICO.match(destino):
                ids_resolvidos.append(destino)
                continue
            result = await self._rest.pesquisar_unidades(filtro=destino, limit=10)
            unidades = result.get("unidades", [])
            exact = next(
                (u for u in unidades if u.get("sigla", "").upper() == destino.upper()),
                None,
            )
            if not exact:
                candidatos = ", ".join(u.get("sigla", "") for u in unidades)
                msg = (
                    f"Unidade '{destino}' não encontrada. Candidatos: {candidatos}. "
                    "Use sei_pesquisar_unidades ou informe o ID numérico diretamente."
                )
                raise SEIValidationError(
                    msg,
                    error_code="UNIDADE_NAO_ENCONTRADA",
                    recoverable=True,
                    suggested_next_tool="sei_pesquisar_unidades",
                    suggested_args={"filtro": destino},
                )
            ids_resolvidos.append(str(exact.get("id", "")))
        resolved = EnvioProcesso(
            unidades_destino=",".join(ids_resolvidos),
            manter_aberto=dados.manter_aberto,
            remover_anotacao=dados.remover_anotacao,
            enviar_email=dados.enviar_email,
            data_retorno=dados.data_retorno,
            dias_retorno=dados.dias_retorno,
            dias_uteis_retorno=dados.dias_uteis_retorno,
            reabrir=dados.reabrir,
        )
        return await self._rest.enviar_processo(processo, resolved)

    async def concluir_processo(self, processo: str) -> dict:
        """Conclui (encerra) um processo na unidade atual."""
        return await self._rest.concluir_processo(processo)

    async def reabrir_processo(self, processo: str) -> dict:
        """Reabre um processo concluído na unidade atual."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.reabrir_processo(id_proc)

    async def atribuir_processo(self, processo: str, usuario: str) -> dict:
        """Atribui um processo a um usuário (resolve nome→id via REST, tenta candidatos)."""
        if _RE_ID_NUMERICO.match(usuario):
            return await self._rest.atribuir_processo(processo, usuario)
        result = await self._rest.listar_usuarios(filtro=usuario)
        candidatos = result.get("usuarios", [])
        if not candidatos:
            msg = f"Nenhum usuário encontrado com '{usuario}'. Use sei_pesquisar_usuarios."
            raise SEINotFoundError(
                msg,
                error_code="USUARIO_NAO_ENCONTRADO",
                recoverable=True,
                suggested_next_tool="sei_pesquisar_usuarios",
                suggested_args={"filtro": usuario},
            )
        erros: list[str] = []
        for u in candidatos:
            id_usuario = u.get("id_usuario")
            if id_usuario is None:
                erros.append(
                    f"{u.get('nome', '')} ({u.get('sigla', '')}): id_usuario ausente na resposta"
                )
                continue
            try:
                return await self._rest.atribuir_processo(processo, str(id_usuario))
            except (SEIError, httpx.HTTPError) as e:
                logger.warning("Falha ao atribuir processo a %s: %s", id_usuario, e)
                erros.append(f"{u.get('nome', '')} ({u.get('sigla', '')}): {e}")
        tentativas_str = "; ".join(erros)
        if len(tentativas_str) > _MAX_ERROS_LEN:
            tentativas_str = tentativas_str[:_MAX_ERROS_LEN] + " [truncado]"
        msg = (
            f"Não foi possível atribuir o processo a nenhum dos {len(candidatos)} "
            f"usuários com '{usuario}': {tentativas_str}"
        )
        raise SEIValidationError(msg)

    async def remover_atribuicao(self, processo: str) -> dict:
        """Remove a atribuição de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.remover_atribuicao(id_proc)

    async def receber_processo(self, processo: str) -> dict:
        """Recebe (aceita) um processo tramitado para a unidade."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.receber_processo(id_proc)

    async def registrar_andamento(self, processo: str, descricao: str) -> dict:
        """Registra um andamento manual no histórico do processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.registrar_andamento(id_proc, descricao)

    async def sobrestar_processo(
        self, processo: str, motivo: str, processo_vinculado: str = ""
    ) -> dict:
        """Sobresta (suspende) um processo."""
        id_proc = await self._resolver_processo(processo)
        proto_vinculado = ""
        if processo_vinculado:
            proto_vinculado = await self._resolver_processo(processo_vinculado)
        return await self._rest.sobrestar_processo(
            id_procedimento=id_proc,
            motivo=motivo,
            protocolo_vinculado=proto_vinculado,
        )

    async def remover_sobrestamento(self, processo: str) -> dict:
        """Remove o sobrestamento de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.remover_sobrestamento(id_proc)

    async def criar_observacao(self, processo: str, descricao: str) -> dict:
        """Cria uma observação em um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.criar_observacao(id_proc, descricao)

    async def criar_anotacao(self, processo: str, descricao: str, prioridade: str = "1") -> dict:
        """Cria uma anotação (post-it) em um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.criar_anotacao(id_proc, descricao, prioridade)

    async def sugestao_assuntos_processo(self, id_tipo_processo: str) -> list[dict]:
        """Sugere assuntos para um tipo de processo."""
        return await self._rest.sugestao_assuntos_processo(id_tipo_processo)
