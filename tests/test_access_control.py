"""Tests for the restricted-access consent gate (``access_control``).

This is the module that decides whether raw content from a restricted
(nivelAcesso=1) or classified (nivelAcesso=2) SEI record may be handed to the
LLM client. A bug here is a privacy/legal incident (LGPD/LAI/sigilo funcional),
not a stack trace, so the safety-critical invariant — *raw content is never
released without explicit consent* — is asserted directly.

No live SEI server required: every function under test is pure.
"""

from __future__ import annotations

import pytest

from todos import access_control as ac

_ALVO = {"tipo": "documento", "id": "123"}


# ---------------------------------------------------------------------------
# normalizar_nivel
# ---------------------------------------------------------------------------


class TestNormalizarNivel:
    @pytest.mark.parametrize("valor", ["0", "1", "2"])
    def test_canonical_strings_pass_through(self, valor: str) -> None:
        assert ac.normalizar_nivel(valor) == valor

    @pytest.mark.parametrize(("valor", "esperado"), [(0, "0"), (1, "1"), (2, "2")])
    def test_integers_are_stringified(self, valor: int, esperado: str) -> None:
        assert ac.normalizar_nivel(valor) == esperado

    def test_whitespace_is_stripped(self) -> None:
        assert ac.normalizar_nivel(" 1 ") == "1"

    @pytest.mark.parametrize("valor", [None, "", "3", "restrito", "x", -1])
    def test_unknown_values_return_none(self, valor: object) -> None:
        assert ac.normalizar_nivel(valor) is None


# ---------------------------------------------------------------------------
# precisa_disclaimer
# ---------------------------------------------------------------------------


class TestPrecisaDisclaimer:
    def test_publico_does_not_need_disclaimer(self) -> None:
        assert ac.precisa_disclaimer("0") is False

    @pytest.mark.parametrize("nivel", ["1", "2"])
    def test_restrito_and_sigiloso_need_disclaimer(self, nivel: str) -> None:
        assert ac.precisa_disclaimer(nivel) is True

    @pytest.mark.parametrize("valor", [None, "", "3", "lixo"])
    def test_unknown_does_not_need_disclaimer(self, valor: object) -> None:
        # Fail-safe direction: an unparseable level is treated as public so the
        # gate never fabricates a block from garbage. (See safety test below for
        # the inverse guarantee on KNOWN restricted levels.)
        assert ac.precisa_disclaimer(valor) is False


# ---------------------------------------------------------------------------
# env_permite_restritos
# ---------------------------------------------------------------------------


class TestEnvPermiteRestritos:
    @pytest.mark.parametrize("valor", ["1", "true", "TRUE", "True", "yes", "Sim", "  true  "])
    def test_truthy_variants(self, monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", valor)
        assert ac.env_permite_restritos() is True

    @pytest.mark.parametrize("valor", ["0", "false", "no", "nao", "", "qualquer"])
    def test_falsy_variants(self, monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", valor)
        assert ac.env_permite_restritos() is False

    def test_unset_defaults_to_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)
        assert ac.env_permite_restritos() is False

    def test_read_at_call_time_supports_runtime_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)
        assert ac.env_permite_restritos() is False
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", "true")
        assert ac.env_permite_restritos() is True


# ---------------------------------------------------------------------------
# avaliar_acesso — the decision matrix
# ---------------------------------------------------------------------------


