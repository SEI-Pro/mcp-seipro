# RFC 0023 — `assinar_documento_web`: POST por senha falhou, causa ainda não isolada

**Status**: Proposta (investigação parcial, sem correção) · **Data**: 2026-07-04
**Autores**: Claude (com Franklin Baldo)

> Revisão de 2026-07-05: título e conclusão corrigidos após review — a
> hipótese original de fluxo assíncrono/polling estava errada (ver §3).
> Identificadores de produção (processo, documento, usuário) foram
> substituídos por placeholders — não são necessários para registrar o bug.

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
ao vivo pela primeira vez, contra uma instância real, um processo `<processo>`,
documento `<id_documento>` (uma Certidão). **Não funcionou** — o documento
continua sem assinatura após a tentativa, confirmado de duas formas
independentes:

1. `sei_ler_documento` (formato html) mostra o mesmo rodapé de antes
   ("Criado por `<usuario>`, versão N por `<usuario>` em ...") — sem
   qualquer bloco "Documento assinado eletronicamente por...".
2. A resposta ao POST não contém nenhum indicador de sucesso nem de erro
   explícito — apenas o mesmo form de assinatura, vazio, de novo.

## 2. O que foi tentado

O form (`frmAssinaturas`, ação `documento_assinar`) já vem com valores
padrão corretos pré-selecionados pelo próprio SEI: `selOrgao`, `selCargoFuncao`
("Procurador do Estado" no caso testado), `txtUsuario`/`hdnIdUsuario`
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

**Tentativa 2** (ad-hoc, fora do código do repositório — ver §4 para o que o
código atual realmente envia): com esse achado, um script de teste corrigiu
para `overrides = {"pwdSenha": senha, "hdnFormaAutenticacao": "S"}` —
replicando o que o JS faz antes do `.submit()` real. **Mesmo resultado**: form
devolvido de novo, sem erro nem sucesso visíveis.

A URL de retorno do POST muda de forma que sugere que ALGO foi processado
(`acao_origem=documento_assinar` e um novo parâmetro `hash_documentos=...`
aparecem, diferente da URL de entrada que tinha
`acao_origem=arvore_visualizar`) — mas o corpo retornado é estruturalmente
idêntico ao form vazio original (mesmos campos, mesmo HTML, sem nenhum aviso
de erro em `alert()`/div de aviso/mensagem de "senha inválida").

**A causa exata da falha da Tentativa 2 não foi isolada.** A hipótese mais
provável é uma diferença pontual entre o payload de fato enviado e o que um
navegador real enviaria (campo faltando, nome de campo errado, encoding),
não um mecanismo assíncrono — ver correção abaixo.

## 3. Hipótese de fluxo assíncrono — descartada em review

A versão original desta RFC especulava que o fluxo de assinatura fosse
assíncrono (polling via `intervaloVerificacao`/`timer` até uma função
`finalizar()` ser chamada), com base nestas variáveis existirem no mesmo
`<script>` da página do form. **Isso está incorreto**, apontado em review:

- `assinarSenha()` — o caminho de autenticação por **senha** — seta
  `hdnFormaAutenticacao = "S"` e chama `frmAssinaturas.submit()`
  **imediatamente**, de forma síncrona. Não há polling nesse ramo.
- `objVerificacaoCertificado`, `intervaloVerificacao`, `timer` e
  `finalizar()` são artefatos do caminho **alternativo** de autenticação por
  **certificado digital** — compartilhado no mesmo script da página, mas é
  um ramo de código diferente, não acionado pela submissão por senha.

Ou seja: o POST por senha é, de fato, uma submissão de formulário única e
síncrona, como o código original assumia. A falha da Tentativa 2 não se
explica por assincronia — a causa raiz permanece não identificada.

## 4. Dois defeitos objetivos identificados em review (independentes da hipótese acima)

1. **`assinar_documento_web()`, no código atual do repositório, ainda envia
   `btnAssinar` mas NÃO envia `hdnFormaAutenticacao="S"`** — ou seja, o
   código que está no repositório não reproduz `assinarSenha()` mesmo depois
   desta investigação. Isso precisa ser corrigido primeiro, antes de
   qualquer outro diagnóstico.
2. **Quando `orgao` é informado, o método sobrescreve `selOrgaoAssinante`,
   mas o form inspecionado usa `selOrgao`.** Esse override é silenciosamente
   ignorado pelo servidor (nome de campo errado — não gera erro, só não tem
   efeito).

## 5. Próximos passos para quem for corrigir

1. **Corrigir os dois defeitos do §4 primeiro**: montar o POST com
   `pwdSenha`, `hdnFormaAutenticacao="S"`, `selOrgao` (não
   `selOrgaoAssinante`) e `selCargoFuncao` — preservando os defaults do form
   quando não houver override explícito — e só then reavaliar se ainda falha.

2. **Comparar o payload efetivamente enviado com o payload de um navegador
   real** (DevTools → Network, durante uma assinatura manual) campo a campo,
   incluindo nomes, encoding e quaisquer tokens dinâmicos (nonce/timestamp)
   que possam ter ficado desatualizados entre o GET do form e o POST via
   `_post_form_preservando`.

3. **Marcar `assinar_documento_web`/`sei_assinar_documento` (backend web)
   como conhecidamente quebrado** até que a causa real seja corrigida e
   validada ao vivo — atualizar a docstring da tool para deixar isso
   explícito (hoje ela diz apenas "nunca foi executada", o que já não é mais
   verdade e pode levar outro agente a confiar demais no código existente).

## 6. O que NÃO foi feito (deliberadamente)

Depois da segunda tentativa sem sucesso, as tentativas ao vivo foram
interrompidas propositalmente — continuar via tentativa e erro num documento
jurídico real, em produção, não é prudente. O documento testado permanece
**sem assinatura**, íntegro, pronto para ser assinado manualmente pela
interface web do SEI enquanto este problema não é resolvido.
