# Setup — rodar o Whats Agenda com integrações reais

Este roteiro usa **Windows, PowerShell e Docker Desktop**, na pasta `C:\Users\jvbin\Desktop\Whats_Agenda`.

Ao terminar, você terá o WhatsApp conectado à Evolution, interpretação pelo Claude e transcrição local de áudio e eventos gravados no Google Calendar **ou** Outlook. Escolha um calendário para começar.

## 1. Entenda os dois números de WhatsApp

| Número | Função |
| --- | --- |
| **A — número do bot** | Será pareado na Evolution. Recebe pedidos e envia respostas/lembretes. |
| **B — seu número pessoal** | Envia os pedidos para A. Será associado ao seu calendário. |

**Faça os testes enviando de B para A.** Mensagens enviadas pelo próprio número conectado (`fromMe=true`), grupos e mensagens para si mesmo são ignoradas. O telefone usado no OAuth será **B**, não A.

Você também precisa de:

- Docker Desktop aberto, usando containers Linux;
- acesso à Internet e computador ligado enquanto quiser receber lembretes;
- chave de API da Anthropic com acesso ao Claude e faturamento/créditos disponíveis;
- conta Google com Calendar ou conta Microsoft com calendário Outlook;
- permissão para criar um cliente OAuth no provedor escolhido.

O Docker instala as dependências da aplicação. Não é necessário instalar Python no Windows para seguir este roteiro.

## 2. Abra a pasta e preserve sua configuração

No PowerShell:

```powershell
Set-Location 'C:\Users\jvbin\Desktop\Whats_Agenda'
docker version
docker compose version

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
notepad .env
```

Edite o `.env` existente. **Não substitua pelo exemplo se ele já contiver suas chaves e senhas.** Não envie esse arquivo para conversas ou repositórios.

O projeto já teve os containers iniciados durante a revisão. Os comandos de inicialização abaixo também servem para retomá-los.

### Quem pode conversar com o bot

O `.env` controla os contatos autorizados em `WHATSAPP_ALLOWED_CONTACTS`, um objeto JSON que associa cada telefone ao nome. A configuração local já contém os oito contatos solicitados. Para alterar:

1. Abra `.env` com `notepad .env` e localize `WHATSAPP_ALLOWED_CONTACTS`.
2. Acrescente ou remova pares `"telefone":"Nome"`, separados por vírgulas, mantendo tudo na mesma linha. Use o número completo com **55 + DDD + número**, somente dígitos, sem `+`, espaços ou traços. Mantenha as aspas simples externas e as aspas duplas internas.
3. Exemplo de formato (substitua pelo telefone real autorizado):

   ```dotenv
   WHATSAPP_ALLOWED_CONTACTS='{"5527999999999":"Nome da pessoa"}'
   ```

4. Salve e recrie os serviços para carregar a nova lista:

   ```powershell
   docker compose up -d --no-deps --force-recreate api worker beat
   ```

Não basta usar `docker compose restart`: é necessário recriar os containers para carregar alterações do `.env`. Para mudanças apenas na lista, não precisa reconstruir a imagem.

Números fora da lista são ignorados silenciosamente: sem resposta, aviso de bloqueio, indicação de digitando, transcrição ou chamada ao Claude. O bloqueio também vale para lembretes e mensagens antigas ainda pendentes de envio. A autorização usa o telefone informado pela Evolution no webhook autenticado, nunca o nome do perfil. Os nomes configurados identificam os usuários locais e são atualizados na próxima mensagem processada.

`WHATSAPP_ALLOWED_CONTACTS='{}'` (ou a variável ausente) bloqueia todos. JSON inválido impede a aplicação de iniciar. A comparação dos telefones é exata; não há autorização por sufixo nem remoção automática do nono dígito. Se um contato autorizado não for reconhecido, confira o telefone retornado pela Evolution. Ao remover alguém, seus eventos permanecem no banco, mas os envios ficam bloqueados; mensagens já enviadas não são desfeitas.