class TestAvaliarAcesso:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Every case in this class controls consent via the per-call flag; the
        # env var must start unset so it cannot leak a "liberar" decision.
        monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)

    def test_public_always_liberates_without_disclaimer(self) -> None:
        decisao, payload = ac.avaliar_acesso("0", confirmou=False, alvo=_ALVO)
        assert decisao == "liberar"
        assert payload is None

    @pytest.mark.parametrize("nivel", ["1", "2"])
    def test_restricted_without_consent_blocks(self, nivel: str) -> None:
        decisao, payload = ac.avaliar_acesso(nivel, confirmou=False, alvo=_ALVO)
        assert decisao == "bloquear"
        assert payload is not None
        assert payload["consentimento_necessario"] is True
        assert payload["tipo_resposta"] == "consentimento_pendente"

    @pytest.mark.parametrize("nivel", ["1", "2"])
    def test_restricted_with_per_call_consent_liberates_with_disclaimer(self, nivel: str) -> None:
        decisao, payload = ac.avaliar_acesso(nivel, confirmou=True, alvo=_ALVO)
        assert decisao == "liberar"
        assert payload is not None
        assert payload["consentimento_necessario"] is False
        assert payload["tipo_resposta"] == "aviso_classificacao_informativo"

    @pytest.mark.parametrize("nivel", ["1", "2"])
    def test_restricted_with_env_consent_liberates_with_disclaimer(
        self, monkeypatch: pytest.MonkeyPatch, nivel: str
    ) -> None:
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", "true")
        decisao, payload = ac.avaliar_acesso(nivel, confirmou=False, alvo=_ALVO)
        assert decisao == "liberar"
        assert payload is not None
        assert payload["consentimento_necessario"] is False
        assert payload["tipo_resposta"] == "aviso_classificacao_informativo"

    def test_hipotese_legal_propagated_to_bloqueio(self) -> None:
        _, payload = ac.avaliar_acesso("1", "Art. 31 LAI", confirmou=False, alvo=_ALVO)
        assert payload is not None
        assert payload["hipotese_legal"] == "Art. 31 LAI"

    def test_hipotese_legal_propagated_to_disclaimer(self) -> None:
        _, payload = ac.avaliar_acesso("1", "Art. 31 LAI", confirmou=True, alvo=_ALVO)
        assert payload is not None
        assert payload["hipotese_legal"] == "Art. 31 LAI"
        assert payload["tipo_resposta"] == "aviso_classificacao_informativo"

    def test_missing_nivel_is_safe_path(self) -> None:
        decisao, payload = ac.avaliar_acesso(None, confirmou=False, alvo=_ALVO)
        assert decisao == "liberar"
        assert payload is None

    def test_alvo_is_propagated(self) -> None:
        _, payload = ac.avaliar_acesso("1", confirmou=False, alvo=_ALVO)
        assert payload is not None
        assert payload["alvo"] == _ALVO

    # --- the invariant that justifies this whole module ---------------------

    @pytest.mark.parametrize(
        ("nivel", "env_val"),
        [(n, e) for n in ("1", "2") for e in ("false", "0", "no", "", "garbage")],
    )
    def test_safety_restricted_without_any_consent_never_liberates(
        self, monkeypatch: pytest.MonkeyPatch, nivel: str, env_val: str
    ) -> None:
        """No combination of 'no consent' may produce a 'liberar' decision.

        This is the legal firewall: restricted/classified content must not reach
        the LLM unless the human explicitly authorized it (per-call flag) or the
        operator opted in at deploy time (env var).
        """
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", env_val)
        decisao, _ = ac.avaliar_acesso(nivel, confirmou=False, alvo=_ALVO)
        assert decisao == "bloquear"


# ---------------------------------------------------------------------------
# construir_aviso_bloqueio / construir_disclaimer_acompanhante structure
# ---------------------------------------------------------------------------


