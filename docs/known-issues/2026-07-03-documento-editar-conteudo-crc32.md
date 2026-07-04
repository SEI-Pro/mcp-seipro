---
status: causa-raiz-confirmada
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

## Causa raiz (confirmada)

**É um bug genérico do SEI 5.0.3-2.41.1 nesta instância — não tem relação
com como criamos o documento.** Confirmado inspecionando o DOM real:

```js
// Ícone do processo (funciona):
<img src="svg/processo_alterar.svg" title="Consultar/Alterar Processo" alt="Consultar/Alterar Processo">

// Ícone do documento "Editar Conteúdo" (quebra):
<img src="svg/documento_editar_conteudo.svg" title=null alt="Editar Conteúdo">
```

Os ícones da barra de ferramentas de **documento** só têm atributo `alt`,
sem `title` — mas `seiAssociarRegistroExibicaoBotoes` (função genérica do
`sei.js`, usada por toda barra de ferramentas do sistema para registrar
qual botão o usuário mais usa) sempre lê `img.attr("title")`:

```js
// sei.js:579-582
function seiAssociarRegistroExibicaoBotoes(tipo, divId, link){
  $(document.getElementById(divId)).children().on("click",function () {
    var img = $(this).find('img:first');
    var titulo = seiCrc32(img.attr("title"));   // title é null aqui → undefined → crash
```

Isso quebra **antes** da navegação real acontecer — o clique nunca chega
no `onclick="editarConteudo('N')"` do link (`editarConteudo` abre uma
**janela popup** via `infraAbrirJanela`, não navega os iframes).

**Confirmado que `editarConteudo('N')` funciona normalmente quando chamado
direto** (bypassando o clique/handler quebrado): sem exceção. E, aplicando
um patch temporário em runtime (`window.seiCrc32 = str => str ?? ''` em
todos os frames) antes do clique, o clique real passa **sem nenhum erro no
console**. Não deu pra confirmar se a janela popup do editor efetivamente
abriu nesse teste — a ferramenta de automação usada (Claude in Chrome)
não rastreia janelas popup fora do grupo de abas que ela controla, então
a ausência de uma nova aba visível não é prova de falha.

## Próximos passos sugeridos

- [ ] **Testar num navegador comum** (não via automação): aplicar o mesmo
      patch runtime no console do DevTools antes de clicar em "Editar
      Conteúdo", e ver diretamente se a janela popup do editor abre. Isso
      confirmaria definitivamente que o contorno funciona.
- [ ] Considerar reportar o bug (`title=null` nos ícones de documento +
      `seiAssociarRegistroExibicaoBotoes` não trata isso) ao fornecedor/
      mantenedor do SEI desta instância — é um bug real da aplicação,
      independente do nosso scraper.
- [ ] Como contorno client-side permanente (sem depender de reportar e
      esperar correção): um `content script`/bookmarklet que aplica o
      mesmo patch (`window.seiCrc32 = str => str ?? ''`) automaticamente
      ao carregar qualquer página do SEI, para todo mundo que usa o
      sistema, não só nesta investigação.
- [ ] Alternativa server-side (scraper): já sabemos o padrão de POST
      (`_post_form_preservando`) usado nos outros fixes desta branch —
      ainda vale tentar achar a ação/campos do form de conteúdo real via
      HTTP puro, sem depender do JS do navegador em nenhuma hipótese,
      mas ainda não localizamos esse form (documento_alterar não é ele).

## Ambiente

- Instância: `sei.sistemas.ro.gov.br` (SEI 5.0.3-2.41.1, sem mod-wssei —
  só backend web)
- todos-sei: branch `fix/pesquisar-unidades-envio-web`, commit `e4e34e4` no
  momento do achado
- Chrome real (não headless), sessão autenticada como usuário PGE-IPERON
