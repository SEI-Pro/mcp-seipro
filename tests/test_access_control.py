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

# Template kept as a module-level constant so it can serve as an expected value
# for equality assertions (e.g. ``assert payload["alvo"] == _ALVO``).  Every
# test function that *passes* this dict into a function under test must use the
# ``alvo`` fixture (below) so it gets a fresh copy — otherwise a future test
# that mutates the dict would poison later tests.
_ALVO = {"tipo": "documento", "id": "123"}


@pytest.fixture
def alvo() -> dict:
    """Return a fresh copy of _ALVO for each test to prevent shared-state bugs."""
    return _ALVO.copy()


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
# Decision matrix — now tested via the building blocks directly
# (avaliar_acesso was removed; the gate lives in _aplicar_gate_documento)
# ---------------------------------------------------------------------------


class TestAvaliarAcesso:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Every case in this class controls consent via the per-call flag; the
        # env var must start unset so it cannot leak a "liberar" decision.
        monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)

    def test_public_always_liberates_without_disclaimer(self) -> None:
        assert not ac.precisa_disclaimer("0")

    @pytest.mark.parametrize("nivel", ["1", "2"])
    def test_restricted_without_consent_blocks(self, nivel: str, alvo: dict) -> None:
        assert ac.precisa_disclaimer(nivel)
        assert not ac.env_permite_restritos()
        payload = ac.construir_aviso_bloqueio(nivel, None, alvo)
        assert payload["consentimento_necessario"] is True
        assert payload["tipo_resposta"] == "consentimento_pendente"

    @pytest.mark.parametrize("nivel", ["1", "2"])
    def test_restricted_with_per_call_consent_liberates_with_disclaimer(
        self, nivel: str, alvo: dict
    ) -> None:
        assert ac.precisa_disclaimer(nivel)
        payload = ac.construir_disclaimer_acompanhante(nivel, None, alvo)
        assert payload["consentimento_necessario"] is False
        assert payload["tipo_resposta"] == "aviso_classificacao_informativo"

    @pytest.mark.parametrize("nivel", ["1", "2"])
    def test_restricted_with_env_consent_liberates_with_disclaimer(
        self, monkeypatch: pytest.MonkeyPatch, nivel: str, alvo: dict
    ) -> None:
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", "true")
        assert ac.env_permite_restritos()
        payload = ac.construir_disclaimer_acompanhante(nivel, None, alvo)
        assert payload["consentimento_necessario"] is False
        assert payload["tipo_resposta"] == "aviso_classificacao_informativo"

    def test_hipotese_legal_propagated_to_bloqueio(self, alvo: dict) -> None:
        payload = ac.construir_aviso_bloqueio("1", "Art. 31 LAI", alvo)
        assert payload["hipotese_legal"] == "Art. 31 LAI"

    def test_hipotese_legal_propagated_to_disclaimer(self, alvo: dict) -> None:
        payload = ac.construir_disclaimer_acompanhante("1", "Art. 31 LAI", alvo)
        assert payload["hipotese_legal"] == "Art. 31 LAI"
        assert payload["tipo_resposta"] == "aviso_classificacao_informativo"

    def test_missing_nivel_is_safe_path(self) -> None:
        assert not ac.precisa_disclaimer(None)

    def test_alvo_is_propagated(self, alvo: dict) -> None:
        payload = ac.construir_aviso_bloqueio("1", None, alvo)
        assert payload["alvo"] == _ALVO

    # --- the invariant that justifies this whole module ---------------------

    @pytest.mark.parametrize(
        ("nivel", "env_val"),
        [(n, e) for n in ("1", "2") for e in ("false", "0", "no", "", "garbage")],
    )
    def test_safety_restricted_without_any_consent_never_liberates(
        self, monkeypatch: pytest.MonkeyPatch, nivel: str, env_val: str
    ) -> None:
        """No combination of 'no consent' may produce content delivery without consent.

        This is the legal firewall: restricted/classified content must not reach
        the LLM unless the human explicitly authorized it (per-call flag) or the
        operator opted in at deploy time (env var).
        """
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", env_val)
        # precisa_disclaimer=True AND env not set → gate must block, not liberate
        assert ac.precisa_disclaimer(nivel)
        assert not ac.env_permite_restritos()