class TestAvisoStructure:
    def test_bloqueio_carries_full_framing(self) -> None:
        aviso = ac.construir_aviso_bloqueio("1", None, _ALVO)
        # The fields a model needs to NOT treat this as a fixable error.
        assert isinstance(aviso["nao_e_erro_tecnico"], str)
        assert aviso["nao_e_erro_tecnico"]
        assert isinstance(aviso["instrucao_para_modelo"], str)
        assert aviso["instrucao_para_modelo"]
        assert isinstance(aviso["mensagem_para_usuario_humano"], str)
        assert aviso["mensagem_para_usuario_humano"]
        assert aviso["riscos"] == ac.riscos_padrao()
        assert isinstance(aviso["como_liberar"], list)
        assert len(aviso["como_liberar"]) > 0
        assert aviso["rotulo_nivel"] == "Restrito"

    def test_bloqueio_nivel_none_consistent_fallback(self) -> None:
        aviso = ac.construir_aviso_bloqueio(None, None, _ALVO)
        assert aviso["rotulo_nivel"] == "Desconhecido"
        assert "Desconhecido" in aviso["mensagem_para_usuario_humano"]

    def test_bloqueio_sigiloso_label(self) -> None:
        aviso = ac.construir_aviso_bloqueio("2", None, _ALVO)
        assert aviso["rotulo_nivel"] == "Sigiloso"

    def test_disclaimer_acompanhante_is_informational(self) -> None:
        d = ac.construir_disclaimer_acompanhante("1", "Art. 31", _ALVO)
        assert d["consentimento_necessario"] is False
        assert d["tipo_resposta"] == "aviso_classificacao_informativo"
        assert d["hipotese_legal"] == "Art. 31"
        assert d["riscos"] == ac.riscos_padrao()

    def test_riscos_padrao_returns_a_copy(self) -> None:
        riscos = ac.riscos_padrao()
        riscos.append("mutação")
        assert "mutação" not in ac.riscos_padrao()

    def test_bloqueio_riscos_list_is_independent_copy(self) -> None:
        aviso = ac.construir_aviso_bloqueio("1", None, _ALVO)
        aviso["riscos"].append("mutação")
        assert "mutação" not in ac.construir_aviso_bloqueio("1", None, _ALVO)["riscos"]


class TestAvisoRecusado:
    def test_tipo_resposta(self) -> None:
        aviso = ac.construir_aviso_recusado("1", "Restrito", _ALVO)
        assert aviso["tipo_resposta"] == "consentimento_recusado"

    def test_mensagem_contains_rotulo(self) -> None:
        aviso = ac.construir_aviso_recusado("1", "Restrito", _ALVO)
        assert "restrito" in aviso["mensagem_para_usuario_humano"].lower()

    def test_instrucao_warns_against_bypass(self) -> None:
        aviso = ac.construir_aviso_recusado("1", "Restrito", _ALVO)
        assert "NÃO tente" in aviso["instrucao_para_modelo"]

    def test_alvo_and_nivel_acesso_present(self) -> None:
        aviso = ac.construir_aviso_recusado("2", "Sigiloso", _ALVO)
        assert aviso["alvo"] == _ALVO
        assert aviso["nivel_acesso"] == "2"


# ---------------------------------------------------------------------------
# prefixar_markdown / prefixar_texto / envelopar_html
# ---------------------------------------------------------------------------


class TestPrefixos:
    @pytest.fixture
    def disclaimer(self) -> dict:
        return ac.construir_disclaimer_acompanhante("1", "Art. 31 LAI", _ALVO)

    def test_markdown_disclaimer_precedes_content(self, disclaimer: dict) -> None:
        out = ac.prefixar_markdown(disclaimer, "CORPO DO DOCUMENTO")
        assert out.index(disclaimer["mensagem"]) < out.index("CORPO DO DOCUMENTO")
        assert out.endswith("CORPO DO DOCUMENTO")
        assert out.startswith("> ")
        assert "Art. 31 LAI" in out

    def test_texto_disclaimer_precedes_content(self, disclaimer: dict) -> None:
        out = ac.prefixar_texto(disclaimer, "CORPO DO DOCUMENTO")
        assert out.index("AVISO:") < out.index("CORPO DO DOCUMENTO")
        assert out.endswith("CORPO DO DOCUMENTO")
        assert "Art. 31 LAI" in out

    def test_html_disclaimer_precedes_content(self, disclaimer: dict) -> None:
        out = ac.envelopar_html(disclaimer, "<p>CORPO</p>")
        assert out.index("<aside") < out.index("<p>CORPO</p>")
        assert out.endswith("<p>CORPO</p>")
        assert "Art. 31 LAI" in out

    def test_all_prefixes_include_every_risk(self, disclaimer: dict) -> None:
        for render in (ac.prefixar_markdown, ac.prefixar_texto, ac.envelopar_html):
            out = render(disclaimer, "x")
            for risco in disclaimer["riscos"]:
                assert risco in out

    def test_prefixes_omit_hipotese_when_absent(self) -> None:
        d = ac.construir_disclaimer_acompanhante("1", None, _ALVO)
        assert "Hipótese legal" not in ac.prefixar_markdown(d, "x")
        assert "Hipótese legal" not in ac.prefixar_texto(d, "x")
        assert "Hipótese legal" not in ac.envelopar_html(d, "x")

    def test_content_is_never_dropped(self, disclaimer: dict) -> None:
        sentinel = "CONTEUDO-UNICO-12345"
        assert sentinel in ac.prefixar_markdown(disclaimer, sentinel)
        assert sentinel in ac.prefixar_texto(disclaimer, sentinel)
        assert sentinel in ac.envelopar_html(disclaimer, sentinel)


