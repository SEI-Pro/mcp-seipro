"""Cliente REST genérico para o mod-wssei v2 do SEI."""

import base64
import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class SEIClient:
    """Cliente REST assíncrono para qualquer instância do SEI com mod-wssei v2."""

    def __init__(self):
        self.base_url = os.environ["SEI_URL"].rstrip("/")
        self._usuario = os.environ["SEI_USUARIO"]
        self._senha = os.environ["SEI_SENHA"]
        self._orgao = os.environ.get("SEI_ORGAO", "0")
        self._contexto = os.environ.get("SEI_CONTEXTO", "")
        self._token: Optional[str] = None
        self._unidade_ativa: Optional[str] = None

        verify_ssl = os.environ.get("SEI_VERIFY_SSL", "true").lower() != "false"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0, read=90.0),
            verify=verify_ssl,
        )

    async def _get_headers(self) -> dict:
        if not self._token:
            await self.autenticar()
        return {"token": self._token}

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Faz request com re-autenticação automática em caso de 401/403."""
        headers = await self._get_headers()
        kwargs.setdefault("headers", {}).update(headers)
        resp = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        if resp.status_code in (401, 403):
            logger.info("Token expirado, re-autenticando...")
            await self.autenticar()
            kwargs["headers"].update({"token": self._token})
            resp = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        resp.raise_for_status()
        return resp

    async def autenticar(self) -> str:
        """Autentica no SEI e obtém token."""
        resp = await self._client.post(
            f"{self.base_url}/autenticar",
            data={
                "usuario": self._usuario,
                "senha": self._senha,
                "orgao": self._orgao,
                "contexto": self._contexto,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Falha na autenticação SEI: {data.get('mensagem')}")
        self._token = data["data"]["token"]
        logger.info("Autenticação SEI bem-sucedida")
        return self._token

    async def consultar_processo(self, protocolo_formatado: str) -> dict:
        """Consulta processo pelo número formatado.
        Retorna: {IdProcedimento, ProtocoloProcedimentoFormatado, NomeTipoProcedimento, ...}
        """
        resp = await self._request(
            "GET",
            "/processo/consultar",
            params={"protocoloFormatado": protocolo_formatado},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao consultar processo: {data.get('mensagem')}")
        return data["data"]

    async def listar_documentos(
        self, id_procedimento: str, limit: int = 200, start: int = 0
    ) -> list[dict]:
        """Lista documentos de um processo.
        Retorna array de: {id, atributos: {tipoDocumento, tipo, protocoloFormatado, ...}}
        """
        resp = await self._request(
            "GET",
            f"/documento/listar/{id_procedimento}",
            params={"limit": limit, "start": start},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar documentos: {data.get('mensagem')}")
        return data.get("data", [])

    async def consultar_documento_interno(self, id_documento: str) -> dict:
        """Consulta metadados de um documento interno pelo id.
        Retorna: id, tipo, unidade geradora, assinaturas, etc.
        """
        resp = await self._request(
            "GET", f"/documento/interno/consultar/{id_documento}"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao consultar documento {id_documento}: {data.get('mensagem')}")
        return data["data"]

    async def listar_assinaturas(self, id_documento: str) -> list[dict]:
        """Lista assinaturas de um documento."""
        resp = await self._request(
            "GET", f"/documento/listar/assinaturas/{id_documento}"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar assinaturas: {data.get('mensagem')}")
        return data.get("data", [])

    async def visualizar_documento_interno(self, id_documento: str) -> str:
        """Visualiza conteúdo HTML de documento interno (tipoDocumento=I)."""
        resp = await self._request("GET", f"/documento/{id_documento}/interno/visualizar")
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao visualizar documento {id_documento}: {data.get('mensagem')}")
        return data["data"]

    async def baixar_anexo(self, id_documento: str) -> bytes:
        """Baixa anexo de documento externo (tipoDocumento=X). Retorna bytes."""
        resp = await self._request("GET", f"/documento/baixar/anexo/{id_documento}")
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            data = resp.json()
            if not data.get("sucesso"):
                raise Exception(f"Erro ao baixar anexo {id_documento}: {data.get('mensagem')}")
            return base64.b64decode(data["data"])
        return resp.content

    async def criar_documento_interno(
        self,
        id_procedimento: str,
        id_serie: str,
        descricao: str = "",
        nivel_acesso: str = "0",
        id_unidade: str = "",
    ) -> dict:
        """Cria documento interno (nativo) em um processo SEI.
        Retorna: {idDocumento, protocoloDocumentoFormatado}
        """
        resp = await self._request(
            "POST",
            f"/documento/{id_procedimento}/interno/criar",
            data={
                "idSerie": id_serie,
                "numero": "",
                "descricao": descricao,
                "dataElaboracao": "",
                "nivelAcesso": nivel_acesso,
                "idHipoteseLegal": "",
                "grauSigilo": "",
                "idUnidadeGeradoraProtocolo": id_unidade,
                "assuntos": "",
                "interessados": "",
                "remetente": "",
                "destinatarios": "",
                "observacao": "",
                "idTextoPadraoInterno": "",
                "idTipoConferencia": "",
                "protocoloDocumentoModelo": "",
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar documento interno: {data.get('mensagem')}")
        return data["data"]

    async def listar_secao_documento(self, id_documento: str) -> dict:
        """Lista seções de um documento interno.
        Retorna: {secoes: [{id, idSecaoModelo, conteudo, ...}], ultimaVersaoDocumento: N}
        """
        resp = await self._request(
            "GET",
            "/documento/secao/listar",
            params={"id": id_documento},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar seções do documento {id_documento}: {data.get('mensagem')}")
        return data.get("data", {})

    async def alterar_secao_documento(
        self,
        id_documento: str,
        secoes: list[dict],
        versao: str = "1",
    ) -> dict:
        """Altera conteúdo HTML das seções de um documento interno.
        secoes: [{id, idSecaoModelo, conteudo}, ...]
        """
        secoes_json = json.dumps(secoes)
        resp = await self._request(
            "POST",
            "/documento/secao/alterar",
            data={
                "documento": id_documento,
                "secoes": secoes_json,
                "versao": versao,
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao alterar seção do documento {id_documento}: {data.get('mensagem')}")
        return data.get("data", {})

    # ------------------------------------------------------------------
    # Usuário e unidades
    # ------------------------------------------------------------------

    async def listar_unidades_usuario(self) -> list[dict]:
        """Lista unidades às quais o usuário autenticado tem acesso."""
        resp = await self._request("GET", "/usuario/unidades")
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar unidades: {data.get('mensagem')}")
        return data.get("data", [])

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca a unidade ativa do usuário."""
        resp = await self._request(
            "POST", "/usuario/alterar/unidade", data={"unidade": id_unidade}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao trocar unidade: {data.get('mensagem')}")
        self._unidade_ativa = id_unidade
        return {"mensagem": data.get("mensagem")}

    async def pesquisar_unidades(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa unidades disponíveis no SEI."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/unidade/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar unidades: {data.get('mensagem')}")
        return self._paginated(data, "unidades", data.get("data", []), start, limit)

    async def listar_usuarios(
        self, filtro: str = "", id_unidade: str = "", apenas_unidade: bool = True
    ) -> dict:
        """Lista usuários.

        - apenas_unidade=True (padrão): só usuários com permissão na unidade
          informada (ou na unidade ativa se id_unidade for vazio).
          Usa o parâmetro 'unidade' da API.
        - apenas_unidade=False: todos os usuários do órgão.
        O filtro por nome/sigla é aplicado client-side.
        """
        params: dict = {"limit": 1000, "start": 0}
        if apenas_unidade:
            unid = id_unidade or self._unidade_ativa
            if unid:
                params["unidade"] = unid
        resp = await self._request("GET", "/usuario/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar usuários: {data.get('mensagem')}")

        usuarios = data.get("data", [])

        if filtro:
            filtro_lower = filtro.lower()
            usuarios = [
                u for u in usuarios
                if filtro_lower in u.get("nome", "").lower()
                or filtro_lower in u.get("sigla", "").lower()
            ]

        return {
            "usuarios": usuarios,
            "total_itens": len(usuarios),
        }

    # ------------------------------------------------------------------
    # Processos — listar, pesquisar, criar, enviar, concluir, reabrir, atribuir
    # ------------------------------------------------------------------

    async def listar_processos(
        self,
        limit: int = 50,
        start: int = 0,
        tipo: str = "",
        usuario: str = "",
        apenas_meus: str = "",
        filtro: str = "",
    ) -> dict:
        """Lista processos da caixa da unidade atual.
        Retorna: {data: [...], total: N}
        """
        params: dict = {"limit": limit, "start": start}
        if tipo:
            params["tipo"] = tipo
        if usuario:
            params["usuario"] = usuario
        if apenas_meus:
            params["apenasMeus"] = apenas_meus
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/processo/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar processos: {data.get('mensagem')}")
        return self._paginated(data, "processos", data.get("data", []), start, limit)

    async def pesquisar_processos(
        self,
        palavras_chave: str = "",
        descricao: str = "",
        busca_rapida: str = "",
        data_inicio: str = "",
        data_fim: str = "",
        limit: int = 50,
        start: int = 0,
    ) -> dict:
        """Pesquisa processos via busca textual (Solr).
        Retorna: {data: [...], total: N}
        """
        params: dict = {"limit": limit, "start": start}
        if palavras_chave:
            params["palavrasChave"] = palavras_chave
        if descricao:
            params["descricao"] = descricao
        if busca_rapida:
            params["buscaRapida"] = busca_rapida
        if data_inicio:
            params["dataInicio"] = data_inicio
        if data_fim:
            params["dataFim"] = data_fim
        resp = await self._request("GET", "/processo/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar processos: {data.get('mensagem')}")
        return self._paginated(data, "processos", data.get("data", []), start, limit)

    async def alterar_processo(
        self,
        id_procedimento: str,
        especificacao: str | None = None,
        nivel_acesso: str | None = None,
        hipotese_legal: str | None = None,
        observacao: str | None = None,
    ) -> dict:
        """Altera metadados de um processo (especificação, nível de acesso, etc.).
        A API exige todos os campos — busca os atuais e sobrescreve os alterados.
        """
        # Buscar dados atuais do processo
        proc = await self.consultar_processo_por_id(id_procedimento)

        # Montar payload com dados atuais + alterações
        na = nivel_acesso if nivel_acesso is not None else proc.get("nivelAcesso", "0")

        # Se mudando para público, limpar hipótese legal
        if na == "0":
            hl = ""
        else:
            hl = hipotese_legal if hipotese_legal is not None else proc.get("hipoteseLegal", "")

        # Assuntos: reenviar os existentes (a API exige)
        assuntos_atuais = proc.get("assuntos", [])
        assuntos_ids = ",".join(str(a.get("id", "")) for a in assuntos_atuais)

        payload = {
            "idTipoProcesso": proc.get("tipoProcesso", ""),
            "especificacao": especificacao if especificacao is not None else proc.get("especificacao", ""),
            "nivelAcesso": na,
            "idHipoteseLegal": hl,
            "grauSigilo": proc.get("grauSigilo", ""),
            "observacao": observacao if observacao is not None else "",
            "assuntos": assuntos_ids,
        }

        resp = await self._request(
            "POST", f"/processo/{id_procedimento}/alterar", data=payload
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao alterar processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def consultar_processo_por_id(self, id_procedimento: str) -> dict:
        """Consulta processo pelo IdProcedimento (id interno)."""
        resp = await self._request("GET", f"/processo/consultar/{id_procedimento}")
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao consultar processo: {data.get('mensagem')}")
        return data["data"]

    async def pesquisar_hipoteses_legais(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa hipóteses legais para processos/documentos restritos ou sigilosos."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/hipoteseLegal/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar hipóteses legais: {data.get('mensagem')}")
        return self._paginated(data, "hipoteses", data.get("data", []), start, limit)

    async def pesquisar_tipos_processo(
        self, filtro: str = "", favoritos: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa tipos de processo disponíveis."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        if favoritos:
            params["favoritos"] = favoritos
        resp = await self._request("GET", "/processo/tipo/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar tipos de processo: {data.get('mensagem')}")
        return self._paginated(data, "tipos", data.get("data", []), start, limit)

    async def criar_processo(
        self,
        tipo_processo: str,
        especificacao: str = "",
        assuntos: str = "",
        interessados: str = "",
        observacoes: str = "",
        nivel_acesso: str = "0",
        hipotese_legal: str = "",
    ) -> dict:
        """Cria novo processo no SEI.
        assuntos e interessados devem ser JSON arrays de objetos com campo "id".
        Ex: '[{"id":"876"}]'
        Retorna: {IdProcedimento, ProtocoloFormatado}
        """
        # Converter IDs simples para formato JSON esperado pela API
        if assuntos and not assuntos.startswith("["):
            ids = [a.strip() for a in assuntos.split(",")]
            assuntos = json.dumps([{"id": i} for i in ids])
        if interessados and not interessados.startswith("["):
            ids = [i.strip() for i in interessados.split(",")]
            interessados = json.dumps([{"id": i} for i in ids])

        resp = await self._request(
            "POST",
            "/processo/criar",
            data={
                "tipoProcesso": tipo_processo,
                "especificacao": especificacao,
                "assuntos": assuntos,
                "interessados": interessados,
                "observacoes": observacoes,
                "nivelAcesso": nivel_acesso,
                "hipoteseLegal": hipotese_legal,
                "grauSigilo": "",
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar processo: {data.get('mensagem')}")
        return data["data"]

    async def enviar_processo(
        self,
        numero_processo: str,
        unidades_destino: str,
        manter_aberto: str = "N",
        remover_anotacao: str = "N",
        enviar_email: str = "N",
        data_retorno: str = "",
        dias_retorno: str = "",
        dias_uteis_retorno: str = "S",
        reabrir: str = "N",
    ) -> dict:
        """Envia (tramita) processo para outra(s) unidade(s)."""
        payload: dict = {
            "numeroProcesso": numero_processo,
            "unidadesDestino": unidades_destino,
            "sinManterAbertoUnidade": manter_aberto,
            "sinRemoverAnotacao": remover_anotacao,
            "sinEnviarEmailNotificacao": enviar_email,
            "sinReabrir": reabrir,
        }
        if data_retorno:
            payload["dataRetornoProgramado"] = data_retorno
        if dias_retorno:
            payload["diasRetornoProgramado"] = dias_retorno
            payload["sinDiasUteisRetornoProgramado"] = dias_uteis_retorno
        resp = await self._request("POST", "/processo/enviar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao enviar processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def concluir_processo(self, numero_processo: str) -> dict:
        """Conclui processo na unidade atual."""
        resp = await self._request(
            "POST", "/processo/concluir", data={"numeroProcesso": numero_processo}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao concluir processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def reabrir_processo(self, id_procedimento: str) -> dict:
        """Reabre processo concluído na unidade."""
        resp = await self._request("POST", f"/processo/reabrir/{id_procedimento}")
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao reabrir processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def atribuir_processo(self, numero_processo: str, id_usuario: str) -> dict:
        """Atribui processo a um usuário da unidade."""
        resp = await self._request(
            "POST",
            "/processo/atribuir",
            data={"numeroProcesso": numero_processo, "usuario": id_usuario},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao atribuir processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Documentos — assinar, pesquisar tipos
    # ------------------------------------------------------------------

    async def assinar_documento(
        self,
        id_documento: str,
        login: str,
        senha: str,
        cargo: str,
        orgao: str = "",
        id_usuario: str = "",
    ) -> dict:
        """Assina documento eletronicamente."""
        payload = {
            "documento": id_documento,
            "login": login,
            "senha": senha,
            "cargo": cargo,
            "orgao": orgao or self._orgao,
        }
        if id_usuario:
            payload["usuario"] = id_usuario
        resp = await self._request(
            "POST",
            "/documento/assinar",
            data=payload,
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao assinar documento: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def pesquisar_tipos_documento(
        self,
        filtro: str = "",
        favoritos: str = "",
        aplicabilidade: str = "",
        limit: int = 50,
        start: int = 0,
    ) -> dict:
        """Pesquisa tipos de documento (séries) disponíveis.
        Retorna: {data: [{id, nome}, ...], total: N}
        """
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        if favoritos:
            params["favoritos"] = favoritos
        if aplicabilidade:
            params["aplicabilidade"] = aplicabilidade
        resp = await self._request("GET", "/documento/tipo/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar tipos de documento: {data.get('mensagem')}")
        return self._paginated(data, "tipos", data.get("data", []), start, limit)

    # ------------------------------------------------------------------
    # Ciência
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Sobrestamento
    # ------------------------------------------------------------------

    async def sobrestar_processo(
        self, id_procedimento: str, motivo: str, protocolo_vinculado: str = ""
    ) -> dict:
        """Sobresta um processo. Motivo é obrigatório."""
        payload = {"motivo": motivo}
        if protocolo_vinculado:
            payload["protocoloDestino"] = protocolo_vinculado
        resp = await self._request(
            "POST", f"/processo/{id_procedimento}/sobrestar/processo", data=payload
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao sobrestar processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def remover_sobrestamento(self, id_procedimento: str) -> dict:
        """Remove sobrestamento de um processo."""
        resp = await self._request(
            "POST", f"/processo/{id_procedimento}/cancelar/sobrestamento"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao remover sobrestamento: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Ciência
    # ------------------------------------------------------------------

    async def dar_ciencia_documento(self, id_documento: str) -> dict:
        """Dá ciência em um documento."""
        resp = await self._request(
            "POST", "/documento/ciencia", data={"documento": id_documento}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao dar ciência no documento: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def dar_ciencia_processo(self, id_procedimento: str) -> dict:
        """Dá ciência em um processo."""
        resp = await self._request(
            "POST", f"/processo/{id_procedimento}/ciencia"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao dar ciência no processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def listar_ciencias_documento(self, id_documento: str) -> list[dict]:
        """Lista ciências registradas em um documento."""
        resp = await self._request(
            "GET", f"/documento/listar/ciencia/{id_documento}"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar ciências do documento: {data.get('mensagem')}")
        return data.get("data", [])

    async def listar_ciencias_processo(self, id_procedimento: str) -> list[dict]:
        """Lista ciências registradas em um processo."""
        resp = await self._request(
            "GET", f"/processo/{id_procedimento}/ciencia/listar"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar ciências do processo: {data.get('mensagem')}")
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Anotação
    # ------------------------------------------------------------------

    async def criar_anotacao(
        self,
        protocolo: str,
        descricao: str,
        prioridade: str = "1",
    ) -> dict:
        """Cria anotação (post-it) em um processo."""
        resp = await self._request(
            "POST",
            "/anotacao/",
            data={
                "protocolo": protocolo,
                "descricao": descricao,
                "prioridade": prioridade,
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar anotação: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    @staticmethod
    def _paginated(data: dict, items_key: str, itens: list, pagina: int, limit: int) -> dict:
        """Monta resposta paginada com metadados."""
        try:
            total = int(data.get("total", 0))
        except (ValueError, TypeError):
            total = len(itens)
        return {
            items_key: itens,
            "pagina_atual": pagina,
            "itens_pagina": len(itens),
            "total_itens": total,
            "tem_proxima": len(itens) >= limit,
        }

    # ------------------------------------------------------------------
    # Marcador
    # ------------------------------------------------------------------

    async def pesquisar_marcadores(self, filtro: str = "", limit: int = 50, start: int = 0) -> dict:
        """Pesquisa marcadores disponíveis na unidade."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/marcador/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar marcadores: {data.get('mensagem')}")
        return self._paginated(data, "marcadores", data.get("data", []), start, limit)

    async def listar_cores_marcador(self) -> list[dict]:
        """Lista cores disponíveis para marcadores."""
        resp = await self._request("GET", "/marcador/cores/listar")
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar cores: {data.get('mensagem')}")
        return data.get("data", [])

    async def criar_marcador(self, nome: str, id_cor: str) -> dict:
        """Cria marcador na unidade atual."""
        resp = await self._request(
            "POST", "/marcador/criar", data={"nome": nome, "idCor": id_cor}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar marcador: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_marcador(self, id_marcador: str, nome: str, id_cor: str) -> dict:
        """Altera nome e/ou cor de um marcador."""
        resp = await self._request(
            "POST", f"/marcador/{id_marcador}/alterar",
            data={"nome": nome, "idCor": id_cor},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao alterar marcador: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def excluir_marcadores(self, ids: str) -> dict:
        """Exclui marcador(es). IDs separados por vírgula."""
        resp = await self._request(
            "POST", "/marcador/excluir", data={"marcadores": ids}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao excluir marcadores: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def desativar_marcadores(self, ids: str) -> dict:
        """Desativa marcador(es) sem excluir. IDs separados por vírgula."""
        resp = await self._request(
            "POST", "/marcador/desativar", data={"marcadores": ids}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao desativar marcadores: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def reativar_marcadores(self, ids: str) -> dict:
        """Reativa marcador(es) desativados. IDs separados por vírgula."""
        resp = await self._request(
            "POST", "/marcador/reativar", data={"marcadores": ids}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao reativar marcadores: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def marcar_processo(self, id_procedimento: str, id_marcador: str, texto: str = "") -> dict:
        """Adiciona marcador a um processo."""
        resp = await self._request(
            "POST", f"/marcador/processo/{id_procedimento}/marcar",
            data={"marcador": id_marcador, "texto": texto},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao marcar processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def consultar_marcador_processo(self, id_procedimento: str) -> list[dict]:
        """Consulta marcadores de um processo."""
        resp = await self._request("GET", f"/marcador/processo/{id_procedimento}/consultar")
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao consultar marcador: {data.get('mensagem')}")
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Acompanhamento Especial
    # ------------------------------------------------------------------

    async def acompanhar_processo(
        self, id_procedimento: str, id_grupo: str = "", observacao: str = ""
    ) -> dict:
        """Adiciona acompanhamento especial em um processo."""
        payload: dict = {"protocolo": id_procedimento}
        if id_grupo:
            payload["grupo"] = id_grupo
        if observacao:
            payload["observacao"] = observacao
        resp = await self._request("POST", "/processo/acompanhar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao acompanhar processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def consultar_acompanhamento(self, id_procedimento: str) -> dict:
        """Consulta acompanhamento de um processo."""
        resp = await self._request(
            "GET", "/processo/acompanhamento/consultar",
            params={"protocolo": id_procedimento},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao consultar acompanhamento: {data.get('mensagem')}")
        return data.get("data", {})

    async def excluir_acompanhamento(self, id_acompanhamento: str) -> dict:
        """Remove acompanhamento especial."""
        resp = await self._request(
            "POST", f"/processo/acompanhamento/{id_acompanhamento}/excluir"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao excluir acompanhamento: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def criar_grupo_acompanhamento(self, nome: str) -> dict:
        """Cria grupo de acompanhamento especial."""
        resp = await self._request(
            "POST", "/grupoacompanhamento/cadastrar", data={"nome": nome}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar grupo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_grupo_acompanhamento(self, id_grupo: str, nome: str) -> dict:
        """Altera nome de um grupo de acompanhamento."""
        resp = await self._request(
            "POST", f"/grupoacompanhamento/{id_grupo}/alterar", data={"nome": nome}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao alterar grupo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def excluir_grupo_acompanhamento(self, ids_grupos: str) -> dict:
        """Exclui grupo(s) de acompanhamento. IDs separados por vírgula."""
        resp = await self._request(
            "POST", "/grupoacompanhamento/excluir", data={"grupos": ids_grupos}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao excluir grupo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def listar_grupos_acompanhamento(self, filtro: str = "", limit: int = 50) -> dict:
        """Lista grupos de acompanhamento disponíveis."""
        params: dict = {"limit": limit}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/grupoacompanhamento/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar grupos: {data.get('mensagem')}")
        return {"grupos": data.get("data", []), "total": data.get("total")}

    # ------------------------------------------------------------------
    # Bloco Interno
    # ------------------------------------------------------------------

    async def criar_bloco_interno(self, descricao: str) -> dict:
        """Cria bloco interno."""
        resp = await self._request("POST", "/bloco/interno/criar", data={"descricao": descricao})
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar bloco interno: {data.get('mensagem')}")
        return data.get("data", {})

    async def incluir_processo_bloco_interno(self, id_bloco: str, protocolos: str) -> dict:
        """Inclui processo(s) em bloco interno."""
        resp = await self._request(
            "POST", f"/bloco/interno/{id_bloco}/processos/incluir",
            data={"protocolos": protocolos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao incluir no bloco: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def retirar_processo_bloco_interno(self, id_bloco: str, protocolos: str) -> dict:
        """Remove processo(s) de bloco interno."""
        resp = await self._request(
            "POST", f"/bloco/interno/{id_bloco}/processos/retirar",
            data={"protocolos": protocolos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao retirar do bloco: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def pesquisar_blocos_internos(self, filtro: str = "", limit: int = 50, start: int = 0) -> dict:
        """Pesquisa blocos internos."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/bloco/interno/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar blocos: {data.get('mensagem')}")
        return self._paginated(data, "blocos", data.get("data", []), start, limit)

    # ------------------------------------------------------------------
    # Bloco de Assinatura
    # ------------------------------------------------------------------

    async def criar_bloco_assinatura(self, descricao: str, unidades: str = "") -> dict:
        """Cria bloco de assinatura."""
        payload: dict = {"descricao": descricao}
        if unidades:
            payload["unidades"] = unidades
        resp = await self._request("POST", "/bloco/assinatura/criar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar bloco de assinatura: {data.get('mensagem')}")
        return data.get("data", {})

    async def incluir_documento_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Inclui documento(s) em bloco de assinatura."""
        resp = await self._request(
            "POST", f"/bloco/assinatura/{id_bloco}/documentos/incluir",
            data={"documentos": documentos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao incluir no bloco: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def disponibilizar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Disponibiliza bloco de assinatura para as unidades."""
        resp = await self._request(
            "POST", f"/bloco/assinatura/{id_bloco}/disponibilizar"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao disponibilizar bloco: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela disponibilização de bloco de assinatura."""
        resp = await self._request(
            "POST", f"/bloco/assinatura/{id_bloco}/disponibilizacao/cancelar"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao cancelar disponibilização: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def pesquisar_blocos_assinatura(self, filtro: str = "", limit: int = 50, start: int = 0) -> dict:
        """Pesquisa blocos de assinatura."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/bloco/assinatura/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar blocos: {data.get('mensagem')}")
        return self._paginated(data, "blocos", data.get("data", []), start, limit)

    async def listar_documentos_bloco_assinatura(self, id_bloco: str, limit: int = 200) -> list[dict]:
        """Lista documentos de um bloco de assinatura."""
        resp = await self._request(
            "GET", f"/bloco/assinatura/{id_bloco}/documentos/listar",
            params={"limit": limit},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar documentos do bloco: {data.get('mensagem')}")
        return data.get("data", [])

    async def retirar_documento_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Retira documento(s) de bloco de assinatura."""
        resp = await self._request(
            "POST", f"/bloco/assinatura/{id_bloco}/documentos/retirar",
            data={"documentos": documentos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao retirar do bloco: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Atribuição — remover
    # ------------------------------------------------------------------

    async def remover_atribuicao(self, id_procedimento: str) -> dict:
        """Remove atribuição de um processo (desatribui). Usa IdProcedimento."""
        resp = await self._request(
            "POST", f"/processo/{id_procedimento}/remover/atribuicao"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao remover atribuição: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Receber processo
    # ------------------------------------------------------------------

    async def receber_processo(self, id_procedimento: str) -> dict:
        """Recebe processo na unidade atual (confirma recebimento)."""
        resp = await self._request(
            "POST", "/processo/receber", data={"procedimento": id_procedimento}
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao receber processo: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Documento externo (upload)
    # ------------------------------------------------------------------

    async def criar_documento_externo(
        self,
        id_procedimento: str,
        id_serie: str,
        arquivo_path: str,
        descricao: str = "",
        nivel_acesso: str = "0",
        id_unidade: str = "",
    ) -> dict:
        """Cria documento externo com upload de arquivo em um processo SEI.
        arquivo_path: caminho local do arquivo (PDF, imagem, etc.)
        Retorna: {idDocumento, protocoloDocumentoFormatado}
        """
        import os
        from datetime import datetime
        if not os.path.exists(arquivo_path):
            raise Exception(f"Arquivo não encontrado: {arquivo_path}")

        nome_arquivo = os.path.basename(arquivo_path)
        data_hoje = datetime.now().strftime("%d/%m/%Y")
        headers = await self._get_headers()

        with open(arquivo_path, "rb") as f:
            resp = await self._client.post(
                f"{self.base_url}/documento/{id_procedimento}/externo/criar",
                headers=headers,
                data={
                    "idSerie": id_serie,
                    "numero": "",
                    "descricao": descricao,
                    "dataElaboracao": data_hoje,
                    "nivelAcesso": nivel_acesso,
                    "idHipoteseLegal": "",
                    "grauSigilo": "",
                    "idUnidadeGeradoraProtocolo": id_unidade,
                    "assuntos": "",
                    "interessados": "",
                    "remetente": "",
                    "destinatarios": "",
                    "observacao": "",
                    "idTextoPadraoInterno": "",
                    "idTipoConferencia": "",
                    "protocoloDocumentoModelo": "",
                },
                files={"anexo": (nome_arquivo, f)},
            )

        if resp.status_code in (401, 403):
            await self.autenticar()
            headers = {"token": self._token}
            with open(arquivo_path, "rb") as f:
                resp = await self._client.post(
                    f"{self.base_url}/documento/{id_procedimento}/externo/criar",
                    headers=headers,
                    data={
                        "idSerie": id_serie, "numero": "", "descricao": descricao,
                        "dataElaboracao": data_hoje, "nivelAcesso": nivel_acesso,
                        "idHipoteseLegal": "", "grauSigilo": "",
                        "idUnidadeGeradoraProtocolo": id_unidade,
                        "assuntos": "", "interessados": "", "remetente": "",
                        "destinatarios": "", "observacao": "",
                        "idTextoPadraoInterno": "", "idTipoConferencia": "",
                        "protocoloDocumentoModelo": "",
                    },
                    files={"anexo": (nome_arquivo, f)},
                )

        resp.raise_for_status()
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao criar documento externo: {data.get('mensagem')}")
        return data["data"]

    # ------------------------------------------------------------------
    # Retorno programado
    # ------------------------------------------------------------------

    async def agendar_retorno_programado(
        self, numero_processo: str, data_retorno: str = "",
        dias_retorno: str = "", dias_uteis: str = "S",
    ) -> dict:
        """Agenda retorno programado para um processo."""
        payload: dict = {"numeroProcesso": numero_processo}
        if data_retorno:
            payload["dataRetornoProgramado"] = data_retorno
        if dias_retorno:
            payload["diasRetornoProgramado"] = dias_retorno
            payload["sinDiasUteisRetornoProgramado"] = dias_uteis
        resp = await self._request(
            "POST", "/processo/agendar/retorno/programado", data=payload
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao agendar retorno: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Consultas adicionais de processo
    # ------------------------------------------------------------------

    async def listar_sobrestamentos(self, id_procedimento: str) -> list[dict]:
        """Lista histórico de sobrestamentos de um processo."""
        resp = await self._request(
            "GET", f"/processo/listar/sobrestamento/{id_procedimento}"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar sobrestamentos: {data.get('mensagem')}")
        return data.get("data", [])

    async def listar_unidades_processo(self, id_procedimento: str) -> list[dict]:
        """Lista unidades onde o processo está aberto."""
        resp = await self._request(
            "GET", f"/processo/listar/unidades/{id_procedimento}"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar unidades: {data.get('mensagem')}")
        return data.get("data", [])

    async def listar_interessados(self, id_procedimento: str) -> list[dict]:
        """Lista interessados de um processo."""
        resp = await self._request(
            "GET", f"/processo/{id_procedimento}/interessados/listar"
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao listar interessados: {data.get('mensagem')}")
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Andamento
    # ------------------------------------------------------------------

    async def registrar_andamento(
        self, id_procedimento: str, descricao: str
    ) -> dict:
        """Registra andamento (atividade) em um processo."""
        resp = await self._request(
            "POST", "/atividade/lancar/andamento/processo",
            data={"protocolo": id_procedimento, "descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao registrar andamento: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Contato
    # ------------------------------------------------------------------

    async def pesquisar_contatos(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa contatos no SEI."""
        params: dict = {"limit": limit}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/contato/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao pesquisar contatos: {data.get('mensagem')}")
        return {"contatos": data.get("data", []), "total": data.get("total")}

    # ------------------------------------------------------------------
    # Bloco de assinatura — assinar
    # ------------------------------------------------------------------

    async def assinar_bloco(
        self, id_bloco: str, login: str, senha: str,
        cargo: str, orgao: str = "", id_usuario: str = "",
    ) -> dict:
        """Assina todos os documentos de um bloco de assinatura."""
        payload = {
            "orgao": orgao or self._orgao,
            "cargo": cargo,
            "login": login,
            "senha": senha,
        }
        if id_usuario:
            payload["usuario"] = id_usuario
        resp = await self._request(
            "POST", f"/bloco/assinatura/{id_bloco}/assinar", data=payload
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao assinar bloco: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def assinar_documentos_bloco(
        self, login: str, senha: str, cargo: str,
        documentos: str, orgao: str = "", id_usuario: str = "",
    ) -> dict:
        """Assina documentos específicos (de um ou mais blocos)."""
        payload = {
            "orgao": orgao or self._orgao,
            "cargo": cargo,
            "login": login,
            "senha": senha,
            "documentos": documentos,
        }
        if id_usuario:
            payload["usuario"] = id_usuario
        resp = await self._request(
            "POST", "/bloco/assinatura/assinar/documentos", data=payload
        )
        data = resp.json()
        if not data.get("sucesso"):
            raise Exception(f"Erro ao assinar documentos: {data.get('mensagem')}")
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def close(self):
        await self._client.aclose()