## 3. Confira endereços e segredos locais

Mantenha estas configurações para rodar no seu computador:

```dotenv
APP_ENV=development
PUBLIC_BASE_URL=http://localhost:8000
DEFAULT_TIMEZONE=America/Sao_Paulo

POSTGRES_USER=whatsagenda
POSTGRES_DB=whatsagenda
REDIS_URL=redis://redis:6379/0

EVOLUTION_API_URL=http://evolution:8080
EVOLUTION_WEBHOOK_BASE_URL=http://api:8000
EVOLUTION_INSTANCE_NAME=whats-agenda
EVOLUTION_INSTANCE_TOKEN=

ADMIN_ALLOWED_NETWORKS=127.0.0.1/32,::1/128
RATE_LIMIT_ENABLED=true
```

`APP_ENV=development` permite o acesso HTTP local. **As três variáveis `FAKE_*` determinam se as integrações são reais**, e serão desligadas no passo 6. Este roteiro local não publica o serviço na Internet.

### Segredos que precisam existir

| Variável | Uso |
| --- | --- |
| `APP_SECRET_KEY` | Segredo da aplicação |
| `ADMIN_API_KEY` | Autenticação administrativa |
| `EVOLUTION_WEBHOOK_SECRET` | Evolution autentica o webhook da agenda |
| `EVOLUTION_API_KEY` | Agenda e painel acessam a Evolution |
| `TOKEN_ENCRYPTION_KEY` | Criptografia dos tokens de calendário |
| `POSTGRES_PASSWORD` / `POSTGRES_ADMIN_PASSWORD` | Banco da agenda: aplicação / administrador |
| `EVOLUTION_DB_PASSWORD` / `EVOLUTION_DB_ADMIN_PASSWORD` | Banco da Evolution: aplicação / administrador |

Preserve os valores válidos já configurados. Se algum segredo de aplicação ainda estiver vazio ou começar com `replace`, gere um valor independente para ele. Com a imagem da aplicação construída:

