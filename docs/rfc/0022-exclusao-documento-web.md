# RFC 0022 — Exclusão de documento via scraper web

**Status**: ✅ Implementada · **Data**: 2026-07-04 · **Atualizado**: 2026-07-05
**Autores**: Claude (com Franklin Baldo)

## 1. Contexto

Esta sessão precisou excluir um documento externo criado por engano (um "Anexo"
duplicado, criado antes de se decidir por embutir a imagem diretamente no HTML
da Certidão via RFC 0020's `alterar_secoes_web`). `todos` não tinha nenhuma
tool nem método no scraper web para excluir documento — nem
`sei_excluir_documento` nem equivalente.

A investigação original desta RFC tentou reverse-engineering ao vivo do botão
"Excluir" na árvore do processo e não achou onde a função JS
`excluirDocumento()` correspondente estava definida — levantando a hipótese de
que seria necessário enumerar frames do frameset maior
(`procedimento_trabalhar`/`procedimento_visualizar`) e replicar algum estado de
"nó selecionado" antes de conseguir montar a URL de exclusão real.

**Essa hipótese estava errada.** Revisão da PR #137 apontou o caminho certo: o
padrão já existente `_get_arvore_visualizar_link_var()`
(`src/todos/sei_web_client.py`), já usado para `linkEditarConteudo`
(`editor_montar`) e `linkAssinarDocumento` (`documento_assinar`), resolve
exatamente esse tipo de ação sem reverse-engineering adicional — a URL de
exclusão é só mais uma variável JS `linkX` (`linkExcluirDocumento`) embutida na
mesma página `arvore_visualizar` já buscada para as outras ações. Não há
frames extras para enumerar nem estado de seleção para replicar; a hipótese
original nasceu de olhar só para `Nos[].acoes` (onde o
`onclick="excluirDocumento();"` de fato não carrega parâmetros) sem notar que
outras ações do mesmo menu já haviam sido resolvidas por esse segundo caminho
(`arvore_visualizar` → variável `linkX`), não por `Nos[].acoes`.

## 2. Solução implementada

`SEIWebClient.excluir_documento_web(protocolo, id_documento, *, confirmar=False)`
segue o mesmo fluxo de duas etapas de `assinar_documento_web`:

1. Resolve `linkExcluirDocumento` via `_get_arvore_visualizar_link_var(protocolo,
   id_documento, "linkExcluirDocumento")` — o mesmo helper, sem nenhuma
   modificação.
2. Um único `GET` na URL resolvida já executa a exclusão de verdade — **não**
   é uma página de confirmação que exige um segundo POST.

Confirmado ao vivo em sei.sistemas.ro.gov.br, 2026-07-03/04, processo
`0016.004284/2026-81`, documento `76861634` (o mesmo "Anexo" duplicado citado
na seção 1):

```python
delete_url, referer = await client._get_arvore_visualizar_link_var(
    "0016.004284/2026-81", "76861634", "linkExcluirDocumento"
)
r = await client._http.get(delete_url, headers={"Referer": referer})
# status 200
```

Reconfirmado via leitura **independente** (não apenas o status HTTP 200):
`sei_consultar_documento_externo` passou a falhar com "Ação
'documento_consultar' não encontrada" (o nó já não existe mais) e
`sei_listar_documentos` deixou de listar o documento — com os outros 7
documentos do processo permanecendo intactos.

Por ser destrutiva/irreversível, a implementação final adiciona as camadas de
segurança que a investigação original já havia antecipado como necessárias
(seção 3, item 4 da versão anterior desta RFC):

- **`confirmar=True` obrigatório** — sem ele, a operação recusa antes de
  qualquer chamada HTTP. Nunca infere sozinha que um documento deve ser
  excluído.
- **Ausência de `linkExcluirDocumento` = recusa legítima do SEI**, não bug de
  parsing — o SEI só oferece a variável quando a exclusão é permitida
  (documento não assinado, processo não tramitado/lido por outra unidade,
  mesma classe de restrição já documentada em `sei_cancelar_assinatura`).
  Propaga um `SEIValidationError` explicando a causa provável.
- **Reconfirmação pós-exclusão via releitura da árvore** — depois do GET, a
  árvore do processo é relida (`_invalidar_arvore` + nova
  `_arvore_do_processo`) e o nó é procurado de novo via `parse_arvore_nos` +
  comparação de `id` — não um `"{id_documento}" in html` ingênuo. Esse detalhe
  importa na prática: um teste desta mesma investigação constrói de propósito
  um HTML pós-exclusão em que o hash de outro nó contém, por coincidência, os
  mesmos dígitos do id excluído — um `in html` teria reportado "ainda
  presente" (falso positivo). Só quando o nó realmente não está mais presente
  a função retorna sucesso; caso contrário levanta erro em vez de reportar
  `{"status": "ok"}` indevido.

## 3. Onde está o código

- `SEIWebClient.excluir_documento_web` — `src/todos/sei_web_client.py`
  (logo após `assinar_documento_web`).
- `DocumentosWeb.excluir_documento` — `src/todos/backends/web/documentos.py`
  (delega ao método acima; exige `processo` explícito, como as demais
  operações web-only).
- `SEIBackend.excluir_documento` — `src/todos/backends/base.py` (stub do
  contrato; sem contraparte REST/mod-wssei — instâncias com mod-wssei que
  escolherem `backend="rest"` recebem `SEINotImplementedError`).
- Tool MCP `sei_excluir_documento` — `src/todos/tools/assinatura.py`.
- Testes: `tests/test_sei_web_client_excluir_documento.py` (sucesso, recusa
  por `confirmar=False`, recusa legítima do SEI, exclusão sem efeito) e
  `tests/test_backends.py` (delegação do backend web).

## 4. Não-objetivos

Não foi investigado se mod-wssei (REST) expõe uma rota de exclusão de
documento mais direta — a hipótese 3 da versão original desta RFC. Como o
caminho web já resolve o caso de uso real desta sessão (instância sem
mod-wssei) com baixo custo de implementação, isso ficou fora de escopo.
Quem precisar de exclusão via REST deve investigar separadamente e, se
existir, adicionar `excluir_documento` a `SEIRestBackend`.
