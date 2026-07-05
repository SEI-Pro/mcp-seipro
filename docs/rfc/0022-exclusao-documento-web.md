# RFC 0022 — Exclusão de documento via scraper web

**Status**: Proposta (investigação parcial, sem implementação) · **Data**: 2026-07-04
**Autores**: Claude (com Franklin Baldo)

## 1. Contexto

Esta sessão precisou excluir um documento externo criado por engano (um "Anexo"
duplicado, criado antes de se decidir por embutir a imagem diretamente no HTML
da Certidão via RFC 0020's `alterar_secoes_web`). `todos` **não tem nenhuma
tool nem método no scraper web** para excluir documento — nem
`sei_excluir_documento` nem equivalente. Uma busca no código (`grep -rn
"documento_excluir\|excluir_documento"`) não encontrou nada.

A tentativa de reverse-engineering ao vivo (mesma metodologia usada esta sessão
pra `criar_documento_interno_web`, `_executar_acao_bloco_form`, etc.) achou o
gatilho do botão "Excluir" na árvore do processo, mas **não** achou onde a
função JS correspondente está definida — ela não está na página retornada por
`_arvore_do_processo()` (que contém a árvore/`Nos[N]`, mas aparentemente não o
toolbar). Provavelmente vive em outro frame do frameset maior
(`procedimento_trabalhar`/`procedimento_visualizar`), que esta investigação não
chegou a enumerar.

## 2. O que já foi confirmado (ponto de partida pra quem continuar)

Na árvore de um processo (`_arvore_do_processo`), cada nó de documento tem uma
string `Nos[N].acoes` com os ícones de ação disponíveis. Pra um documento
externo comum, o botão de exclusão aparece como:

```html
<a href="#" onclick="excluirDocumento();"><img src="svg/excluir.svg?25" alt="Excluir" title="Excluir"/></a>
```

(nome exato do ícone SVG e do `alt`/`title` não confirmados — o trecho ao vivo
capturado nesta sessão, reproduzido abaixo, mostra o `onclick` mas o dump
truncou antes do `<img>` correspondente a essa ação específica; **confirme o
`alt`/`title` reais ao investigar**.)

**Trecho real capturado** (documento externo "Anexo", processo
`0016.004284/2026-81`, nó `Nos[10]` — outros ícones do mesmo nó, pra
contexto de como o restante das ações desse nó se parece):

```js
Nos[10] = new infraArvoreNo("DOCUMENTO","76861634","76635483","controlador.php?acao=arvore_visualizar&acao_origem=procedimento_visualizar&id_procedimento=76635483&id_documento=76861634&infra_sistema=100000100&infra_unidade_atual=110007726&infra_hash=0d916ff8502c7e82cb2779d9440604cd9100d412f61fbf20acac7550b5886ff8","ifrConteudoVisualizacao","Anexo (74080657)","Anexo (74080657)","svg/documento_imagem.svg?25","svg/documento_imagem.svg?25","svg/documento_imagem.svg?25",true,true,null,null,"noVisitado","74080657");
Nos[10].assinatura = '';
Nos[10].acoes = '<a href="controlador.php?acao=documento_escolher_tipo&acao_origem=arvore_visualizar&acao_retorno=arvore_visualizar&id_procedimento=76635483&arvore=1&infra_sistema=100000100&infra_unidade_atual=110007726&infra_hash=046f2f91c14729f31935d7bcc529d79a5461f7a7a5a051211e71a02e8cbce6b8" tabindex="452" ><img src="svg/documento_incluir.svg?25" alt="Incluir Documento" title="Incluir Documento"/></a><a href="controlador.php?acao=documento_alterar_recebido&acao_origem=arvore_visualizar&acao_retorno=arvore_visualizar&id_procedimento=76635483&id_documento=76861634&arvore=1&infra_sistema=100000100&infra_unidade_atual=110007726&infra_hash=730f83230f2d7e7ffffa016245d54c2973c873d86e8d0d68baaa00ffdf4152f1" tabindex="452" ><img src="svg/documento_alterar.svg?25" alt="Consultar/Alterar Documento Externo" title="Consultar/Alterar Documento Externo" /></a><a href="controlador.php?acao=acompanhamento_gerenciar&acao_origem=arvore_visualizar&acao_retorno=arvore_visualizar&id_procedimento=76635483&arvore=1&infra_sistema=100000100&infra_unidade_atual=110007726&infra_hash=2ec8ca394d4f02d72effa337ccfe9b1fb646d13d38762982f4dd0ff504869a8f" tabindex="452" ><img src="svg/acompanhamento_especial_cadastro.svg?25" alt="Acompanhamento Especial" title="Acompanhamento Especial"/></a><a href="#" onclick="cienciaDocumento();" tabindex="452" ><img src="svg/ciencia.svg?25" alt="Ciência" title="Ciência" /></a><a href="controlador.php?acao=procedimento_enviar&acao_origem=arvore_visualizar&acao_retorno=arvore_visualizar&id_procedimento=76635483&arvore=1&infra_sistema=100000100&infra_unidade_atual=110007726&infra_hash=9b1666da3c93a58bd28139abc91973db9bab0afa0a75616a6c104f1b6745b181" tabindex="452" ><img src="svg/processo_enviar.svg?25" alt="Enviar Processo" title="Enviar Processo" /></a>...'
```

`onclick="excluirDocumento();"` (achado via busca ampla por
`onclick="([^"]*[Ee]xclui[^"]*)"` na árvore inteira, não restrita a um nó
específico — apareceu 2 vezes no total) — **sem parâmetros** no próprio
onclick, diferente do padrão `acaoExcluir(id, desc, tipo)` já visto em
bloco de assinatura (RFC/PR #127-#129). Isso sugere que `excluirDocumento()`
lê o documento/nó alvo de **estado global** (provavelmente qual nó está
selecionado no momento na árvore, guardado em variável JS ou campo hidden
compartilhado), não de argumentos explícitos — padrão distinto do que já
foi mapeado nesta base de código até agora.

`function excluirDocumento` **não** foi encontrada no HTML retornado por
`_arvore_do_processo()` (nem por busca direta de string, nem no dump
completo do nó) — está definida em outro lugar, não localizado nesta sessão.

## 3. Hipóteses e próximos passos pra quem for implementar

1. **Encontrar onde `excluirDocumento()` é definida.** `_arvore_do_processo()`
   busca `url_arvore` com `acao=procedimento_visualizar` — isso é uma página
   dentro de um frameset maior (`procedimento_trabalhar` ou similar) que
   provavelmente tem MAIS de um frame (árvore + toolbar + conteúdo). Enumere
   os frames dessa página pai (não só o fragmento que
   `_arvore_do_processo()` já isola) e busque `function excluirDocumento`
   em cada um.

2. **Investigar se a ação depende de "nó selecionado".** Se `excluirDocumento()`
   de fato lê algum estado setado por outro evento JS (ex.: `onclick` do
   próprio nó da árvore, que roda ao clicar para "selecionar" o documento
   antes de qualquer ação de toolbar) — vai ser necessário replicar esse
   "efeito colateral" de seleção (provavelmente popular um campo hidden com
   `id_documento`/`id_procedimento`) antes de conseguir montar a URL/POST de
   exclusão real. Isso é um padrão mais complexo que os já resolvidos nesta
   sessão (RFC 0020, PR #129, PR #135) — todos tinham o alvo da ação
   identificável diretamente na URL ou em `Nos[N].acoes` sem depender de
   estado de seleção prévio.

3. **Verificar se mod-wssei (REST) já tem esse endpoint.** Se a instância
   tiver mod-wssei instalado, pode existir uma rota REST de exclusão de
   documento mais direta, evitando esse reverse-engineering todo do
   caminho web. Vale checar a documentação/schema do mod-wssei antes de
   investir mais tempo no caminho scraper.

4. **Considerar se vale a pena, dado o risco.** Diferente de anotar/incluir/
   retirar documento de bloco de assinatura (reversíveis, baixo risco),
   excluir documento é **destrutivo e majoritariamente irreversível** no
   SEI — e o próprio SEI já restringe quando é permitido (documentos só
   podem ser excluídos enquanto o processo não foi lido/tramitado por outra
   unidade — mesma limitação documentada em `sei_cancelar_assinatura`,
   provavelmente ainda mais restritiva aqui). Qualquer implementação futura
   deveria:
   - confirmar explicitamente com o usuário antes de executar (nunca
     inferir/decidir sozinho que um documento deve ser excluído);
   - verificar o estado do processo/documento antes (não tramitado, não
     assinado, sem referências dependentes) e falhar com erro claro em vez
     de tentar e deixar o SEI rejeitar silenciosamente;
   - ter cobertura de teste equivalente à exigida no restante do projeto
     (mock + confirmação ao vivo antes de merge, seguindo a disciplina
     estabelecida nesta sessão de "não confiar em `ok:true` sem reconferir
     via leitura independente").

## 4. Não-objetivos desta RFC

Esta RFC **não** propõe uma implementação — documenta uma investigação
incompleta e os próximos passos, para que o trabalho já feito não se perca
e outra sessão/agente não precise repetir a mesma exploração do zero.
