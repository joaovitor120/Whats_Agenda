# Revisão de segurança — Whats Agenda

Data: 26/09/2026. Escopo: código Python da aplicação, migrations, integrações, workers, configuração Docker, dependências, scripts e testes presentes nesta pasta, confrontados com o documento de ameaças fornecido.

**Conclusão:** havia falhas reais no protótipo; foram corrigidas e receberam regressões automatizadas. Isso não certifica ausência absoluta de vulnerabilidades nem comprova operação de ponta a ponta com serviços reais. A implantação pública ainda depende das verificações externas abaixo.

## Principais achados e tratamento

| Achado | Correção / limite |
| --- | --- |
| Webhook autenticado só por segredo na URL | Endpoint fixo, Bearer em header, comparação constante, instância vinculada e timestamp obrigatório |
| Replay e corpo sem limite antecipado | UNIQUE por instância/mensagem, janela de 5 min, leitura limitada a 256 KB/5 s e batch de até 20 |
| Remetentes e payloads permissivos | Validação de telefone/JID, tipos, ID, texto, áudio; grupos/fromMe/status não executam ações |
| Fake calendar compartilhado entre usuários | Removido armazenamento global; consultas locais vinculadas ao dono |
| Calendário arbitrário escolhido para alteração | Evento vinculado ao ID da conexão original; unicidade de calendário ativo; rejeição de ambiguidade |
| Curingas ou alvo vazio selecionando eventos | Busca parametrizada com escape de curingas e exigência de um único alvo |
| LLM tratado como autoridade | Schema extra=forbid, limites e enums; proprietário/destinatário fora do schema; política Python independente |
| Prompt injection indireta | Conteúdo externo não entra no contexto do modelo; listagens renderizadas deterministicamente |
| Mutações sem confirmação | Prévia obrigatória também para criações/lotes e lembretes; dono vinculado, prazo de 5 min e uso único, incluindo séries completas |
| Confirmação com contexto antigo | SIM confirma só a proposta vigente; correções invalidam previamente o plano/código antigo e pedem nova confirmação, mesmo se a interpretação da correção falhar |
| Remarcação incompleta criava outro evento | Conversa preserva intent, alvo, categoria, data e recorrência |
| Horário sem data assumia o dia atual | Pede data explicitamente; horários novos são persistidos em UTC |
| OAuth sem PKCE / state reutilizável em falha | PKCE S256; state consumido atomicamente e commit antes da troca; callbacks fixos |
| Conta OAuth ativada sem confirmação do telefone | Token pendente criptografado; identidade mascarada e confirmação pelo mesmo WhatsApp |
| Substituição de conta reaproveitando IDs antigos | Novas conexões têm novo ID; eventos mantêm a conexão original |
| Refresh tokens em risco / configuração fraca | Fernet obrigatório em produção, segredos separados e revogação/desconexão administrativa |
| Admin exposto por chave simples | Allowlist de rede + chave, quota e auditoria sem conteúdo; VPN/MFA devem ser fornecidas pela implantação |
| Gastos sem limite | Quotas Redis atômicas por IP/instância/telefone/usuário, teto global de LLM/áudio, entrada/saída limitadas |
| SSRF de mídia | URLs da mensagem são rejeitadas; somente chave de mídia persistida na Evolution; sem redirects/proxy de ambiente |
| Arquivo de áudio adulterado | Bytes limitados, base64 validado, MIME/magic e duração real com Mutagen; sem arquivos temporários |
| Transcrição local exposta | Serviço sem porta publicada, rede interna sem Internet, segredo exclusivo, execução sem root, limites de CPU/memória e uma inferência por vez; modelo e dependências fixados |
| Contexto desnecessário enviado ao Claude | Apenas mensagem atual, referência temporal e schema; sem histórico nem listagem da agenda; JSON validado localmente, saída limitada e sem pensamento estendido |
| Respostas externas sem limite | JSON com limite de bytes, sem compressão/redirect; validação de IDs/datas e paginação restrita |
| Calendário ignorava dia inteiro/paginação | Inclui eventos de dia inteiro; paginação limitada; falha em vez de declarar disponibilidade em leitura incompleta |
| Locks por thread e processamento duplicado | Lock por remetente, releitura após lock, ordem por recebimento; advisory lock PostgreSQL em produção |
| Async engine reutilizado entre event loops Celery | NullPool evita reutilizar conexões ligadas a outro loop |
| Sucesso remoto seguido de rollback criava duplicata | Plano persistido antes da mutação; Google com ID estável; Microsoft com marcador e reconciliação |
| Reenvio cego após timeout WhatsApp | Claim atômico e status uncertain; entrega ambígua exige revisão |
| Lembretes concorrentes / atraso recontado | Claim e outbox na mesma transação, unicidade, horário original preservado, expiração e revalidação antes de envio |
| Retry infinito após falha | Limite de tentativas; needs_review bloqueia novas operações do usuário |
| Segredos em logs/contexto Docker | Redação, logs HTTP sem query, QR fora do CLI, ignore de .env.*, allowlist de arquivos no build |
| Container root / dependências flutuantes | Usuário sem root, filesystem read-only, limites, hashes de pacotes e digests das imagens |
| Migration dependia dos modelos atuais | Migration inicial congelada; revisão 0002 explícita e teste de atualização com dados legados |
| Retenção indefinida de conteúdo de mensagens | Limpeza diária de payloads, textos, transcrições e planos concluídos; pendências preservadas para investigação |

