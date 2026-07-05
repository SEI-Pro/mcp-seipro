# RFC 0025 — Planos Declarativos de Ação para Páginas do SEI

- **Status:** Draft
- **Data:** 2026-07-04
- **Relacionada a:** RFC 0020 (inspeção e submissão genéricas), RFC 0022 (exclusão de documento)

## Resumo

O `todos` já possui duas primitives genéricas úteis para descobrir e operar páginas do frontend do SEI:

- `sei_inspecionar_pagina`, que lista formulários, ações e JavaScript estático;
- `sei_submeter_form`, que relê uma página e reenvia um formulário preservando seu estado.

Elas reduzem o tempo para investigar fluxos novos, mas a fronteira atual é inadequada para transformar descoberta em operação confiável. A inspeção aceita uma URL assinada arbitrária e faz `GET`, embora no SEI algumas ações mutantes sejam disparadas por `GET` (por exemplo, URLs expostas como `linkReabrirProcesso`). A submissão recebe URL, `form_id`, destino opcional e overrides soltos; isso deixa o agente responsável por reter hashes efêmeros, escolher o botão correto, interpretar callbacks JavaScript e provar que a ação teve efeito.

Esta RFC substitui essa passagem informal de URLs por **planos declarativos de ação**: a inspeção extrai gatilhos estáticos conhecidos, guarda o estado sensível no servidor sob uma referência opaca e devolve ações executáveis apenas por identificador. A execução aceita um plano previamente descoberto, jamais JavaScript nem URL de destino arbitrária.

## Problema

Há três famílias recorrentes de interação no SEI:

1. **Link direto:** um `href` contém uma URL assinada para uma página ou ação.
2. **Variável de visualização:** a página `arvore_visualizar` expõe `var linkX = '...'`; o frontend apenas navega para a URL assinada. `linkExcluirDocumento`, `linkEditarConteudo`, `linkAssinarDocumento` e `linkReabrirProcesso` pertencem a essa família.
3. **Callback que reprograma um formulário:** uma função como `acaoExcluir(id, descricao)` altera campos ocultos, troca `form.action` por uma URL assinada e chama `submit()`.

A inspeção atual identifica parcialmente essas estruturas, mas não produz uma representação executável suficiente. Em particular:

- uma URL `GET` não é sinônimo de leitura;
- `onclick_funcao` registra o nome da função, mas não seus argumentos;
- o coletor de estado reduz campos repetidos a `dict[str, str]`, perdendo checkbox groups e `<select multiple>`;
- uma página sem `id` de formulário pode ser inspecionada, mas não submetida pelo contrato público;
- a escolha do submit button é implícita;
- o resultado de um `POST 200` não prova que o SEI aplicou a alteração;
- hashes, actions e campos ocultos efêmeros vazam para o chamador e tendem a ficar stale entre chamadas.

O caso da exclusão de documento deixou isso claro: não era necessário reproduzir o estado global da árvore nem executar JavaScript. Bastava abrir a página de visualização do nó e resolver a variável `linkExcluirDocumento`. O problema geral, portanto, é de **descoberta estruturada e execução segura**, não de automação de navegador.

## Objetivos

1. Permitir que novos fluxos simples do SEI sejam implementados sem um novo scraper completo.
2. Não executar JavaScript fornecido pelo chamador.
3. Não tratar `GET` como operação automaticamente segura.
4. Preservar a semântica HTML de controles repetidos e multivalorados.
5. Não expor hashes efêmeros como contrato entre ferramentas.
6. Tornar confirmação e verificação pós-ação partes explícitas da API.
7. Manter ferramentas tipadas como a interface preferida para operações frequentes ou de risco.

## Não objetivos

- Implementar um interpretador JavaScript ou um navegador headless.
- Expor um proxy genérico para qualquer endpoint autenticado da instância.
- Substituir ferramentas tipadas existentes.
- Inferir que uma ação é segura apenas por conter uma URL ou uma função JavaScript conhecida.

## Proposta

### 1. Referência opaca de página

`sei_inspecionar_pagina` passa a retornar uma `page_ref` de curta duração, vinculada à sessão, ao URL base e a uma impressão estrutural da página.

```json
{
  "page_ref": "sei-page:01J…",
  "expires_in_seconds": 120,
  "title": "Gerenciar Marcadores",
  "forms": [],
  "triggers": [],
  "actions": []
}
```

O servidor conserva URL, referer, HTML usado para inspeção e campos ocultos. O cliente não precisa transportar nem reutilizar `infra_hash`.

Na execução, a página será relida. O servidor deve comparar, no mínimo, a existência do formulário/gatilho, o nome da ação e a forma básica dos campos. Mudança incompatível gera erro de referência stale, e não uma tentativa com estado antigo.

### 2. Inspeção é estritamente não mutante

