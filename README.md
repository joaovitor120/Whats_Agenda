# Whats Agenda

Assistente de agenda em português pelo WhatsApp, com FastAPI, PostgreSQL, Celery/Redis, Evolution API, Claude/OpenAI e calendários Google/Microsoft.

**Para conectar suas contas e usar integrações reais, siga o [passo a passo em setup.md](setup.md).**

**Estado:** revisão de segurança implementada. O relatório anterior registrou 64 testes aprovados; ele antecede as funcionalidades recentes de digitação, prévia da agenda, Claude e transcrição local. As integrações recentes ainda precisam de validação de ponta a ponta com mensagens reais. Consulte [SECURITY.md](SECURITY.md) para evidências e limitações.

## Fluxo

1. Evolution envia mensagem autenticada para `POST /webhooks/evolution`.
2. A API verifica cabeçalho, instância, timestamp, tamanho, identidade e quotas; grava uma entrada única.
3. Após persistir a mensagem, a API solicita o processamento em segundo plano. Beat recupera pendências a cada 30 segundos. O worker serializa cada conversa.
4. O interpretador produz dados restritos; o serviço de eventos valida dono, calendário, alvo e datas.
5. Criações, alterações, lembretes personalizados e exclusões passam por uma prévia e confirmação (`SIM` ou o código exibido), válida por cinco minutos. Correções invalidam a prévia anterior e exigem nova confirmação. Séries são identificadas explicitamente.
6. Antes de alterar um calendário externo, o plano exato é persistido. Novas tentativas reutilizam a operação.
7. Respostas e lembretes passam pela outbox; agendamentos permanecem no banco.

Não há ferramentas, shell, SQL, URLs ou destinatários controlados pelo LLM. A listagem de agenda é renderizada em Python. O calendário fake usa os eventos locais de cada usuário, sem um cache global compartilhado.

## Instalação local

Python 3.14 é a versão usada para gerar os lockfiles e realizar a revisão.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install -e . --no-deps --no-build-isolation
```

Os lockfiles incluem versões e hashes. O Docker instala somente `requirements.lock`, sem ferramentas de desenvolvimento.

Se ainda não houver configuração local:

```powershell
Copy-Item .env.example .env
```

Preserve seu `.env` existente. Nunca publique esse arquivo. Gere chaves independentes:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

O primeiro comando serve para cada segredo de aplicação, webhook, administração e banco. O segundo gera `TOKEN_ENCRYPTION_KEY`. Armazene a chave de criptografia separadamente dos backups; alterá-la sem recriptografar os tokens impede recuperar as conexões.

## Docker: desenvolvimento

Preencha `POSTGRES_ADMIN_PASSWORD` com uma senha diferente de `POSTGRES_PASSWORD`. Configure também `EVOLUTION_API_KEY`, `EVOLUTION_DB_PASSWORD` e `EVOLUTION_DB_ADMIN_PASSWORD` com segredos independentes; use caracteres seguros para URL nas senhas de banco (o comando `token_urlsafe` acima atende). O bootstrap cria os usuários das aplicações sem privilégios de superusuário.

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

A migração roda no serviço `migrate`, antes da API e dos workers. A API publica somente `127.0.0.1:8000`; PostgreSQL e Redis não publicam portas. Os processos da aplicação usam usuário sem root, filesystem somente para leitura e limites de recursos.

- Documentação local: `http://localhost:8000/docs`.
- Liveness: `/health/live`; readiness: `/health/ready`.
- Parar mantendo os dados: `docker compose down`.
- Evite `docker compose config` sem `--quiet`: a saída pode conter segredos.

### Container da Evolution

O serviço `evolution` agora faz parte do Compose padrão. No Docker Desktop, procure o projeto `whats_agenda` e o container `whats_agenda-evolution-1`.

```powershell
docker compose up -d --build evolution
docker compose ps evolution evolution-postgres evolution-redis
```

A API fica em `http://localhost:8080` e o painel em `http://localhost:8080/manager`, acessíveis somente nesta máquina. No painel, use a URL local e a chave `EVOLUTION_API_KEY` do seu `.env`; não publique essa chave.

A imagem oficial 2.3.7 é fixada por digest; a derivação local executa como `node`, sem root, e evita o wrapper que imprime a URL do banco. Uma correção pontual de CORS permite chamadas sem `Origin` (servidor/healthcheck), mantendo a lista de origens autorizadas do navegador. O build falha se essa correção não corresponder à imagem. Banco, Redis e volumes da Evolution são separados dos da agenda. As portas dos bancos e do Redis não são publicadas.

