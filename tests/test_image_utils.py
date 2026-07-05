"""Testes de `todos.image_utils` (RFC 0024).

Confirmado ao vivo que o SEI descarta silenciosamente imagens embutidas
grandes demais (~104KB base64 falhou, ~26KB funcionou) — estes testes cobrem
a lógica de compressão progressiva até caber num limite, sem depender de
rede/SEI real.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from todos.exceptions import SEIValidationError
from todos.image_utils import comprimir_para_embed, montar_data_uri


def _criar_imagem_grande(tmp_path, largura=1600, altura=1200):
    caminho = tmp_path / "grande.png"
    img = Image.new("RGB", (largura, altura), color=(120, 180, 220))
    # Ruído simples pra evitar compressão perfeita (imagem lisa comprime demais
    # e não exercita a escada de tentativas).
    pixels = img.load()
    for x in range(0, largura, 7):
        for y in range(0, altura, 5):
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
    img.save(caminho, "PNG")
    return caminho


class TestComprimirParaEmbed:
    def test_comprime_ate_caber_no_limite(self, tmp_path) -> None:
        caminho = _criar_imagem_grande(tmp_path)
        b64 = comprimir_para_embed(caminho, max_base64_chars=30_000)
        assert len(b64) <= 30_000
        # confirma que é um JPEG válido decodificável
        dados = base64.b64decode(b64)
        img = Image.open(io.BytesIO(dados))
        assert img.format == "JPEG"

    def test_imagem_pequena_nao_precisa_comprimir_muito(self, tmp_path) -> None:
        caminho = tmp_path / "pequena.png"
        Image.new("RGB", (50, 50), color=(10, 20, 30)).save(caminho, "PNG")
        b64 = comprimir_para_embed(caminho, max_base64_chars=30_000)
        assert len(b64) < 30_000

    def test_limite_impossivel_levanta_erro_claro(self, tmp_path) -> None:
        caminho = _criar_imagem_grande(tmp_path, largura=3000, altura=2000)
        with pytest.raises(SEIValidationError, match="Não foi possível comprimir"):
            comprimir_para_embed(caminho, max_base64_chars=100)


class TestMontarDataUri:
    def test_monta_data_uri_com_prefixo_correto(self, tmp_path) -> None:
        caminho = tmp_path / "pequena.png"
        Image.new("RGB", (50, 50), color=(10, 20, 30)).save(caminho, "PNG")
        uri = montar_data_uri(caminho, max_base64_chars=30_000)
        assert uri.startswith("data:image/jpeg;base64,")
        b64 = uri.removeprefix("data:image/jpeg;base64,")
        base64.b64decode(b64)  # não levanta se for base64 válido
