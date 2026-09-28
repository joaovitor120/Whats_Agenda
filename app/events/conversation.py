"""Conversation state and deterministic decisions, independent of Celery delivery."""

import json
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.ai.anthropic import InterpreterUnavailable
from app.ai.interpreter import IntentInterpreter
from app.ai.renaming import is_rename_request, starts_new_request
from app.ai.schemas import ParsedIntent
from app.core.config import settings
from app.core.limits import consume
from app.core.security import decrypt_token, encrypt_token, hash_state, verify_secret
from app.core.time import utc
from app.db.models import CalendarConnection, ConversationState, PendingConnection
from app.events.recent import recent_event_draft, refers_to_recent_event
from app.events.review import (
    accept_proposed_date,
    ask_confirmation,
    ask_uncertain_date,
    draft_from_state,
    review_creations,
    store_draft,
)
from app.events.service import (
    AmbiguousEvent,
    apply_plan,
    find_target,
    is_title_only_update,
    list_events,
    owned_event,
    prepare_plan,
)
from app.events.social import format_social_reply, social_reply
from app.reminders.preferences import has_reminder_preference


async def handle_text(session, user, inbound, text):
    now = datetime.now(UTC)
    zone = ZoneInfo(user.timezone)
    if re.fullmatch(r"CONECTAR [0-9A-F]{16}", text.strip().upper()):
        return await confirm_connection(session, user, text.strip().upper().split()[1])
    pending = await session.get(ConversationState, user.id)
    parsed = None
    rename_context = None
    draft_context = None
    selected_target_id = None
    if pending and utc(pending.expires_at) <= now:
        await session.delete(pending)
        await session.flush()
        pending = None
    greeting = social_reply(text)
    if greeting:
        if pending:
            greeting += (
                "\n\nSeu pedido anterior continua pendente; podemos retomá-lo quando quiser."
            )
        return greeting
    reply = text.strip().upper().rstrip(".!?")
    if pending and pending.state.get("kind") == "date_clarification":
        if reply in {"SIM", "ISSO", "ISSO MESMO", "CORRETO", "SIM, ESSE DIA", "SIM ESSE DIA"}:
            parsed = accept_proposed_date(pending.state)
            await session.delete(pending)
            await session.flush()
            pending = None
        elif reply in {"NÃO", "NAO"}:
            await store_draft(session, user, draft_from_state(pending.state))
            return "Sem problema! 😊📅 Qual é o dia correto? Pode dizer ‘hoje’, ‘amanhã’, ‘depois de amanhã’ ou a data desejada."
        elif reply not in {"CANCELAR", "CANCELA", "DESISTIR"}:
            draft_context = draft_from_state(pending.state)
    if pending and text.strip().casefold().rstrip(".!?") in {
        "cancelar",
        "cancela",
        "desistir",
        "não",
        "nao",
    }:
        await session.delete(pending)
        return "Tudo bem, não alterei nenhum compromisso. 🙂"
    if pending and pending.state.get("kind") == "confirmation":
        reply = text.strip().upper().rstrip(".!?")
        code = reply.removeprefix("CONFIRMAR ")
        accepted = reply in {
            "SIM",
            "CONFIRMO",
            "PODE ADICIONAR",
            "PODE SALVAR",
            "SIM, PODE ADICIONAR",
            "SIM PODE ADICIONAR",
        }
        accepted = accepted or (
            reply.startswith("CONFIRMAR ")
            and verify_secret(hash_state(code), pending.state["code_hash"])
        )
        if accepted:
            inbound.plan = pending.state["plan"]
            await session.delete(pending)
            await session.commit()
            return await apply_plan(session, user, inbound)
        if reply.startswith("CONFIRMAR "):
            return "Esse código não corresponde à confirmação atual. Confira a última prévia e responda SIM, ou envie uma correção."
        draft_context = draft_from_state(pending.state)
        # Invalidate the old approval before interpreting any correction, even on API failure.
        pending = await store_draft(session, user, draft_context)
        await session.commit()
    elif pending and pending.state.get("kind") in {"draft", "schedule"}:
        draft_context = draft_from_state(pending.state)
    if pending is None and refers_to_recent_event(text):
        recent = await recent_event_draft(session, user, inbound, now)
        if recent:
            selected_target_id, draft_context = recent
    rename_requested = draft_context is None and is_rename_request(text)
    if pending and pending.state.get("kind") == "rename" and not starts_new_request(text):
        rename_context = ParsedIntent.model_validate(pending.state["intent"])
    interrupt_pending = rename_requested or (
        pending is not None and pending.state.get("kind") == "rename" and starts_new_request(text)
    )
    if pending and not interrupt_pending and rename_context is None and draft_context is None:
        state = pending.state
        if state.get("kind") == "event_selection":
            choice = text.strip().casefold().rstrip(".!?")
            spoken = {
                "um": "1",
                "primeiro": "1",
                "dois": "2",
                "segundo": "2",
                "três": "3",
                "tres": "3",
                "terceiro": "3",
                "quatro": "4",
                "quarto": "4",
                "cinco": "5",
                "quinto": "5",
            }
            choice = spoken.get(choice.removeprefix("o "), choice)
            if not re.fullmatch(r"[1-5]", choice) or int(choice) > len(state["candidate_ids"]):
                return "Qual dos compromissos da lista você quer alterar? 📅 Responda com o número da opção, ou CANCELAR para desistir."
            selected_target_id = state["candidate_ids"][int(choice) - 1]
            parsed = ParsedIntent.model_validate(state["intent"])
            await session.delete(pending)
            await session.flush()
    if parsed is None:
        if text.strip().lower() == "ajuda":
            return "Posso criar, alterar, cancelar e consultar eventos. Exemplo: Tenho prova amanhã às 13h."
        await consume("llm-user", user.id, settings.llm_requests_per_day, 86400)
        await consume("llm-global", "instance", 5000, 86400)
        try:
            interpreter = IntentInterpreter()
            if draft_context is not None:
                parsed = await interpreter.interpret(
                    text, user.timezone, now.astimezone(zone), draft_context=draft_context
                )
            elif rename_context is not None:
                parsed = await interpreter.interpret(
                    text, user.timezone, now.astimezone(zone), rename_context=rename_context
                )
            else:
                parsed = await interpreter.interpret(text, user.timezone, now.astimezone(zone))
        except InterpreterUnavailable:
            return "Não consegui acessar a interpretação de mensagens agora. Tente novamente daqui a pouco. 🙂"
        except ValueError:
            if draft_context is not None:
                return "Não consegui entender essa correção. Ainda não salvei nada. 📅 Diga o nome do compromisso e o dado que deseja corrigir."
            if rename_requested or rename_context is not None:
                return "Não consegui identificar os nomes com segurança. 📅 Diga: trocar o nome de Reunião para Planejamento."
            return "Não consegui interpretar a solicitação com segurança. Informe um evento, data e horário."
        if draft_context is not None and parsed.intent not in {
            "create_event",
            "create_events",
            "update_event",
            "delete_event",
            "update_reminder",
        }:
            return "Ainda não salvei o pedido. 📅 Informe qual compromisso quer corrigir e o novo horário, dia, nome ou antecedência do lembrete. Para desistir, responda CANCELAR."
        if (
            draft_context is not None
            and draft_context.intent == "create_events"
            and parsed.intent != "create_events"
        ):
            return "Qual compromisso da lista você deseja corrigir? 📅 Informe o nome ou o número e a mudança."
        if pending and (
            interrupt_pending or rename_context is not None or draft_context is not None
        ):
            await session.delete(pending)
            await session.flush()
    if parsed.intent in {"create_event", "create_events"}:
        clarification = await ask_uncertain_date(session, user, parsed)
        if clarification:
            return clarification
        try:
            return await review_creations(session, user, inbound, parsed)
        except ValueError:
            await store_draft(session, user, parsed)
            return "Ainda não salvei os compromissos. 📅 Confira se as datas são futuras, a duração é válida e a agenda está conectada. Você pode corrigir o pedido."
    if parsed.intent == "update_event":
        clarification = await ask_uncertain_date(session, user, parsed)
        if clarification:
            return clarification
    if parsed.intent == "update_reminder" and (
        not parsed.target_event or not has_reminder_preference(parsed)
    ):
        await store_draft(session, user, parsed)
        if not parsed.target_event:
            return "De qual compromisso você quer mudar o lembrete? 📅 Informe o nome."
        return f"Quais lembretes você quer para “{parsed.target_event}”? 🔔 Pode dizer: apenas 12 horas, 1 hora e 15 minutos antes."
    if (
        rename_requested
        or rename_context is not None
        or (parsed.intent == "update_event" and "title" in parsed.missing_fields)
        or is_title_only_update(parsed)
    ):
        if not parsed.target_event or not parsed.title:
            session.add(
                ConversationState(
                    user_id=user.id,
                    state={"kind": "rename", "intent": parsed.model_dump(mode="json")},
                    expires_at=now + timedelta(minutes=10),
                )
            )
            if not parsed.target_event and not parsed.title:
                return "Claro! Vamos trocar só o nome, mantendo data e horário. 📅 Qual é o nome atual do compromisso e qual será o novo nome?"
            if not parsed.target_event:
                return f"Qual é o nome atual do compromisso que você quer chamar de “{parsed.title}”? 📅"
            return f"Qual será o novo nome de “{parsed.target_event}”? 📅 Vou manter a data e o horário."
    if parsed.intent == "small_talk":
        return format_social_reply(text)
    if parsed.intent == "help":
        return "Posso criar, alterar, cancelar e consultar eventos. Sempre peço confirmação antes de salvar mudanças."
    rename_only = is_title_only_update(parsed)
    if parsed.intent == "update_event" and not parsed.target_event and selected_target_id is None:
        return "Qual compromisso você quer alterar? 📅 Informe o nome atual e o que deseja mudar; para renomear, diga também o novo nome."
    if parsed.intent == "update_event" and not parsed.title and "title" in parsed.missing_fields:
        return "Qual será o novo nome? 📅 Envie o nome atual do compromisso e o novo nome que você quer usar."
    if parsed.intent == "update_event" and parsed.start_at is None and not rename_only:
        await store_draft(session, user, parsed)
        if parsed.recurrence_rule == "FREQ=DAILY" and not parsed.event_date:
            return "Claro! Vou repetir esse compromisso todos os dias. 📅 Qual será o horário?"
        return "Qual é o horário?" if parsed.event_date else "Qual é a data e o horário?"
    if parsed.intent in {"update_event", "delete_event", "update_reminder"}:
        if parsed.confidence < 0.65:
            if rename_only:
                return "Para eu confirmar a mudança, diga o nome atual do compromisso e o novo nome que você quer usar. 📅"
            return "Não tenho certeza da solicitação. Informe o evento, a data e o horário explicitamente."
        try:
            target = (
                await owned_event(session, user, selected_target_id)
                if selected_target_id
                else await find_target(session, user, parsed.target_event)
            )
            plan = await prepare_plan(session, user, inbound, parsed, target.id if target else None)
        except AmbiguousEvent as exc:
            session.add(
                ConversationState(
                    user_id=user.id,
                    state={
                        "kind": "event_selection",
                        "intent": parsed.model_dump(mode="json"),
                        "candidate_ids": [event.id for event in exc.candidates],
                    },
                    expires_at=now + timedelta(minutes=10),
                )
            )
            options = "\n".join(
                f"{index}. {' '.join(event.title.split())[:180]} — {utc(event.start_at).astimezone(zone):%d/%m/%Y às %H:%M}"
                + (" (série recorrente)" if event.recurrence_rule else "")
                for index, event in enumerate(exc.candidates, 1)
            )
            return (
                "Encontrei mais de um compromisso com esse nome. 📅\n"
                f"{options}\n\nResponda com o número da opção desejada em até 10 minutos. "
                "Depois vou pedir sua confirmação antes de alterar."
            )
        except ValueError:
            if rename_only:
                return "Não consegui identificar um único compromisso para renomear. 📅 Informe o nome atual completo e o novo nome; verifique também a conexão da agenda."
            return "Não foi possível identificar uma operação segura. Informe um único evento e uma data futura; verifique a conexão do calendário."
        return await ask_confirmation(session, user, parsed, plan)
    if parsed.intent in {"list_events", "find_event", "find_free_time"}:
        selected = parsed.event_date
        if parsed.intent == "list_events" and not selected and not parsed.target_event:
            # The worker appends the upcoming agenda to every non-creation reply.
            return "Claro! Vamos dar uma olhada na sua agenda. 😊"
        beginning = (
            datetime.combine(selected, time.min, tzinfo=zone) if selected else now.astimezone(zone)
        )
        ending = beginning + timedelta(days=1 if selected else 7)
        events = await list_events(session, user, beginning, ending)
        if parsed.intent == "find_free_time":
            day = selected or (now.astimezone(zone) + timedelta(days=1)).date()
            beginning = datetime.combine(
                day, time.fromisoformat(settings.default_day_start), tzinfo=zone
            )
            ending = datetime.combine(
                day, time.fromisoformat(settings.default_day_end), tzinfo=zone
            )
            events = await list_events(session, user, beginning, ending)
            candidate = beginning
            while candidate + timedelta(hours=2) <= ending:
                if candidate > now and not any(
                    start < candidate + timedelta(hours=2) and end > candidate
                    for start, end, _ in events
                ):
                    return f"Você tem 2 horas livres em {candidate:%d/%m}, das {candidate:%H:%M} às {candidate + timedelta(hours=2):%H:%M}."
                candidate += timedelta(minutes=15)
            return "Não encontrei um intervalo livre de 2 horas nesse dia."
        if parsed.target_event:
            events = [
                item for item in events if parsed.target_event.casefold() in item[2].casefold()
            ]
        return (
            "Sua agenda: "
            + "; ".join(
                f"{start.astimezone(zone):%d/%m %H:%M} — {title}"
                for start, _, title in sorted(events)[:20]
            )
            if events
            else "Não encontrei eventos nesse período."
        )
    return "Não entendi a solicitação. Informe um evento, a data e o horário."


async def confirm_connection(session, user, code):
    grant = await session.scalar(
        select(PendingConnection)
        .where(
            PendingConnection.code_hash == hash_state(code),
            PendingConnection.user_id == user.id,
            PendingConnection.expires_at > datetime.now(UTC),
        )
        .with_for_update()
    )
    if grant is None:
        return "Código de conexão inválido ou expirado."
    if await session.scalar(
        select(CalendarConnection.id).where(
            CalendarConnection.user_id == user.id, CalendarConnection.active.is_(True)
        )
    ):
        return "Já existe um calendário conectado. Solicite a desconexão ao administrador antes de trocar a conta."
    tokens = json.loads(decrypt_token(grant.encrypted_tokens))
    session.add(
        CalendarConnection(
            user_id=user.id,
            provider=grant.provider,
            calendar_id="primary",
            encrypted_access_token=encrypt_token(tokens["access_token"]),
            encrypted_refresh_token=encrypt_token(tokens["refresh_token"])
            if tokens.get("refresh_token")
            else None,
            token_expires_at=grant.token_expires_at,
            active=True,
        )
    )
    await session.delete(grant)
    return "✅ Calendário conectado ao seu WhatsApp."
