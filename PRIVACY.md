# Política de Privacidade — MCP SEI Pro

**Versão:** 1.1
**Vigência:** 24 de outubro de 2026

Esta política descreve como o **MCP SEI Pro** trata dados. O MCP SEI Pro é o servidor MCP (Model Context Protocol) que conecta assistentes de IA, como o Claude, ao SEI (Sistema Eletrônico de Informações). Ele segue a Lei nº 13.709/2018, a Lei Geral de Proteção de Dados Pessoais (LGPD).

## 1. A quem esta política se aplica

O MCP SEI Pro funciona de dois modos:

- **Modo remoto:** é a instância hospedada em `https://mcp.seipro.io`, que o usuário adiciona como conector no Claude (web, desktop, celular ou Claude Code). As seções 3 a 9 tratam principalmente deste modo.
- **Modo local:** o usuário instala o servidor e o executa no próprio computador (ver seção 4).

Esta política **não** cobre:

- a extensão de navegador SEI Pro, que tem [política própria](https://seipro.app/PRIVACY_POLICY.html);
- o SEI do órgão, regido pelas normas do próprio órgão;
- o assistente de IA que o usuário escolhe usar (ex.: Claude, da Anthropic), regido pelos termos e pela política de privacidade do respectivo provedor;
- instâncias que terceiros hospedem a partir deste código-fonte. Quem opera uma instância própria responde por ela.

## 2. Resumo

- O servidor **não tem banco de dados** e não grava em disco credenciais nem conteúdo do SEI.
- A senha do SEI fica **criptografada (AES-256-GCM) dentro do token** de acesso entregue ao assistente. Só o servidor tem a chave para abri-lo.
- O conteúdo do SEI passa pelo servidor **só em trânsito**, quando o usuário pede, e segue para o assistente de IA.
- Não há publicidade, rastreamento, analytics, telemetria nem venda de dados.
- A instância remota roda no **Brasil** (servidor da Hostinger em Campinas, SP).

## 3. Dados tratados no modo remoto

### 3.1 Credenciais do SEI

Ao conectar, o usuário informa na tela de login do servidor:

- a URL do SEI do seu órgão;
- o usuário e a senha do SEI;
- o código do órgão;
- a opção de verificar ou não o certificado SSL.

**Para que servem:** autenticar o usuário na API REST do SEI (módulo mod-wssei), em nome dele, para executar as ações que ele pedir ao assistente.

**Como são guardadas:**

- Ficam criptografadas dentro dos tokens OAuth, e quem guarda os tokens é o cliente (o assistente de IA). Sem a chave do servidor, o conteúdo do token é ilegível, e qualquer alteração o invalida.
- Entre o envio da tela de login e a emissão do token, ficam na memória do servidor por **até 10 minutos**.
- Durante uma sessão ativa, ficam na memória do processo para atender as chamadas daquela sessão.
- **Nunca são gravadas em disco nem em banco de dados.**

### 3.2 Conteúdo do SEI

O servidor lê e escreve no SEI apenas o que as ferramentas acionadas exigem. Isso pode incluir processos, documentos, anexos, andamentos, assinaturas, marcadores, blocos e dados de pessoas que constam nesses registros, como nomes de usuários, de interessados e de unidades.

- **Trânsito:** o conteúdo vai do SEI ao servidor e do servidor ao assistente de IA, sem ser gravado. Arquivos enviados ao SEI (upload) também passam só pela memória.
- **Cache:** listas de apoio (tipos de processo, unidades do usuário, marcadores) ficam na memória por até 1 hora, para evitar consultas repetidas.
- **Documentos restritos e sigilosos:** o servidor não entrega o conteúdo bruto de documento com nível de acesso restrito ou sigiloso sem confirmação explícita do usuário. O mecanismo está descrito na seção [Privacidade e dados restritos](README.md#privacidade-e-dados-restritos) do README.
- **Autoria:** toda ação de escrita (criar, editar, assinar, enviar) fica registrada no próprio SEI em nome do usuário autenticado, como qualquer ação feita pela interface do SEI.

### 3.3 Registros técnicos (logs)

O servidor registra dados técnicos de operação:

- data e hora, método e rota HTTP e código de resposta de cada requisição;
- o endereço IP de origem e o agente de usuário (User-Agent) do cliente;
- eventos de funcionamento, como autenticação bem-sucedida, mudança do modo de transporte e mensagens de erro. Uma mensagem de erro pode incluir um trecho da resposta devolvida pelo SEI.

Os registros **não incluem** senhas, tokens nem o conteúdo das ferramentas acionadas.

### 3.4 Navegador automatizado

Alguns SEIs ficam atrás de um desafio anti-robô (ex.: Cloudflare). Nesses casos, o servidor faz as chamadas por um navegador Chromium sem interface. Esse navegador usa um perfil temporário, e os cookies de sessão dele ficam só na memória e são descartados ao fim do processo.

### 3.5 Registro do aplicativo cliente

Na primeira conexão, o assistente de IA registra-se no servidor pelo protocolo OAuth, informando o nome do aplicativo e os endereços de retorno. Esses dados identificam o aplicativo, não o usuário. Vão criptografados no próprio identificador do cliente e não são gravados.

### 3.6 O que o servidor não faz

- Não usa cookies de rastreamento, analytics, telemetria nem publicidade.
- Não acessa a memória, o histórico de conversas nem os arquivos do usuário no assistente de IA.
- Não vende, aluga nem cede dados a terceiros para qualquer finalidade própria.

## 4. Modo local

No modo local, o servidor roda no computador do usuário:

- As credenciais ficam nas variáveis de ambiente ou na configuração do cliente, na própria máquina.
- O servidor fala direto com o SEI do órgão. Nada passa pela instância `mcp.seipro.io` nem pelos mantenedores do projeto.
- O conteúdo acionado segue para o assistente de IA que o usuário usa, nas condições do respectivo provedor.

## 5. Finalidade e bases legais

A única finalidade do tratamento é permitir que o usuário opere o SEI por meio de um assistente de IA, sob o comando dele.

- **Credenciais e dados de conexão do usuário:** tratados para prestar o serviço que o próprio titular solicita ao conectar-se (art. 7º, V, da LGPD). Os registros técnicos servem também à segurança e à prevenção de abuso (art. 7º, IX).
- **Dados pessoais contidos no SEI:** pertencem ao órgão, que é o controlador desses dados. O usuário acessa o SEI no exercício das suas atribuições e com as permissões que o órgão lhe deu. O MCP SEI Pro só transporta esses dados, sob comando do usuário autenticado, e não os usa para nenhuma outra finalidade.

## 6. Compartilhamento

Os dados só chegam a quem é necessário para o serviço funcionar:

| Destinatário | O quê | Por quê |
|---|---|---|
| SEI do órgão (e a CDN/WAF do órgão, se houver) | Credenciais e requisições | É o sistema que o usuário quer operar |
| Hostinger (servidor no Brasil) | Tráfego e registros técnicos | Hospedagem da instância remota |
| Provedor do assistente de IA (ex.: Anthropic) | Resultados das ferramentas acionadas | Entregar a resposta ao usuário |

O provedor do assistente de IA trata esses resultados conforme os próprios termos e política de privacidade e conforme as configurações da conta do usuário ou da organização dele, inclusive quanto ao uso para treinamento de modelos. Veja, por exemplo, a [política de privacidade da Anthropic](https://www.anthropic.com/legal/privacy).

Os dados também podem ser fornecidos a autoridades, quando houver ordem judicial ou obrigação legal.

## 7. Transferência internacional

No modo remoto, o servidor fica no Brasil, mas o assistente de IA pode processar os dados fora do país. Antes de usar o modo remoto, confira se as normas do seu órgão permitem esse tratamento. Se não permitirem, use o modo local ou hospede uma instância própria em infraestrutura aprovada pelo órgão.

## 8. Retenção

| Dado | Prazo |
|---|---|
| Credenciais aguardando emissão do token | até 10 minutos |
| Token de acesso | 1 hora |
| Token de renovação (substituído a cada uso) | 14 dias |
| Sessão, desde o login com senha | no máximo 30 dias; depois disso é preciso logar de novo |
| Listas de apoio em cache | até 1 hora |
| Estado em memória (sessões, revogações) | até o próximo reinício do servidor |
| Registros técnicos (logs) | rotação por tamanho: os mais antigos são apagados à medida que entram novos, sem cópia de segurança |

## 9. Segurança

- Conexões por HTTPS.
- Tokens criptografados com AES-256-GCM e renovação com rotação: se um token de renovação já usado for reapresentado, a sessão inteira é revogada.
- Retorno do login só para endereços autorizados (claude.ai, claude.com e o próprio computador do usuário).
- A URL do SEI precisa usar HTTPS e apontar para endereço público, para impedir o uso do servidor contra redes internas. A instância `mcp.seipro.io` aceita apenas SEIs em domínios `.gov.br` autorizados.
- No modo remoto, as ferramentas não leem arquivos do disco do servidor.
- Páginas de login sem scripts e protegidas por política de segurança de conteúdo (CSP).
- Código-fonte aberto e auditável em [github.com/SEI-Pro/mcp-seipro](https://github.com/SEI-Pro/mcp-seipro).

## 10. Direitos do titular e como encerrar o uso

O titular tem os direitos do art. 18 da LGPD. Na prática:

- **Encerrar o acesso:** desconecte o conector no assistente de IA. Os tokens deixam de ser renovados e expiram nos prazos da seção 8.
- **Invalidar credenciais já emitidas:** troque a senha do SEI. A senha antiga, guardada nos tokens já emitidos, deixa de autenticar no SEI.
- **Dados guardados:** o servidor não mantém cadastro de usuários nem cópia do conteúdo do SEI. Pedidos sobre registros técnicos podem ser feitos pelo canal da seção 12.
- **Dados que constam no SEI:** pedidos sobre esses dados devem ir ao órgão, que é o controlador deles.

## 11. Alterações desta política

Esta política é versionada no repositório. Cada mudança fica no histórico do arquivo, com a data de vigência atualizada no topo.

## 12. Contato

- **Dúvidas e pedidos:** [issues do repositório](https://github.com/SEI-Pro/mcp-seipro/issues). Não publique senhas nem dados pessoais na issue; se o pedido envolver esse tipo de dado, peça na issue um canal privado.
- **Responsável pela instância `mcp.seipro.io`:** mantenedores do projeto SEI Pro.