A biblioteca `cryptography` foi atualizada para 50.0.1 depois que a auditoria apontou vulnerabilidades conhecidas na versão inicialmente resolvida. Nenhuma vulnerabilidade foi ignorada no pip-audit.

## Evidências locais

- Testes automatizados: **64 aprovados, nenhum pulado**, incluindo PostgreSQL e Redis descartáveis, em `reports/tests.xml`.
- Testes cobrem webhook forjado/replay, entradas inválidas, isolamento de usuários, seleção por curingas, confirmações expiradas/de outro usuário, PKCE/state, ativação OAuth, quotas indisponíveis, mídia inválida, resposta HTTP ilimitada, paginação hostil, concorrência da outbox, lembretes vencidos/cancelados e falha local depois de sucesso remoto.
- Ruff e mypy executados sobre o código.
- Bandit: relatório em `reports/bandit.json`. A única exclusão B105 explica o apagamento intencional de uma credencial revogada; não esconde uma senha configurada.
- pip-audit: `reports/dependency-audit.json` e `reports/dependency-audit-dev.json`; zero vulnerabilidades conhecidas nas consultas realizadas.
- Scanner offline de padrões de credenciais: `scripts/check_secrets.py`. É uma verificação limitada de padrões conhecidos, não uma análise de todo histórico Git.
- Instalação dos lockfiles com `--require-hashes` e `pip check`.
- Migrações 0001 → 0002 em SQLite e PostgreSQL; atualização com dados legados em SQLite; `alembic check` sem divergências nos dois bancos.
- Build Docker com hashes, inicialização de API/Beat/worker, readiness, role PostgreSQL sem superusuário/CREATEDB/CREATEROLE e usuário de aplicação UID 10001 comprovados no projeto descartável `whatsagenda-audit`.
- Concorrência de claim de lembretes e advisory locks PostgreSQL; quotas Redis concorrentes, executadas contra serviços reais locais.
- Fluxo completo API → Redis/Celery → PostgreSQL → adapters fake: webhook autenticado, replay ignorado, criação de um evento com quatro lembretes, resposta enviada pelo worker e entrega de um lembrete vencendo durante o teste. Evidência em `reports/stack-smoke.json`; Google/Microsoft, OpenAI e WhatsApp foram simulados.
- Evolution local 2.3.7: build fixado por digest, UID 1000, migrations, health, painel, autenticação, CORS e persistência de headers/flags do webhook verificados contra o servidor real. A instância sintética usada foi removida. Evidência em `reports/evolution-smoke.json`; nenhum WhatsApp foi pareado.
- Demo offline com evento de prova e quatro lembretes futuros, sem APIs reais.

Os relatórios ficam em pasta ignorada por Git. Não contêm valores de credenciais. Não existia repositório Git nesta pasta durante a revisão, portanto não houve auditoria de histórico remoto nem criação de commit.