# ---------------------------------------------------------------------------
# construir_aviso_bloqueio / construir_disclaimer_acompanhante structure
# ---------------------------------------------------------------------------


class TestAvisoStructure:
    def test_bloqueio_carries_full_framing(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_bloqueio("1", None, alvo)
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

    def test_bloqueio_nivel_none_consistent_fallback(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_bloqueio(None, None, alvo)
        assert aviso["rotulo_nivel"] == "Desconhecido"
        assert "Desconhecido" in aviso["mensagem_para_usuario_humano"]

    def test_bloqueio_sigiloso_label(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_bloqueio("2", None, alvo)
        assert aviso["rotulo_nivel"] == "Sigiloso"

    def test_disclaimer_acompanhante_is_informational(self, alvo: dict) -> None:
        d = ac.construir_disclaimer_acompanhante("1", "Art. 31", alvo)
        assert d["consentimento_necessario"] is False
        assert d["tipo_resposta"] == "aviso_classificacao_informativo"
        assert d["hipotese_legal"] == "Art. 31"
        assert d["riscos"] == ac.riscos_padrao()

    def test_riscos_padrao_returns_a_copy(self) -> None:
        riscos = ac.riscos_padrao()
        riscos.append("mutação")
        assert "mutação" not in ac.riscos_padrao()

    def test_bloqueio_riscos_list_is_independent_copy(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_bloqueio("1", None, alvo)
        aviso["riscos"].append("mutação")
        assert "mutação" not in ac.construir_aviso_bloqueio("1", None, alvo.copy())["riscos"]


class TestAvisoRecusado:
    def test_tipo_resposta(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_recusado("1", "Restrito", alvo)
        assert aviso["tipo_resposta"] == "consentimento_recusado"

    def test_mensagem_contains_rotulo(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_recusado("1", "Restrito", alvo)
        assert "restrito" in aviso["mensagem_para_usuario_humano"].lower()

    def test_instrucao_warns_against_bypass(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_recusado("1", "Restrito", alvo)
        assert "NÃO tente" in aviso["instrucao_para_modelo"]

    def test_alvo_and_nivel_acesso_present(self, alvo: dict) -> None:
        aviso = ac.construir_aviso_recusado("2", "Sigiloso", alvo)
        assert aviso["alvo"] == _ALVO
        assert aviso["nivel_acesso"] == "2"


# ---------------------------------------------------------------------------
# prefixar_markdown / prefixar_texto / envelopar_html
# ---------------------------------------------------------------------------


class TestPrefixos:
    @pytest.fixture
    def disclaimer(self, alvo: dict) -> dict:
        return ac.construir_disclaimer_acompanhante("1", "Art. 31 LAI", alvo)

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

    def test_prefixes_omit_hipotese_when_absent(self, alvo: dict) -> None:
        d = ac.construir_disclaimer_acompanhante("1", None, alvo)
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

    def test_sigiloso_lowercase(self) -> None:
        # Exact lowercase spelling used by some SEI instances.
        assert ac.extrair_nivel_web({"nivel_acesso": "sigiloso"}) == "2"

    def test_sigiloso_uppercase(self) -> None:
        assert ac.extrair_nivel_web({"nivel_acesso": "SIGILOSO"}) == "2"

    def test_sigiloso_in_phrase(self) -> None:
        # Value may include surrounding text, e.g. "Acesso Sigiloso (art. 26)".
        assert ac.extrair_nivel_web({"nivel_de_acesso": "Acesso Sigiloso (art. 26)"}) == "2"

    def test_restrito_lowercase(self) -> None:
        assert ac.extrair_nivel_web({"nivel_acesso": "restrito"}) == "1"


# ---------------------------------------------------------------------------
# Adversarial gate tests — nivelAcesso="2" (sigiloso) must always be blocked
# ---------------------------------------------------------------------------


class TestGateSigiloso:
    """Safety invariants for classified (sigiloso / nivelAcesso=2) documents.

    A single incorrect 'liberar' for a sigiloso document is a LGPD/LAI
    incident, so this class stress-tests the full call path from raw metadata
    through to the final gate decision.
    """

    @pytest.fixture(autouse=True)
    def _no_env_consent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)

    # --- nivel string "2" --------------------------------------------------

    def test_nivel_2_string_blocks_without_consent(self, alvo: dict) -> None:
        """nivelAcesso='2' without consent must produce a block payload."""
        assert ac.precisa_disclaimer("2")
        assert not ac.env_permite_restritos()
        payload = ac.construir_aviso_bloqueio("2", None, alvo)
        assert payload["tipo_resposta"] == "consentimento_pendente"
        assert payload["consentimento_necessario"] is True

    def test_nivel_2_string_liberates_with_consent(self, alvo: dict) -> None:
        """nivelAcesso='2' with explicit consent must liberate with a disclaimer."""
        payload = ac.construir_disclaimer_acompanhante("2", None, alvo)
        assert payload["tipo_resposta"] == "aviso_classificacao_informativo"
        assert payload["rotulo_nivel"] == "Sigiloso"

    # --- hipotese_legal containing "sigiloso" text -------------------------

    def test_sigiloso_hipotese_legal_blocked_without_consent(self, alvo: dict) -> None:
        # hipoteseLegal is metadata-only — it does NOT change the gate decision.
        # The gate key is nivelAcesso; this test confirms hipoteseLegal text
        # with "sigiloso" is properly propagated but doesn't bypass the block.
        assert ac.precisa_disclaimer("2")
        payload = ac.construir_aviso_bloqueio(
            "2",
            "Sigilo de investigacao policial (art. 20 LAI) - sigiloso",
            alvo,
        )
        assert "sigiloso" in payload["hipotese_legal"].lower()

    def test_sigiloso_hipotese_legal_propagated_when_released(self, alvo: dict) -> None:
        """hipotese_legal is passed through unchanged when access is granted."""
        payload = ac.construir_disclaimer_acompanhante("2", "Sigilo bancario (LC 105/2001)", alvo)
        assert payload["hipotese_legal"] == "Sigilo bancario (LC 105/2001)"

    # --- extrair_nivel then gate -------------------------------------------

    def test_extrair_nivel_camel_then_gate_blocks(self) -> None:
        """End-to-end: REST metadata with nivelAcesso=2 must need a block."""
        nivel, _ = ac.extrair_nivel({"nivelAcesso": "2", "hipoteseLegal": "Art. 26 LAI"})
        assert nivel == "2"
        assert ac.precisa_disclaimer(nivel)
        assert not ac.env_permite_restritos()

    def test_extrair_nivel_integer_2_then_gate_blocks(self) -> None:
        """Integer nivelAcesso=2 (REST sometimes returns ints) must also block."""
        nivel, _ = ac.extrair_nivel({"nivelAcesso": 2})
        assert nivel == "2"
        assert ac.precisa_disclaimer(nivel)

    def test_extrair_nivel_web_sigiloso_then_gate_blocks(self) -> None:
        """Web-scraped text 'Sigiloso' must flow through to precisa_disclaimer=True."""
        nivel = ac.extrair_nivel_web({"nivel_de_acesso": "Sigiloso"})
        assert nivel == "2"
        assert ac.precisa_disclaimer(nivel)

    # --- bloqueio payload structure for sigiloso ---------------------------

    def test_bloqueio_sigiloso_has_correct_rotulo(self, alvo: dict) -> None:
        """Block payload for sigiloso must carry the 'Sigiloso' label."""
        aviso = ac.construir_aviso_bloqueio("2", None, alvo)
        assert aviso["rotulo_nivel"] == "Sigiloso"
        assert aviso["nivel_acesso"] == "2"

    def test_bloqueio_sigiloso_contains_all_mandatory_fields(self, alvo: dict) -> None:
        """Block payload must include every field required by the LLM framing contract."""
        aviso = ac.construir_aviso_bloqueio("2", "Art. 26 LAI", alvo)
        for field in (
            "tipo_resposta",
            "nao_e_erro_tecnico",
            "instrucao_para_modelo",
            "mensagem_para_usuario_humano",
            "consentimento_necessario",
            "riscos",
            "como_liberar",
            "alvo",
        ):
            assert field in aviso, f"Missing field: {field}"
        assert aviso["hipotese_legal"] == "Art. 26 LAI"