# ---------------------------------------------------------------------------
# extrair_nivel: REST camelCase + snake_case
# ---------------------------------------------------------------------------


class TestExtrairNivel:
    def test_camelcase_keys(self) -> None:
        nivel, hl = ac.extrair_nivel({"nivelAcesso": "1", "hipoteseLegal": "Art. 31"})
        assert nivel == "1"
        assert hl == "Art. 31"

    def test_snake_case_keys(self) -> None:
        nivel, hl = ac.extrair_nivel({"nivel_acesso": "2", "hipotese_legal": "Sigilo"})
        assert nivel == "2"
        assert hl == "Sigilo"

    def test_global_fallback_key(self) -> None:
        nivel, _ = ac.extrair_nivel({"nivelAcessoGlobal": "1"})
        assert nivel == "1"

    def test_hipotese_as_dict_prefers_nome(self) -> None:
        _, hl = ac.extrair_nivel({"nivelAcesso": "1", "hipoteseLegal": {"id": 9, "nome": "X"}})
        assert hl == "X"

    def test_hipotese_as_dict_falls_back_to_id(self) -> None:
        _, hl = ac.extrair_nivel({"nivelAcesso": "1", "hipoteseLegal": {"id": 9}})
        assert hl == "9"

    def test_hipotese_by_id_only(self) -> None:
        _, hl = ac.extrair_nivel({"nivelAcesso": "1", "idHipoteseLegal": 42})
        assert hl == "42"

    def test_no_level_returns_none(self) -> None:
        nivel, hl = ac.extrair_nivel({"foo": "bar"})
        assert nivel is None
        assert hl is None

    def test_integer_zero_nivel_acesso_is_not_skipped(self) -> None:
        # integer 0 is falsy in Python; the or-chain would skip it and fall
        # through to the next key, misclassifying a public doc as unknown.
        nivel, _ = ac.extrair_nivel({"nivelAcesso": 0})
        assert nivel == "0"

    @pytest.mark.parametrize("nao_dict", [None, [], "string", 5])
    def test_non_dict_input_is_safe(self, nao_dict: object) -> None:
        assert ac.extrair_nivel(nao_dict) == (None, None)


# ---------------------------------------------------------------------------
# extrair_nivel_web: accented keys + textual values
# ---------------------------------------------------------------------------


class TestExtrairNivelWeb:
    def test_textual_restrito_accented_key(self) -> None:
        assert ac.extrair_nivel_web({"nível_de_acesso": "Restrito"}) == "1"

    def test_textual_sigiloso(self) -> None:
        assert ac.extrair_nivel_web({"Nivel de Acesso": "Sigiloso"}) == "2"

    def test_textual_publico(self) -> None:
        assert ac.extrair_nivel_web({"nivel_acesso": "Público"}) == "0"

    def test_numeric_value_falls_back_to_normalizar(self) -> None:
        assert ac.extrair_nivel_web({"nivelAcesso": "1"}) == "1"

    def test_no_matching_key_returns_none(self) -> None:
        assert ac.extrair_nivel_web({"tipo": "Despacho"}) is None