```powershell
docker compose build api
docker compose run --rm --no-deps api python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Repita a geração para cada segredo necessário, copie o resultado para o campo correspondente e evite expor a saída do terminal. Se o Compose reclamar de uma variável obrigatória vazia antes do build, você pode gerar um segredo diretamente no PowerShell:

```powershell
$randomBytes = New-Object byte[] 48
$randomGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$randomGenerator.GetBytes($randomBytes)
$randomGenerator.Dispose()
[Convert]::ToBase64String($randomBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
```

Para **uma nova** chave Fernet:

```powershell
docker compose run --rm --no-deps api python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Cole-a em `TOKEN_ENCRYPTION_KEY`. **Não troque uma chave Fernet usada por conexões existentes**: os tokens antigos deixam de ser decifráveis.

`DATABASE_URL` deve usar a mesma senha de `POSTGRES_PASSWORD`:

```dotenv
DATABASE_URL=postgresql+asyncpg://whatsagenda:SENHA_DO_BANCO_DA_AGENDA@postgres:5432/whatsagenda
```

Não copie o marcador `SENHA_DO_BANCO_DA_AGENDA` literalmente. Prefira senhas geradas com caracteres seguros para URL. Em bancos já inicializados, mudar a senha no `.env` **não altera a senha do PostgreSQL**; coordene essa mudança com a role existente. Não apague volumes para resolver autenticação.

## 4. Configure Claude e a transcrição local

No `.env`, mantenha sua chave Anthropic e configure:

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=COLE_SUA_CHAVE_AQUI
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
LLM_MAX_OUTPUT_TOKENS=2048
FAKE_LLM=false
AUDIO_ENABLED=true
AUDIO_TRANSCRIPTION_PROVIDER=local
LOCAL_TRANSCRIPTION_URL=http://transcription:8001
LOCAL_TRANSCRIPTION_API_KEY=SEGREDO_EXCLUSIVO_DE_PELO_MENOS_32_CARACTERES
COMPOSE_PROFILES=audio
```

Gere o segredo local pelo mesmo procedimento da seção 3, preservando um valor válido existente. Não copie o marcador acima literalmente. Não coloque suas chaves no painel da Evolution.

Confira créditos e acesso ao modelo no [Console Anthropic](https://console.anthropic.com/). O Haiku usa uma saída limitada a 2.048 tokens para comportar vários compromissos, sem pensamento estendido. Correções recebem apenas o rascunho pendente como contexto, sem histórico completo. Os tokens de entrada também são cobrados. Consultas da agenda e respostas predefinidas são produzidas pelo código.

O serviço local **faster-whisper small/int8** converte o áudio em texto, e o Claude interpreta esse texto. Não há transcrição nativa de áudio pelo Claude. Não é necessária chave OpenAI para essa configuração. O primeiro build baixa o modelo; depois ele roda sem Internet no container `transcription`. O modelo small substitui o tiny para melhorar o reconhecimento. Ainda pode errar com ruído, nomes e horários; repetições excessivas e segmentos de baixa confiança são recusados antes de chegar ao Claude. O container tem limite de 2 GB de RAM e 2 CPUs.

## 5. Cadastre um provedor de calendário

Faça **5A ou 5B**. O projeto mantém apenas uma conexão ativa de calendário por usuário.

### 5A. Google Calendar

1. Abra o [Google Cloud Console](https://console.cloud.google.com/) e selecione/crie um projeto.
2. Em APIs e serviços, habilite a **Google Calendar API**.
3. Configure o Google Auth Platform/tela de consentimento: nome, contato e público. Para uma conta pessoal, use público externo e inclua seu e-mail como usuário de teste enquanto o aplicativo estiver em teste.
4. Em acesso a dados, configure os escopos usados pela aplicação: `openid`, `email` e `https://www.googleapis.com/auth/calendar.events`.
5. Crie um cliente OAuth do tipo **Aplicativo da Web**. Cadastre este URI de redirecionamento autorizado, exatamente:

```text
http://localhost:8000/oauth/google/callback
```

6. Copie o ID e o segredo do cliente para o `.env`:

```dotenv
GOOGLE_CLIENT_ID=SEU_CLIENT_ID
GOOGLE_CLIENT_SECRET=SEU_CLIENT_SECRET
GOOGLE_REDIRECT_URI=http://localhost:8000/oauth/google/callback
```

Não use cliente Desktop, chave de API simples ou conta de serviço para este fluxo. O projeto usa consentimento do usuário e o calendário principal da conta conectada. Referências: [habilitar Calendar e configurar consentimento](https://developers.google.com/workspace/calendar/api/quickstart/python) e [criar credenciais OAuth Web](https://developers.google.com/workspace/guides/create-credentials).

**Uso contínuo:** em um aplicativo externo com status de publicação Testing, refresh tokens para esses escopos podem expirar em sete dias. Para operação contínua, configure o status de publicação adequado e conclua as exigências de verificação aplicáveis. Referência: [Google — expiração de tokens](https://developers.google.com/identity/protocols/oauth2#expiration).

### 5B. Microsoft / Outlook

1. Abra o [Microsoft Entra admin center](https://entra.microsoft.com/), vá a App registrations e crie um registro.
2. Escolha o público compatível com sua conta. Para Outlook.com pessoal junto com contas organizacionais, permita ambos os tipos e use `MICROSOFT_TENANT=common`. Para aplicativo restrito à organização, use o ID do tenant correspondente.
3. Adicione a plataforma **Web** e o redirecionamento abaixo:

```text
http://localhost:8000/oauth/microsoft/callback
```

4. Em API permissions, use permissões **delegadas** do Microsoft Graph: `User.Read`, `Calendars.ReadWrite` e `offline_access`. Conceda consentimento administrativo se a organização exigir.
5. Em Certificates & secrets, crie um client secret. Copie o **Value**, não o Secret ID, e registre a validade.
6. Preencha o `.env`:

```dotenv
MICROSOFT_CLIENT_ID=APPLICATION_CLIENT_ID
MICROSOFT_CLIENT_SECRET=VALOR_DO_CLIENT_SECRET
MICROSOFT_TENANT=common
MICROSOFT_REDIRECT_URI=http://localhost:8000/oauth/microsoft/callback
```

Sua conta precisa ter um calendário acessível pelo Graph. Referências: [registro do aplicativo](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) e [registro e credenciais para Microsoft Graph](https://learn.microsoft.com/en-us/graph/auth-register-app-v2).

## 6. Desative as simulações e suba os serviços

Depois de preencher Anthropic, criptografia, segredos e o calendário escolhido, altere:

```dotenv
FAKE_LLM=false
FAKE_CALENDAR=false
FAKE_EVOLUTION=false
AUDIO_ENABLED=true
```

Salve o `.env` e execute:

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps --all
Invoke-RestMethod http://localhost:8000/health/ready
Invoke-RestMethod http://localhost:8080/
```

Espere API, Evolution, bancos e Redis ficarem saudáveis. Worker e Beat devem estar `Up`. O serviço `migrate` terminar com **Exited (0)** é esperado: ele executa as migrations e encerra.

Confirme que os processos receberam a configuração, sem imprimir chaves:

```powershell
docker compose exec -T api python -c 'from app.core.config import settings as s; print({"fake_llm":s.fake_llm,"fake_calendar":s.fake_calendar,"fake_evolution":s.fake_evolution,"anthropic_configurada":bool(s.anthropic_api_key),"fernet_configurada":bool(s.token_encryption_key)})'
```

Os três campos `fake_*` devem ser `False`; os dois campos `*_configurada`, `True`.

O áudio fica desativado separadamente por `AUDIO_ENABLED=false`. Ao receber um áudio válido, o bot pede educadamente uma mensagem de texto, sem baixar a mídia nem chamar transcrição ou LLM. Isso não altera uma conversa pendente.

Para ativar áudio local, use a configuração da seção 4 e execute `docker compose up -d --build transcription api worker beat`. O container `transcription` deve ficar saudável. Para parar somente esse serviço, use `docker compose stop transcription`, após desativar o áudio e recriar API/worker/Beat.

Depois de novas alterações no `.env`, use `docker compose up -d --force-recreate api worker beat`. Um simples `restart` não carrega novas variáveis no container.

## 7. Conecte o número do bot à Evolution

1. Abra [Evolution Manager](http://localhost:8080/manager).
2. No login, use a URL `http://localhost:8080` e o valor de `EVOLUTION_API_KEY` do `.env`.
3. Crie uma instância com nome **exatamente igual** a `EVOLUTION_INSTANCE_NAME`; no exemplo, `whats-agenda`. Se ela já existir, abra-a.
4. Escolha a integração WhatsApp/Baileys (`WHATSAPP-BAILEYS`).
5. Abra a opção de conectar/gerar QR da instância.
6. No WhatsApp do **número A**, entre em Aparelhos conectados → Conectar um aparelho e leia o QR.
7. Aguarde a instância mostrar conexão aberta/conectada.

Os nomes dos botões podem variar no painel. Não crie outra instância com nome diferente para contornar um erro: o backend só aceita a instância configurada.

### Acesso direto ao painel na VPS, sem túnel

No servidor, configure o IP e uma porta externa disponível. Neste exemplo, 8090 evita a porta 8080 que já apresentou conflito:

```bash
cd /opt/Whats_Agenda
git pull --ff-only
python3 scripts/configure_evolution_access.py --host 76.13.228.205 --port 8090
docker compose config --quiet
docker compose up -d --force-recreate evolution
docker compose ps evolution
```

O script atualiza somente `EVOLUTION_BIND_ADDRESS=0.0.0.0`, `EVOLUTION_PORT=8090` e `EVOLUTION_SERVER_URL=http://76.13.228.205:8090` no `.env`. As credenciais são preservadas. O Compose usa a mesma URL como origem permitida no CORS e mantém a porta interna da Evolution em 8080. A API da agenda, PostgreSQL e Redis continuam privados.

Abra **http://76.13.228.205:8090/manager**. No login, informe **http://76.13.228.205:8090** e sua `EVOLUTION_API_KEY`. Depois crie/abra a instância e leia o QR conforme os passos anteriores. O navegador acessa diretamente a VPS; nenhuma Evolution precisa rodar no computador.

Se houver firewall no painel do provedor, permita TCP 8090. Se a porta escolhida estiver ocupada, execute o script com outra porta disponível e use a nova URL indicada. Para conferir localmente na VPS: `curl -fsS http://127.0.0.1:8090/`. Esta verificação confirma o serviço, mas o acesso externo ainda depende das regras de rede.

O acesso direto por IP deste exemplo usa HTTP, que transmite a API Key sem criptografia. Para uso permanente com HTTPS, configure um domínio e proxy conforme `deploy/PRODUCTION.md`.

### Configure o webhook automaticamente

Com a instância criada e pareada:

```powershell
docker compose exec -T api python -m app.integrations.evolution.setup
```

O comando configura o evento `MESSAGES_UPSERT`, a URL `http://api:8000/webhooks/evolution` e o header `Authorization: Bearer <EVOLUTION_WEBHOOK_SECRET>`. Ele não imprime o QR nem as credenciais.

Você não precisa de túnel público para esse webhook: Evolution e agenda estão na mesma rede Docker. Não coloque `http://localhost:8000` como destino dentro da Evolution, porque ali `localhost` seria o próprio container dela.

## 8. Registre seu usuário pelo WhatsApp

Do **número B**, envie para o **número A**:

```text
Oi
```

Aguarde a resposta de ajuda. O bot solicita o processamento logo após receber a mensagem e sinaliza “digitando…” durante o preparo da resposta, com três segundos de digitação antes do envio. Worker ocupado ou indisponível pode atrasar o início do indicador. Beat recupera tarefas pendentes a cada 30 segundos caso o despacho imediato falhe.

Quando a mensagem não resultar na criação de um compromisso, a resposta também inclui até cinco próximos compromissos, com emojis, datas e horários no seu fuso. A busca cobre os próximos dois anos e inclui ocorrências de eventos recorrentes. Se houver menos de cinco, mostra os disponíveis; se não houver nenhum, avisa. Em modo fake, esses compromissos são os registrados localmente, não os da sua conta Google/Microsoft.

Essa primeira mensagem cria seu usuário. Só depois inicie o OAuth. Não crie um compromisso antes de conectar o calendário.

## 9. Autorize seu calendário

Este comando faz a requisição administrativa **de dentro do container**, via loopback, usando a chave já carregada. Assim, não é preciso ampliar `ADMIN_ALLOWED_NETWORKS` para a rede Docker.

No PowerShell, informe **seu número B**, com país e DDD, somente dígitos, e escolha o provedor:

```powershell
$phone = '5511999999999' # SUBSTITUA pelo numero B
$provider = 'google'     # ou 'microsoft'

$oauthUrl = @'
import sys
import httpx
from app.core.config import settings

phone, provider = sys.argv[1:3]
if provider not in {"google", "microsoft"}:
    raise SystemExit("Use google ou microsoft")
response = httpx.get(
    f"http://127.0.0.1:8000/oauth/{provider}/start",
    params={"phone": phone},
    headers={"X-Admin-Api-Key": settings.admin_api_key},
    timeout=20,
    trust_env=False,
)
if response.status_code != 200:
    raise SystemExit(f"HTTP {response.status_code}: {response.json().get('detail')}")
print(response.json()["authorization_url"])
'@ | docker compose exec -T api python - $phone $provider

if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel iniciar o OAuth.' }
Start-Process ($oauthUrl.Trim())
```

1. No navegador **deste computador**, entre na conta do calendário e autorize os escopos.
2. O navegador retorna a `localhost:8000`; a resposta deve indicar `pending_whatsapp_confirmation`.
3. No WhatsApp B, aguarde a mensagem com a conta mascarada e o código.
4. Confira a conta e responda ao bot exatamente:

```text
CONECTAR CODIGO_RECEBIDO
```

5. Aguarde **“Calendário conectado ao seu WhatsApp”**.

O consentimento e a confirmação têm prazo de 10 minutos. Use o código recebido, sem copiar o marcador acima. Não compartilhe a URL OAuth. Se expirar ou houver falha na troca do token, execute novamente o comando para obter um fluxo novo.

Não abra o link no celular durante esse setup local: o callback `localhost` passaria a apontar para o celular. Para autorizações em outros dispositivos, será necessário um endereço HTTPS alcançável e redirects correspondentes.

## 10. Valide um compromisso real

Gere uma frase com data futura, no PowerShell:

```powershell
$eventDate = (Get-Date).AddDays(10).ToString('dd/MM/yyyy')
"Tenho prova de calculo dia $eventDate as 13h"
```

Envie a frase resultante de B para A. Confira:

1. O bot apresenta a prévia. Responda `SIM` para salvar; se corrigir algo, confira e confirme a nova prévia.
2. O evento aparece no calendário da conta autorizada, no dia e horário corretos.
3. Como a prova está a dez dias, são previstos seis lembretes locais: 7 dias, 3 dias, 24 horas, 2 horas e 5 minutos antes, além do alerta no início.
4. Ao perguntar sobre a agenda desse dia, o bot encontra o evento.

Os lembretes são controlados pelo **banco da agenda** e enviados pelo WhatsApp; não precisam aparecer como notificações configuradas no Google/Outlook.

### Alteração, cancelamento e áudio

- Para uma rotina diária: “Quero estudar inglês todos os dias às 19h”. O bot agenda a próxima ocorrência futura e repete diariamente no mesmo horário, com lembretes 15 minutos antes, 5 minutos antes e no início. Você também pode informar a data inicial: “Estudar inglês todos os dias a partir de amanhã às 19h”. Sem horário, o bot pergunta e guarda o pedido por dez minutos.
- Os lembretes futuros da série são renovados automaticamente. Mantenha Docker, `worker`, `beat` e a conexão da Evolution ativos para recebê-los. Se o computador ficar desligado, não há envio durante esse período. Alertas antecipados expiram no início do evento; o alerta AGORA admite até 2 minutos de atraso (ou a tolerância configurada, se menor). Mensagens vencidas além da tolerância são descartadas.

- Para renomear sem remarcar: “Mude o nome do evento Reunião de equipe para Planejamento semanal”. Informe o nome atual e o novo, depois envie o código de confirmação. Não é necessário repetir data e horário.
- Saudações como “Opa, bom dia, e aí, tudo bem?” recebem uma resposta amigável e a prévia da agenda, inclusive quando chegam em áudio.

- Envie uma remarcação indicando um único evento, a data completa e o novo horário.
- Confira a prévia e responda `CONFIRMAR <código recebido>` em até cinco minutos.
- Confira a mudança no calendário externo.
- Faça o mesmo com um cancelamento de teste.
- Com `AUDIO_ENABLED=false`, envie um áudio e confira o pedido educado para mandar texto. Para testar transcrição, habilite `AUDIO_ENABLED=true`, recrie os serviços e grave um áudio curto e claro com evento, data e horário. Na configuração local, Whisper transcreve e Claude interpreta; depois segue o mesmo fluxo de confirmação quando aplicável.

`SIM` confirma apenas a última prévia válida da sua conversa. O código também pode ser usado; códigos antigos não confirmam uma prévia corrigida. Mudanças em recorrências são tratadas como mudanças da série inteira, conforme a prévia. Eventos alterados diretamente no Google/Outlook não têm sincronização bidirecional completa dos lembretes locais.

### Vários dias, vários eventos e lembrete individual

Você também pode usar “Dentista amanhã às 15h” ou “Estudar inglês depois de amanhã às 19h”. Quando houver dúvida no dia, o bot apresenta a data provável com o dia da semana. Responda `SIM` para confirmar o dia ou corrija, como “Não, depois de amanhã”. Essa resposta ainda não adiciona o evento: confira a prévia completa e confirme novamente para salvar.

Envie “Academia toda segunda, terça e sexta às 18:50; inglês todos os dias às 20h”. Confira os dois itens na prévia. Você pode responder “Mude só a academia para 19h”; confira a nova prévia e responda `SIM` para adicionar ambos. Até dez eventos são aceitos por pedido.

Para receber **somente os avisos que você escolher**, envie “Me lembre da academia apenas 12 horas, 1 hora e 15 minutos antes”. Confira a prévia e responda `SIM`. A academia terá exatamente esses três lembretes, sem aviso de 5 minutos nem do início; os demais eventos mantêm suas configurações. Em uma série, a escolha se repete em cada ocorrência. “Apenas 30 minutos antes” deixa um único aviso. Se quiser incluir o início, diga “somente 5 minutos antes e na hora”.

Sem “apenas/somente/só”, por exemplo “Me lembre da academia 30 minutos antes”, o sistema mantém também 5 minutos antes e início, listados na prévia. Para restaurar o padrão, envie “Volte aos lembretes padrão da academia” e confirme. Você pode informar até dez horários distintos, entre o início e sete dias antes. Horários iguais não geram duplicatas; avisos já passados não são reenviados.

Todos os lembretes enviados faltando menos de uma hora começam com 🚨. No início, a mensagem destaca `AGORA`: “🚨 Atenção! Você tem o compromisso ‘Academia’ marcado para AGORA! ⏰”. A verificação acontece a cada 30 segundos; fila e conexão podem atrasar a entrega em relação ao horário previsto.

### Confira um lembrete sem esperar dias

Crie uma **reunião de teste para aproximadamente 20 minutos no futuro**, com data e horário explícitos. Pela política de reunião, o lembrete de 15 minutos antes será agendado para aproximadamente cinco minutos depois da criação. Também serão agendados os alertas de 5 minutos antes e do início. Os de 24 horas e uma hora não serão criados, pois já estariam no passado.

Deixe Docker, worker e Beat rodando. Confira os avisos no WhatsApp B, incluindo o emoji de alerta e a mensagem AGORA no início.

## 11. Uso diário e diagnóstico

### Ligar e desligar preservando dados

Se aparecer `failed to connect ... dockerDesktopLinuxEngine` ou `The system cannot find the file specified`, inicie o Docker Desktop e aguarde o motor Linux:

```powershell
docker desktop start --timeout 90
docker info --format '{{.OSType}}'
```

O segundo comando deve mostrar `linux`. Depois execute `docker compose up -d` e `docker compose ps`. Se a inicialização exceder o tempo limite, abra o Docker Desktop pelo menu Iniciar e confira o estado exibido. Mantenha o Docker Desktop rodando enquanto usar o bot.

```powershell
# Ligar, com Docker Desktop aberto
docker compose up -d

# Ver estado
docker compose ps --all

# Parar mantendo bancos e sessoes
docker compose stop
```

`docker compose down` também mantém os volumes por padrão. **Não use `down -v`** na sua instalação: ele apaga os volumes do banco e da Evolution. Mantenha o computador sem suspensão para operação contínua.

### Consultar logs

```powershell
docker compose logs --tail 100 api worker beat
docker compose logs --tail 100 evolution
docker compose logs --tail 60 migrate
```

Revise e remova dados pessoais/segredos antes de compartilhar logs, principalmente da Evolution.

### Consultar operações com problemas

```powershell
@'
import json
import httpx
from app.core.config import settings
r = httpx.get(
    "http://127.0.0.1:8000/admin/operations",
    headers={"X-Admin-Api-Key": settings.admin_api_key},
    timeout=20, trust_env=False,
)
r.raise_for_status()
print(json.dumps(r.json(), indent=2))
'@ | docker compose exec -T api python -
```

| Problema | O que conferir |
| --- | --- |
| Não responde ao “Oi” | Número B enviando para A; instância conectada; nome exato; `FAKE_EVOLUTION=false`; webhook configurado; worker/Beat ativos. |
| Contato aparece como `@lid` e não é processado | A Evolution precisa fornecer `data.key.remoteJidAlt` com telefone individual válido (`@s.whatsapp.net`). Sem esse campo o contato é ignorado; não use os dígitos do LID como telefone. |
| Evolution 401 | `EVOLUTION_API_KEY` deve coincidir com a chave carregada no container Evolution. Se mudou a chave, recrie também `evolution`. |
| Webhook 400 | Confira instância, relógio do sistema e timestamp; histórico com mais de cinco minutos é rejeitado. |
| Webhook 401 | Rode novamente o setup depois de conferir `EVOLUTION_WEBHOOK_SECRET`. |
| Admin 403 no Windows | Use os comandos administrativos internos deste guia; não libere `0.0.0.0/0`. |
| OAuth 404: usuário inexistente | Primeiro envie “Oi” de B e espere a resposta; confira o telefone informado. |
| OAuth 503 | Client ID e redirect do provedor precisam estar configurados no container recriado. |
| `redirect_uri_mismatch` / erro de redirect Microsoft | Compare esquema, hostname, porta e caminho cadastrados com o `.env`. Não troque `localhost` por `127.0.0.1` em apenas um lado. |
| Google bloqueia o usuário em teste | Inclua a conta em test users; confira o público e os escopos do consentimento. |
| Falha de interpretação/transcrição | Confira chave, modelo, créditos e limites da Anthropic; para áudio local, confira se `transcription` está saudável, o perfil `audio` e o segredo compartilhado. |
| Banco rejeita senha | A senha do `.env` precisa corresponder à role já existente no volume. |
| Pedido funciona, mas não aparece no calendário | Confira `FAKE_CALENDAR=false`, confirmação `CONECTAR` concluída e conta autorizada. Eventos antigos criados em fake não são migrados para a agenda real. |
| Entrada `needs_review` ou saída `uncertain` | Confira o efeito externo antes de tentar novamente; essas situações exigem reconciliação administrativa. |
| Lembrete não veio após longo período desligado | Lembretes atrasados além da tolerância, ou cujo evento já começou, expiram. |

## 12. Quando levar para um servidor 24 horas

O setup acima já usa integrações reais, mas permanece local. Para disponibilização contínua em servidor:

1. Configure domínio, proxy HTTPS e certificados.
2. Ajuste `PUBLIC_BASE_URL`, `EVOLUTION_WEBHOOK_BASE_URL` e redirects OAuth para as URLs HTTPS definitivas; atualize também os cadastros dos provedores.
3. Configure o proxy confiável na API conforme [deploy/PRODUCTION.md](deploy/PRODUCTION.md).
4. Use `APP_ENV=production`, todos os `FAKE_*` desligados e os segredos exigidos pelo validador.
5. Restrinja administração/Evolution à rede privada ou VPN; configure MFA no acesso administrativo externo.
6. Faça backup criptografado, guarde a chave Fernet separadamente e valide uma restauração.
7. Repita pareamento, OAuth, criação, alteração, cancelamento e entrega de lembrete no ambiente definitivo; confira a resposta de áudio desativado ou a transcrição, conforme `AUDIO_ENABLED`.

Os comandos administrativos via HTTP/loopback deste guia são para o modo local; em produção a API exige HTTPS também nas rotas administrativas. Use o ingresso HTTPS administrativo configurado, sem tentar contornar essa exigência com headers enviados pelo cliente.

Consulte [SECURITY.md](SECURITY.md) para os limites conhecidos e [README.md](README.md) para arquitetura, quotas e referências operacionais.
