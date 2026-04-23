#!/usr/bin/env python3
"""Teste de validação: expandir pastas colapsadas na árvore do processo.

Testa o processo 50300.018005/2024-68 que deveria retornar ~30 documentos
mas estava retornando apenas ~12 por causa de pastas colapsadas.

Uso: python test_arvore_expansion.py
"""

import asyncio
import os
import sys


async def test_arvore_expansion():
    """Testa se a correção expande as pastas colapsadas."""
    # Valida variáveis de ambiente
    sei_url = os.environ.get("SEI_URL")
    sei_usuario = os.environ.get("SEI_USUARIO")
    sei_senha = os.environ.get("SEI_SENHA")

    if not all([sei_url, sei_usuario, sei_senha]):
        print("❌ Erro: Variáveis de ambiente não configuradas.")
        print("   Configure: SEI_URL, SEI_USUARIO, SEI_SENHA")
        sys.exit(1)

    # Importa o cliente após garantir que src está no path
    from src.mcp_seipro.sei_web_client import SEIWebClient

    cliente = SEIWebClient(
        sei_url=sei_url,
        sei_usuario=sei_usuario,
        sei_senha=sei_senha,
        sei_sigla_orgao=os.environ.get("SEI_SIGLA_ORGAO", "ANTAQ"),
    )

    try:
        print("🔐 Fazendo login no SEI...")
        await cliente.login()
        print("✓ Login bem-sucedido\n")

        protocolo = "50300.018005/2024-68"
        print(f"📋 Consultando processo: {protocolo}")
        resultado = await cliente.listar_documentos(protocolo)
        print(f"✓ Processo consultado\n")

        # Validações
        total = resultado["total_documentos"]
        documentos = resultado["documentos"]

        # SEIs esperados (conforme o bug report)
        seis_esperados = {
            "2333220": "OSF 469",
            "2384065": "Relatório de Fiscalização 35",
            "2393526": "Auto de Infração 006779",
            "2449381": "PATI 5",
            "2534132": "Deliberação PAS 18",
        }

        seis_retornados = {
            d["numero_sei"]: d["tipo_documento"]
            for d in documentos
            if "numero_sei" in d
        }

        print(f"📊 Resultados:")
        print(f"   Total de documentos: {total}")
        print(f"   Esperado: ≥25 documentos")
        print()

        # Valida presença dos documentos esperados
        documentos_encontrados = []
        documentos_ausentes = []

        for sei, descricao in seis_esperados.items():
            if sei in seis_retornados:
                documentos_encontrados.append((sei, seis_retornados[sei]))
                print(f"   ✓ {sei} — {descricao} ({seis_retornados[sei]})")
            else:
                documentos_ausentes.append((sei, descricao))
                print(f"   ✗ {sei} — {descricao} [NÃO ENCONTRADO]")

        print()

        # Resumo das validações
        success = True
        if total < 25:
            print(f"❌ FALHA: Esperado ≥25 documentos, retornou {total}")
            success = False
        else:
            print(f"✅ PASSA: Total de documentos ({total}) ≥ 25")

        if documentos_ausentes:
            print(f"❌ FALHA: {len(documentos_ausentes)} documento(s) esperado(s) não encontrado(s)")
            for sei, desc in documentos_ausentes:
                print(f"   - {sei}: {desc}")
            success = False
        else:
            print(f"✅ PASSA: Todos os {len(seis_esperados)} documento(s) esperado(s) encontrado(s)")

        print()
        if success:
            print("🎉 TESTE PASSOU — Pastas foram expandidas corretamente!")
            return 0
        else:
            print("⚠️  TESTE FALHOU — Pastas ainda não foram expandidas")
            return 1

    finally:
        await cliente.close()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(test_arvore_expansion())
        sys.exit(exit_code)
    except Exception as e:
        import traceback
        print(f"❌ Erro: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