Para comunicação entre os containers:

```dotenv
EVOLUTION_API_URL=http://evolution:8080
EVOLUTION_WEBHOOK_BASE_URL=http://api:8000
```

`PUBLIC_BASE_URL` continua sendo a URL usada pelos callbacks OAuth no navegador. Em produção, use uma URL HTTPS acessível à Evolution também em `EVOLUTION_WEBHOOK_BASE_URL`, pois a API exige HTTPS nesse modo.

O servidor pode estar saudável sem WhatsApp pareado. Para habilitar a integração, configure `FAKE_EVOLUTION=false`, recrie API/worker/Beat e use o setup administrativo para obter o QR. Os modos fake de LLM/calendário continuam controlados separadamente. Nenhum número é pareado automaticamente.

**Volumes antigos:** o script de criação de roles só executa em banco novo. Não apague um volume para corrigir permissões. Em instalações anteriores, faça backup e ajuste as roles com um administrador PostgreSQL. A migração `0002_security` invalida OAuth antigo e coloca mensagens pendentes recebidas pelo webhook antigo em revisão.

## Evolution e webhook

A versão implantada da Evolution deve suportar cabeçalhos personalizados no webhook e busca da mídia persistida por chave.

```http
POST /webhooks/evolution
Authorization: Bearer <EVOLUTION_WEBHOOK_SECRET>
Content-Type: application/json
```

O segredo **não faz mais parte da URL**. Reconfigure webhooks existentes. O corpo precisa incluir:

- `instance` exatamente igual a `EVOLUTION_INSTANCE_NAME`;
- `event: "messages.upsert"`;
- `data.key.id` e `data.key.remoteJid`, no formato de contato individual `5511999999999@s.whatsapp.net`;
- `data.messageTimestamp` em segundos Unix, até 5 minutos atrás;
- `data.message.conversation` ou `audioMessage` válido.

Histórico antigo/offline, grupos, status, mensagens próprias e identidades `@lid` sem telefone confiável não geram ações. Ajuste a janela conscientemente se sua infraestrutura tiver atrasos maiores.

Contatos `@lid` são aceitos quando a Evolution fornece `data.key.remoteJidAlt` com telefone individual válido (`@s.whatsapp.net`) no webhook autenticado. O JID original é preservado para localizar mídia; o identificador LID nunca é convertido em telefone.

Para configurar a instância, use `POST /admin/evolution/setup` com `X-Admin-Api-Key`, a partir de uma rede em `ADMIN_ALLOWED_NETWORKS`. O QR é retornado apenas nessa resposta, com cache desabilitado; não o publique.

Em Docker, a origem vista pela API pode ser o gateway da rede, não `127.0.0.1`. Identifique a origem da sua instalação e autorize somente o IP/rede administrativa necessária. Não use `0.0.0.0/0` nem confie em `X-Forwarded-For` enviado pelo cliente.

A Evolution deve ficar em rede privada. O Compose fornece a instância local; atualização da imagem, pareamento e operação de produção exigem acompanhamento. Para usar um servidor externo, ajuste `EVOLUTION_API_URL` e inicie apenas os serviços da agenda.

## Conectar um calendário

O usuário precisa primeiro mandar uma mensagem pelo WhatsApp. O administrador inicia o consentimento:

```http
GET /oauth/google/start?phone=5511999999999
X-Admin-Api-Key: <ADMIN_API_KEY>
```

Use `microsoft` para Microsoft. Abra a `authorization_url` retornada.

- Cadastre exatamente `PUBLIC_BASE_URL/oauth/{provider}/callback` no provedor.
- Google: escopo de eventos e identidade `openid email`, usado para mostrar a conta a confirmar.
- Microsoft: `Calendars.ReadWrite`, `offline_access` e `User.Read`, usado para identificar a conta.
- OAuth usa state de uso único, prazo de 10 minutos e PKCE S256.
- Após o callback, o WhatsApp recebe a identidade mascarada e `CONECTAR <código>`.
- A conexão só é ativada quando o mesmo usuário confirma em até 10 minutos.
- Há somente um calendário ativo por usuário. Uma reconexão nunca herda os IDs de eventos de outra conta.