A ferramenta de inspeção não deve seguir uma URL apenas por pertencer ao mesmo host. A classificação de uma URL vem antes do fetch:

- **página de leitura conhecida:** pode ser obtida e inspecionada;
- **URL de ação conhecida ou não classificada:** deve ser descrita como descoberta, sem ser acessada;
- **URL externa ou fora da raiz da instância:** rejeitada.

A saída pode conter metadados de uma ação descoberta, mas não sua URL assinada em claro. `incluir_raw=True` deve ocultar `infra_hash`, tokens e valores de campos ocultos sensíveis por padrão; um modo de depuração mais amplo, se mantido, deve ser deliberadamente separado da ferramenta MCP pública.

### 3. Formulários e gatilhos como planos declarativos

A inspeção deve produzir `form_ref` e `trigger_id` estáveis dentro de uma `page_ref`, além de um plano interpretável sem JavaScript.

```json
{
  "trigger_id": "trigger:delete:row-76861634-123",
  "label": "Excluir",
  "kind": "form_submit",
  "risk": "destructive",
  "form_ref": "form:frmRelBlocoProtocoloLista",
  "submit_button": {"name": "sbmExcluir", "value": "Excluir"},
  "arguments": [{"name": "item_id", "value": "76861634-123"}],
  "mutations": [
    {"field": "hdnInfraItemId", "value_from": "item_id"},
    {"form_action": "rel_bloco_protocolo_excluir"}
  ]
}
```

O parser suporta inicialmente apenas padrões estáticos e verificáveis:

- `location.href` ou `window.location.href` com literal;
- variáveis `linkX` com URL literal;
- atribuição de campo de formulário a argumento literal do callback;
- troca de `form.action` por literal;
- `form.submit()`;
- submit HTML direto.

Callbacks que escapem desse subconjunto são retornados como `unsupported_callback`, com nome e motivo, mas não são executáveis pela API genérica.

**Callbacks não entram como input.** Aceitar uma callback, um fragmento JavaScript ou um “script” do chamador converteria a ferramenta em um executor de browser improvisado, impediria validação confiável e apagaria a fronteira entre inspeção e alteração. O input deve sempre referir um plano já descoberto.

### 4. Estado de formulário fiel ao HTML

O estado interno de formulários passa a ser uma lista ordenada de pares:

```python
list[tuple[str, str]]
```

Isso preserva controles com o mesmo `name`, checkbox groups e selects múltiplos. No contrato MCP, os overrides podem ser modelados como:

```json
[
  {"name": "selUnidades", "value": "123"},
  {"name": "selUnidades", "value": "456"}
]
```

ou, onde ergonomicamente adequado:

```json
{"selUnidades": ["123", "456"]}
```

A inspeção deve informar, para cada campo: `name`, tipo, valor(es) atuais, `checked`, `disabled`, `readonly`, `required`, `multiple`, opções e se o campo é oculto. Campos ocultos podem aparecer pelo nome e por um indicador de presença, sem vazar seu valor.

Formulários sem atributo `id` recebem um `form_ref` derivado de sua posição e de uma impressão estrutural. A API pública não deve depender exclusivamente de `form_id`.

### 5. Submissão por plano, não por URL arbitrária

Introduzir:

```python
sei_executar_plano_sei(
    page_ref: str,
    trigger_id: str,
    overrides: list[dict[str, str]] | None = None,
    submit_button: dict[str, str] | None = None,
    confirmar: bool = False,
    expect: dict | None = None,
)
```

Regras:

- a ação deve pertencer à `page_ref` e continuar compatível após releitura;
- operações `write` e `destructive` exigem `confirmar=True`;
- com mais de um submit button possível, `submit_button` é obrigatório;
- `url_destino` arbitrária não faz parte da nova API;
- a codificação continua obedecendo charset/enctype do formulário e a convenção ISO-8859-1 do SEI quando aplicável;
- a execução invalida caches relacionados somente depois de resposta sem erro e procede à verificação requerida.

`sei_submeter_form` pode permanecer temporariamente como ferramenta de compatibilidade/diagnóstico, mas deve ser marcada como legada e não deve ser usada por novas ferramentas tipadas.

### 6. Verificação pós-ação

A resposta HTTP não é prova suficiente. A execução deve aceitar ou exigir uma pós-condição compatível com o plano:

```json
{
  "kind": "row_absent",
  "key": "76861634-123"
}
```

Predicados iniciais:

- `document_absent_from_tree`;
- `document_present_in_tree`;
- `row_absent` / `row_present` em tabela conhecida;
- `field_equals` após releitura;
- `marker_absent` / `marker_present`;
- `process_state` em valor esperado.

Ferramentas tipadas podem fornecer pós-condições obrigatórias. A API genérica deve devolver `verification: "not_requested"` ou `"unsupported"` de modo explícito; nunca deve apresentar “sem erro HTTP” como sucesso material.

## Interface proposta

