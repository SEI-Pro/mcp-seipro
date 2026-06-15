"""Tests for sei_styles — the SEI CSS-style catalog and HTML reference helpers.

Pure module: the two helpers build the exact HTML the SEI editor expects for
document references and addressees, and STYLE_SHORTCUTS maps intents onto the
real style catalog. A typo in a shortcut target or a malformed anchor span would
silently produce a document the SEI web UI can't link or address.
"""

from __future__ import annotations

from todos import sei_styles as ss


class TestHtmlReferenciaSei:
    def test_embeds_id_and_numero(self) -> None:
        out = ss.html_referencia_sei("683594", "0627920")
        assert 'id="lnkSei683594"' in out
        assert ">0627920</a>" in out
        assert 'class="ancoraSei"' in out

    def test_is_a_self_contained_span(self) -> None:
        out = ss.html_referencia_sei("1", "2")
        assert out.startswith("<span")
        assert out.endswith("</span>")


class TestHtmlDestinatario:
    def test_embeds_unidade_sigla_nome(self) -> None:
        out = ss.html_destinatario("110000061", "SFC", "Superintendência X")
        assert 'data-id="110000061"' in out
        assert "SFC - Superintendência X" in out

    def test_uses_anchor_and_alignment_classes(self) -> None:
        out = ss.html_destinatario("1", "AB", "Nome")
        assert 'class="Texto_Alinhado_Esquerda"' in out
        assert "ancoraSei interessadoSeiPro" in out
        # &Agrave; is the SEI-required "À" entity before the addressee.
        assert "&Agrave;" in out


class TestStyleCatalogIntegrity:
    def test_every_shortcut_targets_a_real_style(self) -> None:
        # A shortcut pointing at a non-existent style class would emit a class
        # the SEI editor ignores. This is the invariant most likely to rot.
        unknown = {
            atalho: alvo for atalho, alvo in ss.STYLE_SHORTCUTS.items() if alvo not in ss.SEI_STYLES
        }
        assert not unknown, f"shortcuts target unknown styles: {unknown}"

    def test_every_style_has_descricao_and_exemplo(self) -> None:
        faltando = {
            nome: sorted({"descricao", "exemplo"} - set(meta))
            for nome, meta in ss.SEI_STYLES.items()
            if not {"descricao", "exemplo"} <= set(meta)
        }
        assert not faltando, f"styles missing required keys: {faltando}"

    def test_exemplo_references_its_own_class(self) -> None:
        # Each example HTML should actually use the class it documents.
        descasados = [nome for nome, meta in ss.SEI_STYLES.items() if nome not in meta["exemplo"]]
        assert not descasados, f"examples not referencing their class: {descasados}"
