#!/usr/bin/env python3
"""Teste do parser de árvore do SEI — valida detecção de pastas colapsadas.

Testa o parse_arvore_nos() com HTML contendo PASTA (processo relacionado colapsado)
e valida que seria feita uma chamada com abrir_pastas=1.
"""

from src.mcp_seipro.sei_web_client import parse_arvore_nos

# HTML simulado com PASTA colapsada (processo relacionado)
# Nó 0: processo raiz
# Nó 1-N: documentos normais
# Nó com tipo_no="PASTA": pasta colapsada (processo relacionado) que seria expandido
SAMPLE_HTML_WITH_COLLAPSED_FOLDER = """
    Nos[0] = new infraArvoreNo("PROCESSO", "2550406", "", "", "", "50300.018005/2024-68", "Processo Principal", "processo.gif", "");
    Nos[1] = new infraArvoreNo("DOCUMENTO", "3008683", "2550406", "", "", "Recibo Eletrônico", "Recibo de entrega", "documento.gif", "");
    Nos[2] = new infraArvoreNo("PASTA", "joinPASTA1", "2550406", "javascript:void(0)", "", "Pasta Colapsada 1", "Processos Relacionados", "pasta.gif", "");
    Nos[3] = new infraArvoreNo("DOCUMENTO", "3008684", "2550406", "", "", "Despacho GPF 2874369", "Despacho", "documento.gif", "");
    Nos[4] = new infraArvoreNo("PASTA", "joinPASTA2", "2550406", "javascript:void(0)", "", "Pasta Colapsada 2", "Processos Relacionados", "pasta.gif", "");
"""

HTML_AFTER_EXPANSION = """
    Nos[0] = new infraArvoreNo("PROCESSO", "2550406", "", "", "", "50300.018005/2024-68", "Processo Principal", "processo.gif", "");
    Nos[1] = new infraArvoreNo("DOCUMENTO", "3008683", "2550406", "", "", "Recibo Eletrônico", "Recibo de entrega", "documento.gif", "");
    Nos[2] = new infraArvoreNo("PASTA", "joinPASTA1", "2550406", "", "", "Pasta Colapsada 1 - Expandida", "Processos Relacionados", "pasta.gif", "");
    Nos[3] = new infraArvoreNo("DOCUMENTO", "2333220", "joinPASTA1", "", "", "Ordem de Serviço Fiscalização 469", "OSF", "documento.gif", "");
    Nos[4] = new infraArvoreNo("DOCUMENTO", "2384065", "joinPASTA1", "", "", "Relatório Fiscalização Navegação 35", "Relatório", "documento.gif", "");
    Nos[5] = new infraArvoreNo("DOCUMENTO", "2393526", "joinPASTA1", "", "", "Auto de Infração 006779", "Auto", "documento.gif", "");
    Nos[6] = new infraArvoreNo("DOCUMENTO", "2449381", "joinPASTA1", "", "", "PATI 5", "PATI", "documento.gif", "");
    Nos[7] = new infraArvoreNo("DOCUMENTO", "2534132", "joinPASTA1", "", "", "Deliberação PAS 18", "Deliberação", "documento.gif", "");
    Nos[8] = new infraArvoreNo("DOCUMENTO", "2450074", "joinPASTA1", "", "", "Planilha Dosimetria A", "Planilha", "documento.gif", "");
    Nos[9] = new infraArvoreNo("DOCUMENTO", "2450076", "joinPASTA1", "", "", "Planilha Dosimetria B", "Planilha", "documento.gif", "");
    Nos[10] = new infraArvoreNo("DOCUMENTO", "2450077", "joinPASTA1", "", "", "Planilha Dosimetria C", "Planilha", "documento.gif", "");
    Nos[11] = new infraArvoreNo("PASTA", "joinPASTA2", "2550406", "", "", "Pasta Colapsada 2 - Expandida", "Processos Relacionados", "pasta.gif", "");
    Nos[12] = new infraArvoreNo("DOCUMENTO", "3008684", "joinPASTA2", "", "", "Despacho GPF 2874369", "Despacho", "documento.gif", "");
"""


def test_parse_arvore_with_pastas():
    """Testa se o parser detecta corretamente PASTA (pastas colapsadas)."""
    print("🔍 Testando parse_arvore_nos() com PASTA colapsadas...\n")

    # Parse do HTML com pastas colapsadas
    nos = parse_arvore_nos(SAMPLE_HTML_WITH_COLLAPSED_FOLDER)

    print(f"   Nós parseados: {len(nos)}")
    for i, n in enumerate(nos):
        tipo = n.get("tipo_no", "?")
        label = n.get("label", "?")
        print(f"   [{i}] {tipo:12} → {label}")
    print()

    # Valida detecção de PASTA
    pastas = [n for n in nos[1:] if n.get("tipo_no") == "PASTA"]
    print(f"✓ Pastas colapsadas detectadas: {len(pastas)}")
    assert len(pastas) == 2, f"Esperado 2 PASTs, encontrado {len(pastas)}"

    # Documentos antes da expansão (exclui root e PASTA)
    docs_antes = [n for n in nos[1:] if n.get("tipo_no") != "PASTA"]
    print(f"✓ Documentos visíveis (sem pastas): {len(docs_antes)}")
    assert len(docs_antes) == 2, f"Esperado 2 documentos, encontrado {len(docs_antes)}"
    print()

    # Parse do HTML após expansão (abrir_pastas=1)
    nos_after = parse_arvore_nos(HTML_AFTER_EXPANSION)
    print(f"   Nós após expansão: {len(nos_after)}")

    docs_depois = [n for n in nos_after[1:] if n.get("tipo_no") != "PASTA"]
    print(f"✓ Documentos após expansão: {len(docs_depois)}")
    assert len(docs_depois) > len(docs_antes), "Deve haver mais documentos após expansão"
    print()

    # Valida SEIs esperados
    seis_esperados = {"2333220", "2384065", "2393526", "2449381", "2534132", "2450074", "2450076", "2450077"}
    seis_encontrados = {n.get("id") for n in docs_depois}

    print("   Documentos esperados após expansão:")
    for sei in sorted(seis_esperados):
        found = sei in seis_encontrados
        status = "✓" if found else "✗"
        print(f"   {status} {sei}")

    assert seis_esperados.issubset(seis_encontrados), "Alguns SEIs esperados não foram encontrados"
    print()

    print("✅ TESTE PASSOU — Detector de pastas colapsadas funcionando corretamente")
    print("   Recomendação: Quando há PASTA, fazer GET com ?abrir_pastas=1 para expandir")
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.exit(test_parse_arvore_with_pastas())
    except AssertionError as e:
        print(f"❌ TESTE FALHOU: {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"❌ Erro: {e}")
        traceback.print_exc()
        sys.exit(1)