### Inspeção

```python
sei_inspecionar_pagina(
    url: str,
    incluir_raw: bool = False,
) -> PageInspection
```

A forma de entrada permanece temporariamente compatível, mas o resultado é centrado em `page_ref`, `form_ref` e `trigger_id`, não em URLs assinadas.

### Execução

```python
sei_executar_plano_sei(
    page_ref: str,
    trigger_id: str,
    overrides: list[FieldOverride] = [],
    submit_button: SubmitButton | None = None,
    confirmar: bool = False,
    expect: Verification | None = None,
) -> ActionResult
```

### Ferramentas tipadas

Uma ferramenta tipada permanece a camada recomendada:

```python
sei_excluir_documento(
    processo: str,
    documento: str,
    confirmar: bool = False,
) -> dict
```

Internamente, ela pode usar a mesma resolução de plano (`linkExcluirDocumento`), mas deve aplicar confirmação e verificar a ausência do documento na árvore.

## Segurança

- Nunca executar código JavaScript recebido do chamador.
- Nunca permitir URL de destino arbitrária em uma execução nova.
- Nunca expor hashes e tokens de sessão como API deliberada.
- Separar `read`, `write` e `destructive` nas anotações MCP.
- Rejeitar ações cujo plano não possa ser reconstruído de forma idêntica ao reler a página.
- Exigir confirmação explícita para alterações materiais.
- Preferir ferramentas tipadas para exclusão, tramitação, assinatura, cancelamento e outras operações de alto impacto.

## Compatibilidade e migração

### Fase 1 — Base de dados do formulário

1. Alterar o coletor interno para preservar pares repetidos.
2. Incluir metadados de controles e botão no resultado da inspeção.
3. Capturar argumentos literais de callbacks e associá-los ao elemento que os dispara.
4. Redigir hashes e campos ocultos sensíveis no output MCP.

### Fase 2 — Page references e planos

1. Criar armazenamento TTL, por sessão, de `page_ref`.
2. Produzir planos somente para padrões estáticos allowlisted.
3. Classificar URLs como página, descoberta não executável, write ou destructive.
4. Impedir que a inspeção faça GET de ações classificadas como mutantes.

### Fase 3 — Executor e verificação

1. Implementar `sei_executar_plano_sei`.
2. Exigir botão explícito quando ambíguo.
3. Implementar predicados iniciais de verificação.
4. Migrar uma operação real de baixo risco e a exclusão de documento como casos de prova.

### Fase 4 — Consolidação

1. Usar o mecanismo para acelerar wrappers tipados.
2. Marcar `sei_submeter_form` como legado para novas integrações.
3. Remover a dependência pública de `url_destino` após período de compatibilidade.

## Testes de aceitação

1. Uma URL `linkReabrirProcesso` descoberta é classificada como mutante e não é acessada por `sei_inspecionar_pagina`.
2. Um `onclick="acaoExcluir('id', 'descrição')"` gera gatilho com `id` preservado como argumento literal.
3. Callback fora do subconjunto permitido aparece como `unsupported_callback` e não pode ser executado.
4. `<select multiple>` e checkbox groups preservam todos os pares `name=value`, incluindo ordem.
5. Um formulário sem `id` recebe `form_ref` e pode ser submetido por plano.
6. Uma página alterada entre inspeção e execução é recusada como stale.
7. Duas opções de submit exigem botão explícito.
8. Uma resposta 200 sem mudança de estado falha a pós-condição solicitada.
9. URLs externas, redirects externos e actions fora da instância continuam rejeitados.
10. A ferramenta tipada de exclusão só retorna êxito quando o documento não reaparece após releitura da árvore.

## Alternativas consideradas

### Executar callback JavaScript fornecida pelo chamador

Rejeitada. É um interpretador de browser sem as garantias de um browser, amplia muito a superfície de erro e permite que uma entrada de modelo controle a ação autenticada.

### Usar Playwright/Selenium

Rejeitada para o fluxo ordinário. A maior parte dos casos conhecidos consiste em URLs assinadas, formulários e JavaScript estático; um browser adicionaria custo, fragilidade e uma superfície operacional desnecessária.

### Manter URLs assinadas como contrato entre tools

Rejeitada. Elas são efêmeras, podem vazar capacidades de sessão e tornam o agente responsável pela coerência entre inspeção e execução.

### Fazer de `sei_submeter_form` a API definitiva

Rejeitada. Ela é valiosa como diagnóstico, mas a combinação de URL, form id, destino e overrides não carrega intenção, nível de risco, botão escolhido nem pós-condição.

## Decisão

Adotar planos declarativos, referências opacas de página e execução baseada em gatilhos previamente inspecionados. Não aceitar callbacks como input. A ferramenta genérica passa a servir para descobrir e formalizar fluxos simples; ferramentas tipadas continuam sendo a camada de uso comum e de maior segurança.