Desconexão administrativa: `POST /admin/connections/{connection_id}/disconnect?user_id={user_id}`. Remove credenciais locais, desativa a conexão e cancela lembretes locais associados. Tenta revogar no Google. Se `remote_revocation_required=true`, o usuário também deve remover o consentimento no portal do provedor; o escopo Microsoft usado aqui não permite revogar todas as sessões da conta.

## Modos e limites

`FAKE_LLM`, `FAKE_CALENDAR` e `FAKE_EVOLUTION` são explícitos. Desligue os três para integrações reais. Falta de chave do provedor selecionado impede a inicialização com LLM real; não ativa o interpretador fake silenciosamente.

### Claude econômico

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sua-chave
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
LLM_MAX_OUTPUT_TOKENS=2048
FAKE_LLM=false
```

O Claude recebe somente instruções curtas, data/fuso e a mensagem atual. Não recebe o histórico nem a lista da agenda. O pensamento estendido fica desativado; o JSON é validado localmente. Saudações e a prévia de compromissos não consomem chamadas de LLM. Erros da API geram resposta amigável, sem repetir automaticamente a chamada paga. Quotas diárias continuam ativas. A chave Anthropic é independente de `OPENAI_API_KEY`.

Referências: [modelos e preços da Anthropic](https://platform.claude.com/docs/en/models/overview) e [saídas estruturadas](https://platform.claude.com/docs/en/build-with-claude/structured-outputs). Haiku 4.5 é a opção econômica da linha atual consultada em 27/09/2026; confira disponibilidade antes de futuras implantações.

### Ativar ou desativar áudio

`AUDIO_ENABLED=false` é o padrão. Áudios válidos recebidos geram a resposta fixa abaixo, sem download, transcrição ou interpretação pelo LLM, e preservam a conversa pendente:

> No momento, estou atendendo apenas por texto. Por favor, envie sua solicitação em uma mensagem de texto para que eu possa ajudar. 😊

Para áudio local, configure:

```dotenv
AUDIO_ENABLED=true
AUDIO_TRANSCRIPTION_PROVIDER=local
LOCAL_TRANSCRIPTION_URL=http://transcription:8001
LOCAL_TRANSCRIPTION_API_KEY=segredo-aleatorio-exclusivo-com-32-ou-mais-caracteres
COMPOSE_PROFILES=audio
```

Execute `docker compose up -d --build transcription api worker beat`. O container `transcription` usa faster-whisper **small multilíngue, CPU int8**, com português fixo e uma transcrição por vez. O modelo é baixado no build, com revisão fixa. Em execução, o serviço fica em rede interna sem Internet e sem porta publicada, com autenticação e limites de memória/CPU. Áudio e transcrição ficam em memória no transcritor; a aplicação persiste o texto conforme sua política de retenção.

O fluxo é **áudio → Whisper local → texto → Claude**. Claude não recebe o arquivo de áudio. A transcrição não tem cobrança de API, mas usa os recursos do computador; o modelo small pode errar nomes, datas e horários. O serviço rejeita repetições excessivas e segmentos de baixa confiança; usa até 2 GB de RAM e 2 CPUs. Falhas pedem que o usuário envie texto. A transcrição OpenAI continua opcional com `AUDIO_TRANSCRIPTION_PROVIDER=openai` e `OPENAI_API_KEY` válida.

Operações cujo plano já foi persistido seguem a recuperação existente para evitar inconsistência com o calendário externo.

Padrões:

| Controle | Limite |
| --- | --- |
| Webhook | 256 KB, até 20 mensagens por requisição |
| Texto | 4.000 caracteres |
| Mensagens por telefone | 20/minuto e 200/dia |
| Interpretação | 100/usuário/dia e 5.000/instância/dia |
| Áudio | 20 MB, duração real até 600 s |
| Orçamento de áudio | 1.800 s/usuário/dia; 36.000 s/instância/dia |
| Saída do modelo | 2.048 tokens por interpretação configurados; teto ajustável até 4.096 |
| Tentativas de entrada | até 5 |
| Confirmação de mutação | 5 minutos, uso único |
| Conversa incompleta / OAuth | 10 minutos |
| Retenção de conteúdo de mensagens | 30 dias |
| Atraso de lembrete | antecipados: até 30 minutos, limitados ao início; alerta AGORA: até 2 minutos |

Áudio reserva a duração informada pela Evolution, arredondada para cima. Depois da validação dos bytes, cobra qualquer diferença para a duração real antes de transcrever. Falhas ainda consomem a reserva, mas áudios curtos não gastam automaticamente 600 segundos. Ao atingir a cota, o bot pede texto sem repetir a tentativa. Redis indisponível bloqueia novas chamadas sujeitas a quota.

Eventos aceitam datas futuras até dois anos, duração máxima de sete dias e recorrência diária ou semanal em um ou vários dias. Os lembretes são materializados para 365 dias e renovados diariamente, sem terminar a série ao completar esse horizonte. As políticas estão em `app/reminders/policies.py`.

### Confirmação, correções e vários compromissos

- O acesso é restrito aos telefones de `WHATSAPP_ALLOWED_CONTACTS` no `.env`, em JSON `{"telefone internacional":"Nome"}`. Lista ausente ou `{}` bloqueia todos. Use `55` + DDD + número, sem pontuação. Contatos externos à lista são ignorados antes de transcrição/LLM; o worker revalida mensagens antigas e o cliente Evolution bloqueia respostas, presença e lembretes a destinatários não autorizados. Veja em `setup.md` como editar a lista e recriar os serviços para aplicar a mudança.

- Datas relativas usam o fuso do usuário: “hoje”, “amanhã”, “depois de amanhã” (com ou sem acento), “daqui a três dias” e dias da próxima semana. O Claude recebe as datas locais de referência a cada mensagem. Um horário de hoje que já passou não é transferido silenciosamente para amanhã.
- “Neste domingo” inclui hoje quando a mensagem chega num domingo. Para um pedido individual sem recorrência, o código confere essa referência após a interpretação do modelo. Horários já passados exigem correção, sem avançar uma semana. Ao informar somente “será às 18 horas”, o horário é guardado no rascunho em `event_time`, e o bot pergunta o dia antes de apresentar a confirmação.
- “Corrigir o agendamento para…” pode retomar o último evento salvo pelo mesmo usuário nos dez minutos anteriores. A nova prévia identifica o evento e exige outra confirmação. Essa referência não seleciona itens de um lote nem eventos de outra pessoa; sem uma referência recente e única, o bot pede o nome. Eventos já cadastrados não são corrigidos retroativamente por uma atualização de código.
- Se o dia for incerto, o bot pergunta educadamente se a data provável está correta, mostrando dia/mês/ano e dia da semana. Sem uma data identificável, pede o dia sem inventar uma sugestão. `SIM` nessa etapa confirma somente o dia; depois vêm o horário que faltar e a prévia completa para confirmar o agendamento. Em lotes, cada data incerta é esclarecida separadamente, preservando os demais itens.

- Nada é adicionado antes da confirmação. A prévia mostra nome, primeira data, horário, dias recorrentes e antecedência dos lembretes. Responda `SIM` ou `CONFIRMAR <código>` em cinco minutos.
- Corrija em linguagem natural, por exemplo “Na verdade a academia será às 19h”. O bot atualiza o rascunho e envia uma nova prévia. O código anterior deixa de valer. Se a interpretação falhar, a proposta antiga continua invalidada; será necessário completar/revisar o rascunho.
- É possível pedir até dez compromissos de uma vez: “Academia toda segunda, terça e sexta às 18:50; inglês todos os dias às 20h”. A confirmação abrange a lista inteira. Se faltar informação de algum item, o bot guarda todos e pergunta o que falta antes de salvar.
- Uma série com vários dias é um único evento: `FREQ=WEEKLY;BYDAY=MO,TU,FR`. A próxima ocorrência futura inicia a série, preservando o horário local.
- Cada item do lote tem identificadores e registro de recuperação próprios. Uma falha externa depois de salvar parte do lote pode exigir revisão administrativa; não há transação atômica entre calendários externos. Itens já concluídos não são recriados na retomada.
- Para escolher exatamente os avisos de um compromisso: “Me lembre da academia apenas 12 horas, 1 hora e 15 minutos antes”. A prévia mostra **somente 12h, 1h e 15min antes**; após `SIM`, os avisos de 5 minutos e do início são removidos desse evento. A preferência vale para cada ocorrência da série; outros eventos não mudam. “Apenas 30 minutos antes” gera um único aviso. Para incluir o início explicitamente, diga “somente 5 minutos antes e na hora”. Sem “apenas/somente/só”, o pedido personalizado mantém os avisos padrão de 5 minutos e início, sempre apresentados na prévia. “Volte aos lembretes padrão da academia” restaura a política da categoria.
- A lista é guardada em `events.reminder_offsets_minutes`, e `events.reminders_exclusive` indica exclusividade. `reminder_minutes` continua compatível com preferências antigas. São permitidos até dez horários distintos, de zero (início) a sete dias antes. Duplicatas são removidas; avisos já passados não são reenviados. Corrigir uma data ou renomear mantém a preferência, salvo pedido explícito para mudá-la.
- O teto de saída da LLM foi ampliado para comportar a lista de eventos. O modelo continua Haiku, sem pensamento estendido; não existe uma chamada por ocorrência nem por lembrete. Somente o rascunho necessário é enviado como contexto de uma correção.
- O schema enviado ao Claude exige todos os campos, usando `null`, `false` e listas vazias onde não houver dados. Isso reduz a complexidade de compilação causada por campos opcionais duplicados no pedido e nos itens de um lote; a validação local continua obrigatória. A chamada tem limite total de 40 segundos e espera HTTP de 35 segundos. Falhas registram tipo, status HTTP quando disponível e duração, sem registrar mensagem, transcrição ou chave. Referência: [limites de complexidade do Claude](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#schema-complexity-limits).

### Rotinas diárias

- Envie “Quero estudar inglês todos os dias às 19h”. Sem uma data inicial explícita, a primeira ocorrência será hoje se o horário ainda não passou, ou amanhã caso contrário, no fuso do usuário.
- Se enviar apenas “Estudar inglês todos os dias”, o bot pergunta o horário e mantém a recorrência enquanto aguarda a resposta por até dez minutos.
- Por padrão, cada ocorrência diária recebe lembretes 15 minutos antes, 5 minutos antes e no início. Antecedências personalizadas substituem a de 15 minutos; quando exclusivas, substituem também os avisos de 5 minutos e início. Ao criar o evento, são agendados somente os horários de lembrete que ainda não passaram.
- O bot confirma a frequência diária na resposta e inclui as próximas ocorrências nas consultas à agenda. Renomeações mantêm a série; cancelar o evento cancela toda a série e seus lembretes pendentes, após confirmação.
- Lembretes enviados na mesma data local da ocorrência mostram “HOJE às HH:MM”; nos demais dias, mostram a data. O texto é atualizado ao preparar o envio, inclusive se ficou na fila durante a virada do dia.
- A frequência diária usa `FREQ=DAILY` no banco/Google e `daily` com intervalo 1 no Microsoft Graph. Referências: [Google Calendar](https://developers.google.com/workspace/calendar/api/concepts/events-calendars) e [Microsoft Graph](https://learn.microsoft.com/en-us/graph/api/resources/recurrencepattern?view=graph-rest-1.0).

### Alertas próximos ao compromisso

- Por padrão, os eventos têm alertas de 5 minutos antes e do início, somados à política da categoria ou às antecedências personalizadas, sem duplicatas. Uma escolha exclusiva (“apenas/somente/só”) usa exatamente os horários pedidos e desativa os extras naquele evento.
- Lembretes enviados faltando menos de uma hora começam com 🚨. No início, a mensagem é: `🚨 Atenção! Você tem o compromisso “Academia” marcado para AGORA! ⏰`.
- O agendador consulta os lembretes a cada 30 segundos e encaminha imediatamente as novas mensagens para o worker. A fila, o intervalo de envio e a conexão podem acrescentar atraso; não há garantia de entrega no segundo exato. O alerta do início aceita até 2 minutos de atraso (ou a tolerância configurada, se menor). Alertas antecipados expiram ao começar o evento.
- A entrega depende de Docker, worker, Beat e Evolution ativos. A atualização dos eventos já cadastrados pode ser aplicada com `docker compose exec -T worker python -c "import asyncio; from app.workers.reminders import _refresh_recurring; asyncio.run(_refresh_recurring(include_single=True))"`. O comando acrescenta os alertas futuros faltantes sem duplicar os existentes.

## Recuperação e operação

- Saudações e conversa social têm resposta amigável, também em áudio. “Oi” e “bom dia” recebem uma saudação simples; “Tudo certo por aqui! E com você?” aparece somente quando a mensagem pergunta como o bot está, como “tudo bem?” ou “como você está?”. Frases sociais comuns são tratadas localmente; outras podem ser classificadas pelo Claude como `small_talk`. Uma saudação não cancela uma operação pendente. Pedidos mistos, como “bom dia, marque uma reunião”, continuam no fluxo da agenda.
- Para trocar apenas o título: “Mude o nome do evento Reunião de equipe para Planejamento semanal”. O bot pede confirmação e preserva datas, horários, recorrência e lembretes. Apenas o campo de título é atualizado no provedor.
- Frases diretas de renomeação com nome atual e novo nome são extraídas localmente; as demais passam pelo Claude com contexto específico. Durante uma criação ainda pendente, mudar o nome corrige o rascunho. Campos ausentes ficam pendentes por dez minutos. `CANCELAR` descarta a solicitação pendente. A alteração continua exigindo confirmação.
- A busca de títulos ignora acentos e diferenças de caixa, sempre dentro dos eventos do remetente. Nomes repetidos geram uma lista de até cinco opções com data e horário; a escolha fica pendente por dez minutos e ainda exige a confirmação da alteração. A prévia da agenda usa marcadores sem números para não confundir com essa seleção.
- Respostas que não criam um compromisso incluem uma lista amigável com até cinco próximos compromissos, em ordem cronológica e no fuso do usuário. A consulta considera ocorrências futuras nos próximos dois anos, inclusive recorrências, e exclui cancelados. Falta de conexão e falhas de consulta recebem avisos próprios. No modo `FAKE_CALENDAR=true`, a lista usa os eventos locais de demonstração; o calendário real exige a integração configurada.
- O bot sinaliza “digitando…” durante o processamento e tenta enviar a resposta assim que ela é persistida. A presença é renovada em pulsos curtos e encerrada ao concluir ou falhar; falhas de presença não impedem a resposta. O envio inclui três segundos de digitação para tornar visíveis também respostas rápidas, como “Oi”. O indicador depende da conexão e da exibição do WhatsApp; mensagens aguardando um worker livre ainda não têm presença ativa.
- `GET /admin/operations` lista IDs de entradas com falha/revisão e saídas incertas.
- Operações de calendário têm plano persistido e ID estável. Google usa ID definido pelo cliente; Microsoft usa `transactionId` e propriedade de reconciliação.
- Uma criação Microsoft com resultado ambíguo só é reconciliada; não é reenviada cegamente.
- A Evolution não oferece aqui uma garantia comprovada de envio exatamente uma vez. Timeout ou queda após envio deixa `uncertain`, sem reenvio automático. Isso evita duplicidade, mas pode exigir recuperação manual de uma mensagem não entregue.
- Uma entrada `needs_review` bloqueia novas operações do usuário até reconciliação administrativa.
- Não altere estados diretamente para “tentar de novo” sem conferir o efeito externo.
- Conteúdo de mensagens é limpo pela tarefa diária de retenção. Identidades, eventos, IDs de deduplicação e operações não resolvidas permanecem enquanto necessários.

## Verificações

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy app
python -m bandit -r app -q
python scripts/check_secrets.py
python -m pip_audit -r requirements.lock --disable-pip --no-deps
python scripts/demo.py
```

A demo força integrações fake e usa banco em memória; não chama APIs reais.

Dois testes adicionais de infraestrutura são habilitados por `INTEGRATION_DATABASE_URL` e `INTEGRATION_REDIS_URL`. Use serviços descartáveis: o nome do banco deve terminar em `_test`. O teste PostgreSQL cria e remove apenas seu schema temporário.

## Produção

`APP_ENV=production` bloqueia modos fake, segredos padrão/curtos, ausência de Fernet, SQLite, ausência de credenciais, quotas desligadas e callbacks incompatíveis. Desabilita Swagger e adiciona cabeçalhos de segurança.

O Compose fornecido é a base local; **não é uma implantação pública pronta**. TLS, VPN/MFA administrativa, firewall, isolamento da Evolution, backups criptografados e teste de restauração dependem da infraestrutura. Siga [deploy/PRODUCTION.md](deploy/PRODUCTION.md).

Listagens/consultas consideram o calendário conectado, inclusive eventos de dia inteiro. Alterações e exclusões operam nos eventos criados pelo bot e vinculados à conexão original. Não existe sincronização bidirecional completa de eventos editados fora do bot.