## Limitações e pendências para liberação pública

1. **Ambiente de implantação:** os testes Docker/PostgreSQL/Redis usaram infraestrutura descartável e dados sintéticos. A atualização de uma cópia da base real, capacidade sob carga e restauração de backup continuam pendentes. Sem as variáveis `INTEGRATION_*`, os dois testes de infraestrutura são pulados nas próximas execuções.
2. **Integrações reais:** não foram usados tokens de Google/Microsoft ou OpenAI para operações de negócio. A Evolution local foi testada em health, autenticação e configuração de webhook; consentimento, refresh, mídia e envio real pelo WhatsApp ainda exigem homologação.
3. **Autenticação administrativa:** a aplicação não fornece MFA/RBAC completos. Exigir VPN/Zero Trust ou mTLS em ingresso separado antes de produção.
4. **Evolution comprometida / WhatsApp roubado:** uma origem confiável comprometida ainda pode representar o usuário. O Compose agora inclui a Evolution com banco/Redis próprios e acesso de loopback. A correção de CORS da derivação local é limitada à ausência de Origin e vinculada ao digest; não é uma auditoria completa do código/dependências Node.js de terceiros. Atualização, proteção da chave e das sessões continuam necessárias.
5. **HTTPS e infraestrutura:** proxy, certificados, ACLs, TLS remoto de banco/Redis, backup criptografado, restauração e scanner da imagem/host ainda precisam de execução no ambiente de implantação.
6. **Entrega exatamente uma vez:** não é prometida. Após falha ambígua de WhatsApp, evitar duplicata pode significar exigir recuperação manual de uma mensagem que não foi entregue. Microsoft também exige revisão se a reconciliação não encontrar uma criação anteriormente tentada.
7. **Privacidade e retenção:** o job limpa conteúdo de mensagens, mas não implementa um fluxo completo de exclusão da conta nem expiração de backups externos. Metadados operacionais e eventos continuam persistidos.
8. **Calendários externos:** não há espelhamento bidirecional completo. Alterações feitas fora do bot podem deixar os lembretes locais desatualizados. Homologar esse comportamento antes de uso em compromissos críticos.
9. **SQLite legado:** o protótipo podia armazenar horas locais sem offset. A origem exata do fuso de dados antigos pode não ser recuperável automaticamente; conferir contra a agenda original antes de reutilizar.
10. **Segredos previamente expostos:** valores copiados para conversas, logs ou saídas antigas de configuração devem ser rotacionados. Não foi feita rotação automática da chave Fernet, pois isso poderia inutilizar tokens existentes.
11. **Limitações intencionais:** mensagens antigas, JIDs sem telefone confiável, calendários ambíguos, recorrências não suportadas e confirmações inválidas falham de forma fechada. Não são convertidos em ações por aproximação.
12. **Sem garantia absoluta:** testes, revisão estática e scanners reduzem riscos conhecidos; não substituem pentest nem homologação operacional.

O roteiro concreto de implantação e homologação está em [deploy/PRODUCTION.md](deploy/PRODUCTION.md).

## Referências primárias consultadas

- IDs definidos pelo cliente ajudam a impedir criação duplicada no Google Calendar: [Google — Create events](https://developers.google.com/workspace/calendar/api/guides/create-events).
- `transactionId` é o identificador de repetição fornecido pelo cliente no Graph: [Microsoft — Event resource](https://learn.microsoft.com/en-us/graph/api/resources/event?view=graph-rest-1.0).
- PKCE, CSRF e redirect URIs fazem parte das práticas OAuth atuais: [RFC 9700](https://datatracker.ietf.org/doc/html/rfc9700).
- JSON schema estrito e configuração de armazenamento da Responses API: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) e [Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses).
- Autenticação por headers em webhooks e busca de mídia pela chave persistida: [Evolution — documentação de headers](https://github.com/evolution-foundation/docs-evolution/pull/22) e [implementação oficial da mídia](https://github.com/evolution-foundation/evolution-api/blob/main/src/api/integrations/channel/whatsapp/whatsapp.baileys.service.ts).

