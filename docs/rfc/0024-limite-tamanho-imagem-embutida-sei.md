# RFC 0024 — Limite (não documentado) de tamanho para imagem embutida em seção SEI

**Status**: Implementada · **Data**: 2026-07-05
**Autores**: Claude (com Franklin Baldo)

## 1. Contexto

Ao montar uma Certidão com um print (screenshot) embutido diretamente no HTML
via `<img src="data:image/jpeg;base64,...">`, uma primeira tentativa com uma
imagem de ~104KB em base64 resultou em **perda silenciosa**: o parágrafo
inteiro que continha a imagem desapareceu do conteúdo persistido —
confirmado por reconferência independente via `listar_secoes_web` (não
bastou confiar no `{"status": "ok"}` da resposta do `alterar_secoes_web`).

Uma imagem minúscula (68 bytes base64, 1×1 px) embutida da mesma forma
**sobreviveu** — provando que não é um bloqueio geral a `data:` URIs, é um
limite de **tamanho**.

## 2. O que foi confirmado

- **Funciona**: ~26.104 chars base64 (19.576 bytes JPEG, após `resize(0.6x)`
  + `quality=45`).
- **Falha silenciosamente**: ~104KB chars base64 (parágrafo inteiro removido,
  sem erro).
- O limite exato entre esses dois valores **não foi isolado** — não valia a
  pena gastar mais tentativas ao vivo num documento real só para encontrar o
  número exato.
- Rastreado como comportamento do **próprio SEI** (não do `todos`):
  `sanitize_iso8859()` só faz entity-encode de caracteres fora do
  ISO-8859-1, não mexe em base64 ASCII válido; não há nenhum outro passo de
  filtragem de conteúdo entre a montagem do form e o POST em
  `alterar_secoes_web`.

## 3. O que foi implementado

`todos.image_utils.comprimir_para_embed()` (e o atalho
`montar_data_uri()`) — comprime progressivamente (escala + qualidade JPEG,
da mais leve pra mais agressiva) até caber num limite configurável
(`DEFAULT_MAX_BASE64_CHARS = 30_000`, com boa margem abaixo da faixa
confirmada), levantando `SEIValidationError` com mensagem clara se nem a
tentativa mais agressiva couber.

Requer Pillow, disponível via o extra opcional `llm` (mesmo guard já usado
em `tools/analise.py` para pymupdf/Pillow — RFC 0019 §2.6).

## 4. Como usar (fluxo de assinatura + embed de imagem, ponta a ponta)

1. Gerar o screenshot (ex.: `pink`'s `kanoe_screenshot`/`expediente_screenshot`,
   RFC equivalente no `pink`) ou qualquer PNG/JPEG local.
2. `data_uri = montar_data_uri(caminho_da_imagem)`.
3. Montar o HTML da seção com `<img src="{data_uri}" .../>` embutido no meio
   do texto certificado.
4. `alterar_secoes_web(protocolo, id_documento, secoes)` — qualquer edição
   de seção também **derruba automaticamente uma assinatura existente** (é
   assim que `sei_cancelar_assinatura` funciona — não há endpoint dedicado
   de "cancelar assinatura" no SEI, só uma edição mínima que força a
   remoção). Portanto: se o documento já estiver assinado e precisar de
   correção, edite normalmente — a assinatura cai sozinha; não tente achar
   um botão de "revogar assinatura" separado.
5. **Reconfira sempre via nova leitura** (`listar_secoes_web` ou
   `sei_ler_documento`) antes de considerar a edição bem-sucedida — o SEI
   não sinaliza erro quando descarta conteúdo grande demais.
6. Só então assinar via `assinar_documento_web()` (ver RFC 0023 para o
   histórico da correção desse método — precisa enviar `pwdSenha` +
   `hdnFormaAutenticacao="S"`, replicando exatamente o que o JS real
   `assinarSenha()` faz).
7. **Reconfira a assinatura de novo** via releitura do documento — o rodapé
   deve mostrar "Documento assinado eletronicamente por..." com código
   verificador e CRC. Sem isso, não presuma sucesso mesmo que a chamada não
   tenha levantado erro.

## 5. Não-objetivos

Não foi determinado o limite exato de tamanho aceito pelo SEI — apenas uma
faixa seguindo o método de tentativa; `DEFAULT_MAX_BASE64_CHARS` foi
escolhido com margem, não como o valor máximo real permitido.
