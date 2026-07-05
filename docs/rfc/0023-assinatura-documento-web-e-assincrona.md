# RFC 0023 — `assinar_documento_web` não funciona: fluxo de assinatura parece assíncrono

**Status**: Proposta (investigação parcial, sem correção) · **Data**: 2026-07-04
**Autores**: Claude (com Franklin Baldo)

## 1. Contexto

`SEIWebClient.assinar_documento_web()` (`src/todos/sei_web_client.py`, por volta
da linha 2570) já existia no código, exposta pela tool `sei_assinar_documento`
(`src/todos/tools/assinatura.py`) para instâncias sem mod-wssei. A própria
docstring já avisava: "NÃO IMPLEMENTADO/TESTADO CONTRA A INSTÂNCIA REAL —
escrito por especificação de código... a submissão final (POST com senha)
nunca foi executada nesta sessão".

Nesta sessão, com autorização explícita do usuário e usando a senha já
disponível via keyring (mesmo mecanismo usado para toda autenticação desta
sessão — nunca digitada manualmente), essa submissão foi finalmente executada
ao vivo pela primeira vez, contra `sei.sistemas.ro.gov.br`, processo
`0016.004284/2026-81`, documento `76861508` ("Certidão 128"). **Não funcionou**
— o documento continua sem assinatura após a tentativa, confirmado de duas
formas independentes:

1. `sei_ler_documento` (formato html) mostra o mesmo rodapé de antes
   ("Criado por 76450694220, versão 6 por 76450694220 em ...") — sem
   qualquer bloco "Documento assinado eletronicamente por...".
2. A resposta ao POST não contém nenhum indicador de sucesso nem de erro
   explícito — apenas o mesmo form de assinatura, vazio, de novo.

## 2. O que foi tentado e por quê pareceu razoável

O form (`frmAssinaturas`, ação `documento_assinar`) já vem com valores
padrão corretos pré-selecionados pelo próprio SEI: `selOrgao` = 9 (PGE),
`selCargoFuncao` = "Procurador do Estado", `txtUsuario`/`hdnIdUsuario`
corretos. Os únicos campos vazios são `pwdSenha` e `hdnFormaAutenticacao`.

**Tentativa 1**: `overrides = {"pwdSenha": senha, "btnAssinar": "Assinar"}` —
resultado: form idêntico devolvido, sem sinal de sucesso ou erro.

Ao inspecionar o HTML da página do form mais a fundo, achamos que
`btnAssinar` é `<button type="button" ... onclick="assinarSenha();">` — ou
seja, **não é um botão de submit real**; incluir seu `value` no POST é
irrelevante, porque o clique de verdade dispara a função JS `assinarSenha()`,
que faz:

```js
function assinarSenha(){
    if (infraTrim(document.getElementById('pwdSenha').value)==''){
      alert('Senha não informada.');
      document.getElementById('pwdSenha').focus();
    }else{
      document.getElementById('hdnFormaAutenticacao').value = 'S';
      if (OnSubmitForm()){
        infraExibirAviso();
        document.getElementById('frmAssinaturas').submit();
        return true;
      }
    }
    return false;
}
```

**Tentativa 2**: com esse achado, corrigimos para
`overrides = {"pwdSenha": senha, "hdnFormaAutenticacao": "S"}` — replicando
exatamente o que o JS faz antes do `.submit()` real. **Mesmo resultado**: form
devolvido de novo, sem erro nem sucesso visíveis.

A URL de retorno do POST muda de forma que sugere que ALGO foi processado
(`acao_origem=documento_assinar` e um novo parâmetro `hash_documentos=...`
aparecem, diferente da URL de entrada que tinha
`acao_origem=arvore_visualizar`) — mas o corpo retornado é estruturalmente
idêntico ao form vazio original (mesmos campos, mesmo HTML, sem nenhum aviso
de erro em `alert()`/div de aviso/mensagem de "senha inválida").

## 3. Hipótese: fluxo assíncrono com polling, não POST síncrono

O script da página do form declara, no topo:

```js
var objVerificacaoCertificado = null;
var intervaloVerificacao = null;
var bolAssinandoSenha = false;
var timer = null;
```

E define uma função `finalizar()` **vazia**, só com um comentário:

```js
function finalizar(){
  //se realizou assinatura
}
```

Isso sugere fortemente que o fluxo real de assinatura no SEI **não é um POST
único e síncrono**: o clique real provavelmente inicia um processamento
server-side (geração da assinatura, hash, possivelmente envio a um serviço de
certificação) e o JS do lado do cliente faz **polling** (via
`intervaloVerificacao`/`timer`, provavelmente com `setInterval` em algum lugar
não capturado neste trecho) até que o servidor sinalize conclusão — só então
`finalizar()` seria de fato chamada com lógica adicional que não está visível
no trecho investigado (talvez injetada dinamicamente, ou dependente de uma
resposta AJAX que nunca foi disparada porque esta sessão só fez uma requisição
POST simples, sem repetir a etapa de polling).

**Isso não foi confirmado — é uma hipótese baseada nas pistas acima.** Uma
possibilidade alternativa: o POST pode estar **de fato correto**, mas o SEI
exige algum outro campo/cabeçalho não capturado aqui (ex.: um token
anti-CSRF renovado a cada carregamento do form, que nossa reutilização do
form original já capturado — via `_post_form_preservando` — pode ter deixado
desatualizado se o servidor invalida por timing).

## 4. Próximos passos para quem for corrigir

1. **Observar a rede real do navegador ao clicar "Assinar" manualmente** (SEI
   sistemas.ro.gov.br, com Franklin operando) — usando as devtools do
   navegador (Network tab), capturar exatamente quais requisições HTTP
   acontecem entre o clique e a confirmação visual de sucesso. Isso revela
   se há de fato uma segunda requisição de polling, e para qual endpoint.

2. **Não reutilizar `_post_form_preservando` sem verificar se o form exige
   um token de sessão de curta duração** — comparar o HTML do form recém-
   carregado com o que foi de fato enviado, campo a campo, garantindo que
   nenhum campo dinâmico (nonce, timestamp) ficou desatualizado entre o GET
   do form e o POST.

3. **Marcar `assinar_documento_web`/`sei_assinar_documento` (backend web)
   como conhecidamente quebrado** até que o mecanismo real seja mapeado —
   atualizar a docstring da tool para deixar isso explícito (hoje ela diz
   apenas "nunca foi executada", o que já não é mais verdade e pode levar
   outro agente a confiar demais no código existente).

4. **Considerar não perseguir isso via scraping** se o mecanismo real for
   complexo/frágil (ex.: depende de certificado digital, biometria, ou outro
   passo que não é replicável via HTTP puro) — pode ser um caso em que a
   assinatura via navegador real (Playwright, mesmo padrão da RFC 0021) seja
   mais robusta que tentar replicar o POST cru.

## 5. O que NÃO foi feito (deliberadamente)

Depois da segunda tentativa sem sucesso, as tentativas ao vivo foram
interrompidas propositalmente — continuar via tentativa e erro num documento
jurídico real, em produção, não é prudente. O documento (Certidão 128,
0016.004284/2026-81) permanece **sem assinatura**, íntegro, pronto para ser
assinado manualmente pela interface web do SEI enquanto este problema não é
resolvido.
