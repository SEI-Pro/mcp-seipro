"""Mixin web: leitura e escrita de processos."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from todos.backends.web._session import _WebMixin
from todos.exceptions import ProcessoEmOutraUnidadeError, SEIError, SEIValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from todos.backends.base import EnvioProcesso, FiltrosPesquisaProcessos, NovoProcesso

_T = TypeVar("_T")


def _traduzir_erro_processo(e: SEIError) -> SEIError | None:
    """Mapeia um erro web de processo numa exceção específica, ou None se desconhecido."""
    low = str(e).lower()
    if (
        "aberto na unidade" in low
        or "está aberto" in low
        or "esta aberto" in low
        or "aberto em outra" in low
        or "aberto nas unidades" in low
    ):
        return ProcessoEmOutraUnidadeError(
            "Processo aberto em outra(s) unidade(s). Conclua o processo nessas unidades "
            "(sei_concluir_processo) antes desta operação, ou verifique "
            "sei_listar_unidades_processo."
        )
    return None


class ProcessosWeb(_WebMixin):
    """Operações web de leitura e escrita de processos."""

    async def _proc(self, coro: Awaitable[_T]) -> _T:
        """Executa uma chamada de processo, traduzindo erros conhecidos do SEI."""
        try:
            return await coro
        except SEIError as e:
            especifico = _traduzir_erro_processo(e)
            if especifico is not None:
                raise especifico from e
            raise

    # ------------------------------------------------------------------
    # Operações de leitura de processos
    # ------------------------------------------------------------------

    async def consultar_processo(self, processo: str) -> dict:
        """Consulta os dados completos de um processo pelo protocolo formatado."""
        return await self._web.consultar_processo(processo)

    async def consultar_processo_detalhe(self, processo: str) -> dict:
        """Retorna detalhes (unidades abertas, interessados, sobrestamentos)."""
        return await self._web.consultar_processo_detalhe(processo)

    async def listar_relacionamentos(self, processo: str) -> dict:
        """Lista processos relacionados (da árvore web — não requer mod-wssei 3.0.2+)."""
        result = await self._web.consultar_processo(processo)
        rels = result.get("relacionados", [])
        return {"relacionados": rels, "total_itens": len(rels)}

    async def arvore_processo(self, processo: str) -> dict:
        """Retorna a árvore de documentos de um processo."""
        return await self._web.listar_documentos(processo)

    async def listar_documentos(self, processo: str) -> dict:
        """Lista os documentos de um processo."""
        return await self._web.listar_documentos(processo)

    async def listar_processos(
        self, pagina: int = 0, apenas_meus: str = "", tipo: str = "", filtro: str = ""
    ) -> dict:
        """Lista os processos abertos na unidade atual."""
        return await self._web.listar_processos(
            detalhada=True,
            pagina=pagina,
            apenas_meus=(apenas_meus.upper() == "S"),
            tipo=tipo,
            filtro=filtro,
        )

    async def pesquisar_processos(self, filtros: FiltrosPesquisaProcessos) -> dict:
        """Pesquisa processos por texto e filtros estruturados."""
        q_web = " ".join(filter(None, [filtros.palavras_chave, filtros.busca_rapida]))
        return await self._web.pesquisar_processos_web(
            q=q_web,
            descricao=filtros.descricao,
            data_inicio=filtros.data_inicio,
            data_fim=filtros.data_fim,
            pagina=filtros.pagina,
        )

    async def listar_atividades(self, processo: str) -> dict:
        """Lista o histórico de andamentos de um processo."""
        return await self._web.listar_atividades(processo)

    async def listar_unidades_processo(self, processo: str) -> list[dict]:
        """Lista as unidades onde o processo está aberto."""
        detalhe = await self._web.consultar_processo_detalhe(processo)
        return detalhe.get("unidades_abertas", [])

    async def listar_interessados(self, processo: str) -> list[dict]:
        """Lista os interessados de um processo."""
        detalhe = await self._web.consultar_processo_detalhe(processo)
        return detalhe.get("interessados", [])

    async def listar_sobrestamentos(self, processo: str) -> list[dict]:
        """Lista o histórico de sobrestamentos de um processo."""
        detalhe = await self._web.consultar_processo_detalhe(processo)
        return detalhe.get("sobrestamentos", [])

    async def verificar_acesso(self, processo: str) -> dict:
        """Verifica se o usuário tem acesso a um processo."""
        return await self._web.verificar_acesso_web(processo)

    async def consultar_atribuicao(self, processo: str) -> dict:
        """Retorna o usuário atualmente atribuído ao processo."""
        return await self._web.consultar_atribuicao_web(processo)

    # ------------------------------------------------------------------
    # Operações de escrita de processos
    # ------------------------------------------------------------------

    async def criar_processo(self, dados: NovoProcesso) -> dict:
        """Cria um novo processo."""
        assuntos_ids = [a.strip() for a in dados.assuntos.split(",") if a.strip()]
        interessados_ids = [i.strip() for i in dados.interessados.split(",") if i.strip()]
        return await self._web.criar_processo_web(
            tipo_processo=dados.tipo_processo,
            especificacao=dados.especificacao,
            assuntos_ids=assuntos_ids,
            interessados_ids=interessados_ids,
            nivel_acesso=dados.nivel_acesso,
            hipotese_legal=dados.hipotese_legal,
        )

    async def alterar_processo(
        self,
        processo: str,
        especificacao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        observacao: str = "",
    ) -> dict:
        """Altera metadados de um processo (form procedimento_alterar)."""
        return await self._web.alterar_processo_web(
            protocolo=processo,
            especificacao=especificacao,
            nivel_acesso=nivel_acesso,
            hipotese_legal=hipotese_legal,
            observacao=observacao,
        )

    async def enviar_processo(self, processo: str, dados: EnvioProcesso) -> dict:
        """Tramita um processo para uma ou mais unidades."""
        destinos = [d.strip() for d in dados.unidades_destino.split(",") if d.strip()]
        if not destinos:
            msg = "unidades_destino não pode ser vazio."
            raise SEIValidationError(msg)
        ids_resolvidos: list[str] = []
        for destino in destinos:
            if destino.isdigit():
                ids_resolvidos.append(destino)
                continue
            matches = await self._web.autocomplete_unidades(destino)
            exact = next(
                (m for m in matches if m.get("sigla", "").upper() == destino.upper()),
                None,
            )
            if not exact:
                candidatos = ", ".join(m.get("sigla", "") for m in matches)
                msg = (
                    f"Unidade '{destino}' não encontrada via autocomplete web. "
                    f"Candidatos: {candidatos}. Informe o ID numérico diretamente."
                )
                raise SEIValidationError(msg)
            ids_resolvidos.append(exact["id"])
        return await self._web.enviar_processo_web(
            protocolo=processo,
            unidades_ids=ids_resolvidos,
            manter_aberto=dados.manter_aberto,
            remover_anotacao=dados.remover_anotacao,
            enviar_email=dados.enviar_email,
            data_retorno=dados.data_retorno,
            dias_retorno=dados.dias_retorno,
        )

    async def concluir_processo(self, processo: str) -> dict:
        """Conclui (encerra) um processo na unidade atual."""
        return await self._proc(self._web.executar_acao_processo(processo, "procedimento_concluir"))

    async def reabrir_processo(self, processo: str) -> dict:
        """Reabre um processo concluído na unidade atual."""
        return await self._web.reabrir_processo_web(processo)

    async def atribuir_processo(self, processo: str, usuario: str) -> dict:
        """Atribui um processo a um usuário da unidade."""
        form_info = await self._web.obter_form_acao(processo, "procedimento_atribuicao_cadastrar")
        opcoes_usuario = form_info.get("selects", {}).get("selAtribuicao", [])
        if not opcoes_usuario:
            msg = "Nenhum usuário disponível para atribuição nesta unidade."
            raise SEIValidationError(msg)
        alvo = usuario.strip()
        alvo_lower = alvo.lower()
        # Match exato por value (id) ou por texto; substring só desempata quando
        # houver um único candidato (evita atribuir à pessoa errada).
        exatos = [
            o
            for o in opcoes_usuario
            if o.get("value") == alvo or o.get("texto", "").strip().lower() == alvo_lower
        ]
        if exatos:
            id_usuario = exatos[0]["value"]
        else:
            parciais = [o for o in opcoes_usuario if alvo_lower in o.get("texto", "").lower()]
            if len(parciais) == 1:
                id_usuario = parciais[0]["value"]
            elif not parciais:
                msg = f"Usuário '{usuario}' não encontrado no form de atribuição."
                raise SEIValidationError(msg)
            else:
                candidatos = ", ".join(o.get("texto", "") for o in parciais)
                msg = (
                    f"Usuário '{usuario}' é ambíguo ({len(parciais)} candidatos: "
                    f"{candidatos}). Informe o login/id exato."
                )
                raise SEIValidationError(msg)
        return await self._web.executar_acao_processo(
            processo, "procedimento_atribuicao_cadastrar", {"selAtribuicao": id_usuario}
        )

    async def remover_atribuicao(self, processo: str) -> dict:
        """Remove a atribuição de um processo (re-submete o form com seleção vazia)."""
        return await self._web.executar_acao_processo(
            processo, "procedimento_atribuicao_cadastrar", {"selAtribuicao": ""}
        )

    async def receber_processo(self, processo: str) -> dict:
        """Recebe (aceita) um processo tramitado para a unidade."""
        return await self._web.executar_acao_processo(processo, "procedimento_receber")

    async def registrar_andamento(self, processo: str, descricao: str) -> dict:
        """Registra um andamento manual no histórico do processo."""
        return await self._web.executar_acao_processo(
            processo, "procedimento_atualizar_andamento", {"txaDescricao": descricao}
        )

    async def executar_acao(self, processo: str, acao: str) -> dict:
        """Executa uma ação genérica do toolbar do processo (web)."""
        return await self._web.executar_acao_processo(processo, acao)

    async def sobrestar_processo(
        self, processo: str, motivo: str, processo_vinculado: str = ""
    ) -> dict:
        """Sobresta (suspende) um processo."""
        campos: dict[str, str] = {"txaMotivo": motivo}
        if processo_vinculado:
            campos["txtProcedimentoDestino"] = processo_vinculado
            campos["txtIdentificacaoProcedimentoDestino"] = processo_vinculado
        return await self._proc(
            self._web.executar_acao_processo(processo, "procedimento_sobrestar", campos)
        )

    async def remover_sobrestamento(self, processo: str) -> dict:
        """Remove o sobrestamento de um processo (via lista de sobrestados)."""
        return await self._web.remover_sobrestamento_web(processo)

    async def remover_anotacao(self, processo: str) -> dict:
        """Remove a anotação (post-it) de um processo."""
        return await self._web.remover_anotacao_web(processo)

    async def listar_historico_atribuicoes(self, processo: str) -> dict:
        """Lista o histórico de atribuições de um processo (anterior/atribuídos)."""
        return await self._web.listar_historico_atribuicoes_web(processo)

    async def criar_anotacao(self, processo: str, descricao: str, prioridade: str = "1") -> dict:
        """Cria uma anotação (post-it) em um processo."""
        campos = {"txaDescricao": descricao}
        if prioridade == "2":
            # Checkbox "Sin" do SEI: o backend testa == "S" (o value no HTML é
            # vazio); enviar "1" perde a flag silenciosamente.
            campos["chkSinPrioridade"] = "S"
        return await self._web.executar_acao_processo(processo, "anotacao_registrar", campos)

    async def gerar_pdf_processo(self, processo: str) -> bytes:
        """Gera o PDF consolidado de um processo."""
        return await self._web.gerar_pdf_processo(processo)

    async def gerar_zip_processo(self, processo: str) -> bytes:
        """Gera o ZIP com os documentos de um processo."""
        return await self._web.gerar_zip_processo(processo)
