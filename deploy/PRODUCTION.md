# Implantação e verificações externas

O Compose inclui uma Evolution local 2.3.7 fixada por digest, com banco/Redis separados e usuário sem root. O código não instala TLS, VPN, MFA, backups nem atualiza automaticamente essa imagem. Esses controles precisam ser aplicados ao ambiente antes de expor o serviço. As APIs da agenda e da Evolution são publicadas apenas no loopback.

## Rede e HTTPS

- Mantenha Evolution, PostgreSQL e Redis em redes privadas. Não publique 5432/6379 nem o painel Evolution.
- Use TLS com verificação de certificado em conexões de banco/Redis que saiam da rede privada.
- Termine HTTPS em um proxy confiável. Na API, habilite `--proxy-headers --forwarded-allow-ips=<IP_EXATO_DO_PROXY>`. Nunca use `*`.
- O proxy deve sobrescrever `X-Forwarded-For` e `X-Forwarded-Proto`, sem confiar nos valores recebidos da Internet.
- Publique somente `POST /webhooks/evolution` e os callbacks OAuth. Bloqueie `/admin`, `/oauth/*/start`, `/docs`, `/openapi.json` e `/health/ready` na borda pública.
- Em `APP_ENV=production`, configure `EVOLUTION_WEBHOOK_BASE_URL` para o ingresso HTTPS; o endereço HTTP `http://api:8000` é apenas para desenvolvimento.
- Limite corpo a 256 KB, tempo de leitura a 5 segundos e conexões simultâneas por IP. A aplicação também valida esses limites.
- Não registre URI completa/query string dos callbacks, Authorization, QR nem corpos das mensagens.

Exemplo conceitual de trechos para um proxy Nginx já provisionado com certificados:

```nginx
# Contexto http:
limit_req_zone $binary_remote_addr zone=agenda:10m rate=2r/s;

# Dentro do server HTTPS:
client_max_body_size 256k;
client_body_timeout 5s;
access_log off;

location = /webhooks/evolution {
    limit_except POST { deny all; }
    limit_req zone=agenda burst=20 nodelay;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Host $host;
    proxy_read_timeout 30s;
    proxy_pass http://127.0.0.1:8000;
}
location ~ ^/oauth/(google|microsoft)/callback$ {
    limit_except GET { deny all; }
    limit_req zone=agenda burst=5 nodelay;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Host $host;
    proxy_read_timeout 50s;
    proxy_pass http://127.0.0.1:8000;
}
location / { return 404; }
```

Esse trecho depende da topologia e não foi executado nesta máquina. Se o proxy estiver em container, o upstream e o IP confiável precisam refletir a rede Docker. Redirecione HTTP para HTTPS no virtual host de porta 80, usando o hostname fixo configurado.

## Administração

Disponibilize um ingresso administrativo separado, alcançável somente por VPN/Zero Trust com MFA, ou autenticação mTLS. Exija também `X-Admin-Api-Key` e configure `ADMIN_ALLOWED_NETWORKS` com a menor rede possível. O aplicativo implementa allowlist e chave, não um servidor de identidade/MFA/RBAC completo.

Não exponha o ingresso administrativo ao criar uma exceção genérica no proxy público. Teste de uma rede externa que setup/QR, desconexão e início OAuth recebem 403/404.

## PostgreSQL e Redis

O bootstrap de banco novo cria `POSTGRES_USER` como role limitada. `postgres` usa `POSTGRES_ADMIN_PASSWORD`, uma credencial separada. Para reduzir ainda mais privilégios, separe uma role de migration com DDL e uma role de runtime com DML.

Em volumes existentes, confirme privilégios com o administrador: o init SQL não é reexecutado. Não apague dados para aplicar essa configuração.

Redis armazena mensagens de trabalho com IDs, locks de desenvolvimento e quotas. Está sem porta publicada e com serialização JSON no Celery. Um cliente com acesso de escrita ao broker é parte da fronteira confiável: use ACL/autenticação dedicada quando outros serviços compartilharem a rede e TLS se o tráfego atravessar redes não confiáveis. Não use eviction para descartar quotas.

A Evolution usa outra rede de dados, outro PostgreSQL, outro Redis e credenciais próprias. Seu volume `evolution_instances` contém sessões sensíveis; proteja-o e inclua sua retenção no plano operacional. A retenção de 30 dias da agenda não remove mensagens do banco da Evolution. A imagem de terceiros exige seu próprio acompanhamento de vulnerabilidades; o pip-audit da aplicação Python não cobre seus pacotes Node.js ou o sistema operacional.

## Backups, retenção e exclusão

- Configure backup PostgreSQL consistente e criptografado, com acesso restrito e chave separada do arquivo.
- Faça backup protegido da chave Fernet; perder essa chave inutiliza os tokens persistidos.
- Defina prazo de retenção também para backups, logs e sessões da Evolution.
- Restaure periodicamente em ambiente isolado e confira eventos, tokens, migrations e permissões.
- A tarefa de retenção limpa textos/payloads/transcrições; ela não é um sistema completo de exclusão de conta.
- Para excluir uma conta: primeiro revogue conexões, encerre jobs do usuário, remova seus eventos/lembretes e registros associados em transação controlada, e aplique a política de expiração de backups.
- SQLite é apenas desenvolvimento. O protótipo anterior podia gravar horas sem offset: revise horários de bases SQLite antigas contra a fonte original antes de reutilizá-las. PostgreSQL preserva os offsets.

Não há backup de produção configurado ou restauração comprovada nesta revisão. O banco usado nos testes foi descartável, com dados sintéticos.

## Recuperação após falha

Consulte `GET /admin/operations` na rede administrativa.

Para entrada `needs_review`, confira o plano salvo e o calendário na conexão original. Em criação Google, o ID da operação é o ID remoto. Em Microsoft, procure a propriedade `WhatsAgendaOperation` e `transactionId`. Só conclua/reabra a operação após verificar o resultado real.

Para saída `uncertain`, confira o histórico de mensagens da Evolution. Não mude diretamente para `pending` sem verificar se houve entrega. Não há garantia de exactly-once entre o banco e o WhatsApp.

## Homologação obrigatória

Nesta revisão foram concluídos build/inicialização Docker, migrations em banco novo, testes de concorrência PostgreSQL e quotas Redis em ambiente isolado. As verificações abaixo devem ser repetidas na infraestrutura escolhida; integrações de negócio reais e atualização da base existente continuam pendentes.

1. Build dos containers e migrations em PostgreSQL, inclusive atualização de uma cópia de dados existentes.
2. Testes de concorrência com PostgreSQL e Redis descartáveis usando as variáveis `INTEGRATION_*`.
3. Webhook real com header; repetição, timestamp antigo, assinatura/secret incorreto e reconexão da Evolution.
4. OAuth Google/Microsoft: PKCE, consentimento, confirmação pelo WhatsApp, refresh expirado e revogação.
5. Criação/listagem/alteração/exclusão e séries em contas de teste, incluindo falha após sucesso remoto.
6. Envio e transcrição de áudio válido, inválido, grande e com duração adulterada.
7. Quotas reais e indisponibilidade de Redis, broker, calendário e OpenAI.
8. Restauração de backup, rotação de segredos, MFA administrativa, logs e firewall.
9. Scanner da imagem e do host. Os digests Docker fixam a imagem, mas não provam ausência de CVEs.
10. Atualização da Evolution para uma versão suportada, revisão das permissões e proteção dos dados de sessão/QR.

Nenhuma chamada externa de negócio foi feita nesta revisão. A lista acima deve ser concluída na infraestrutura escolhida antes da liberação pública.

