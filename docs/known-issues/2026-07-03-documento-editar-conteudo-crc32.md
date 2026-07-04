---
status: aberto
descoberto_em: 2026-07-03
instancia: sei.sistemas.ro.gov.br (SEI 5.0.3-2.41.1, sem mod-wssei)
branch_relacionada: fix/pesquisar-unidades-envio-web
---

# Editar conteúdo de documento quebra com TypeError em `seiCrc32` (JS do próprio SEI)

## Contexto

Na mesma sessão em que corrigimos `sei_pesquisar_unidades`/`sei_enviar_processo`
(nome de ação `procedimento_enviar` vs `procedimento_tramitar`),
`sei_pesquisar_tipos_processo` (fluxo moderno `procedimento_escolher_tipo`) e
`sei_criar_documento` (flag `hdnFlagDocumentoCadastro`, campo `rdoNivelAcesso`),
conseguimos criar de verdade um processo + documento na instância real:

- Processo: `0020.010296/2026-86` (id_procedimento `76858549`)
- Documento: Ofício 15380/2026 (id_documento `76858997`, número SEI `74078183`),
  criado via `id_serie=11`

O passo seguinte natural era preencher o conteúdo do ofício
(`sei_listar_secoes`/`sei_editar_secao`, que dependem de
`_get_doc_signed_url(..., "editor_montar")` — hoje ajustado pra também tentar
`documento_alterar` como alias, já que "editor_montar" não aparece nas ações
do nó do documento nesta instância).

**Só que `documento_alterar` acabou sendo a ação errada para o conteúdo** — é
a tela de metadados do documento (só tem `txaObservacoes`, sem
`#divEditores`/textareas de seção). O alias que adicionamos hoje resolve o
sintoma (para de lançar `SEIParseError`) mas retorna `secoes: []` porque a
página não tem o conteúdo esperado.

## Reprodução manual no navegador

Abrindo o documento na UI do SEI (Chrome, sessão real autenticada como
FRANKLIN SILVEIRA BALDO / PGE-IPERON) e clicando no ícone **"Editar
Conteúdo"** da barra de ferramentas — o clique não navega, não muda nada
visualmente. O console mostra uma exceção JS **antes** de qualquer requisição
de rede acontecer (ou seja, o JS quebra no próprio handler do clique, nunca
chega a montar/enviar a URL assinada):

```
[EXCEPTION] TypeError: Cannot read properties of undefined (reading 'length')
    at crc32StringToBytes (https://sei.sistemas.ro.gov.br/sei/js/sei.js?5.0.3-2.41.1:532:29)
    at crc32GenBytes (https://sei.sistemas.ro.gov.br/sei/js/sei.js?5.0.3-2.41.1:546:17)
    at seiCrc32 (https://sei.sistemas.ro.gov.br/sei/js/sei.js?5.0.3-2.41.1:562:10)
    at HTMLAnchorElement.<anonymous> (https://sei.sistemas.ro.gov.br/sei/js/sei.js?5.0.3-2.41.1:582:18)
    at HTMLAnchorElement.dispatch (jquery-3.7.0.min.js:2:39997)
    at ce.event.add.v.handle (jquery-3.7.0.min.js:2:37968)
```

Reproduzido 2x, idêntico nas duas vezes (mesmos números de linha).

Também aparece no carregamento da página (antes de qualquer clique — pode
ser não relacionado, ruído de versão de jQuery UI):

```
TypeError: $(...).resizable is not a function
    at adicionarLinha (controlador.php?acao=procedimento_trabalhar...:102:25)
    at inicializar (...:128:5)
    at onload (...:158:33)
```

## Hipóteses (nenhuma confirmada ainda)

1. **Bug genérico do SEI 5.0.3-2.41.1 nesta instância** — reproduziria em
   qualquer documento, criado pela UI normal ou pelo scraper.
2. **Específico de documentos criados pelo nosso fluxo via scraper**
   (`criar_documento_interno_web`) — talvez falte algum campo/estado que o
   JS client-side espera e que normalmente é preenchido por alguma etapa da
   UI que pulamos ao ir direto via POST (`documento_escolher_tipo` →
   `hdnIdSerie` → `hdnFlagDocumentoCadastro=2`).

`seiCrc32` parece calcular um checksum de algum valor (provavelmente de um
atributo ou hidden field específico) antes de montar a URL assinada da ação
de editar — e esse valor está `undefined` neste caso.

## Próximos passos sugeridos (nenhum executado ainda)

- [ ] Criar um documento do zero **pela UI normal** (sem passar pelo
      scraper) nesta mesma instância e testar se "Editar Conteúdo" quebra
      do mesmo jeito. Se sim → bug do SEI, não nosso, considerar reportar
      upstream ao fornecedor do SEI. Se não → `criar_documento_interno_web`
      está deixando de configurar algo que a UI configura.
- [ ] Se for nosso: inspecionar o código-fonte de `seiCrc32`/`crc32GenBytes`
      em `sei.js` (buscar o argumento passado no handler de clique do link
      "Editar Conteúdo") e comparar o estado do DOM/cookies entre um
      documento criado via UI vs. via scraper.
- [ ] Como contorno independente da causa: já sabemos o padrão de POST
      (`_post_form_preservando`, mesmo usado em `criar_processo_web` e no
      fix de hoje para `criar_documento_interno_web`) — dá pra tentar POST
      direto em `documento_alterar`/ação equivalente para editar seções,
      sem depender do clique/JS quebrado do navegador. Requer descobrir a
      ação e os campos do form de conteúdo real (ainda não localizados).

## Ambiente

- Instância: `sei.sistemas.ro.gov.br` (SEI 5.0.3-2.41.1, sem mod-wssei —
  só backend web)
- todos-sei: branch `fix/pesquisar-unidades-envio-web`, commit `e4e34e4` no
  momento do achado
- Chrome real (não headless), sessão autenticada como usuário PGE-IPERON
