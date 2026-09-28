"""Durable drafts and human-readable confirmation before any calendar write."""

import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.ai.schemas import ParsedIntent
from app.core.security import hash_state
from app.core.time import utc
from app.db.models import ConversationState
from app.events.formatting import format_calendar_date
from app.events.recurrence import recurrence_label
from app.events.service import owned_event, prepare_plan
from app.reminders.preferences import has_reminder_preference, reminder_label


async def store_draft(session, user, parsed):
    current = await session.get(ConversationState, user.id)
    if current is None:
        current = ConversationState(user_id=user.id)
        session.add(current)
    current.state = {"kind": "draft", "intent": parsed.model_dump(mode="json")}
    current.expires_at = datetime.now(UTC) + timedelta(minutes=10)
    return current


def draft_from_state(state):
    if state.get("intent"):
        return ParsedIntent.model_validate(state["intent"])
    plan = state["plan"]
    if plan.get("kind") == "batch":
        return ParsedIntent(
            intent="create_events", confidence=1, events=[item["intent"] for item in plan["plans"]]
        )
    return ParsedIntent.model_validate(plan["intent"])


async def ask_uncertain_date(session, user, parsed):
    """Ask about one ambiguous item at a time; confirming its day never writes an event."""
    items = parsed.events if parsed.intent == "create_events" else [parsed]
    zone = ZoneInfo(user.timezone)
    for index, item in enumerate(items):
        candidate = item.start_at.astimezone(zone).date() if item.start_at else item.event_date
        if not item.date_uncertain and not (candidate and item.confidence < 0.65):
            continue
        current = await store_draft(session, user, parsed)
        title = item.title or item.target_event or "seu compromisso"
        if candidate is None:
            return f"Desculpe, fiquei em dúvida sobre o dia de “{title}”. 😊📅 Você pode me dizer a data? Pode responder, por exemplo, ‘amanhã’ ou ‘dia 10/10’."
        if candidate < datetime.now(zone).date():
            return f"Entendi {format_calendar_date(candidate)} para “{title}”, mas esse dia já passou. 📅 Qual seria a nova data, por favor? 🙂"
        current.state = {
            **current.state,
            "kind": "date_clarification",
            "item_index": index,
            "proposed_date": candidate.isoformat(),
        }
        return (
            f"Desculpe, fiquei em dúvida sobre o dia em que você gostaria de agendar “{title}”. 😊📅\n"
            f"Você quis dizer {format_calendar_date(candidate)}?\n\n"
            "Responda SIM se o dia estiver correto ou me diga a data desejada. "
            "Depois eu mostro o resumo completo para você confirmar o agendamento. ✨"
        )
    return None


def accept_proposed_date(state):
    parsed = draft_from_state(state)
    index = state["item_index"]
    items = parsed.events if parsed.intent == "create_events" else [parsed]
    item = items[index]
    values = item.model_dump()
    values.update(
        event_date=state["proposed_date"],
        date_uncertain=False,
        confidence=max(item.confidence, 0.65),
    )
    confirmed = ParsedIntent.model_validate(values)
    if parsed.intent != "create_events":
        return confirmed
    events = [event.model_dump() for event in parsed.events]
    events[index] = confirmed.model_dump(exclude={"events"})
    return ParsedIntent.model_validate({**parsed.model_dump(), "events": events})


async def ask_confirmation(session, user, parsed, plan):
    current = await store_draft(session, user, parsed)
    code = secrets.token_hex(4).upper()
    current.state = {
        "kind": "confirmation",
        "intent": parsed.model_dump(mode="json"),
        "plan": plan,
        "code_hash": hash_state(code),
    }
    current.expires_at = datetime.now(UTC) + timedelta(minutes=5)
    plans = plan["plans"] if plan.get("kind") == "batch" else [plan]
    lines = ["📅 Confira antes de eu salvar:"]
    zone = ZoneInfo(user.timezone)
    for index, item in enumerate(plans, 1):
        intent = ParsedIntent.model_validate(item["intent"])
        event = (
            await owned_event(session, user, item["event_id"])
            if intent.intent != "create_event"
            else None
        )
        if intent.intent == "delete_event":
            if event is None:
                raise ValueError("Missing target")
            lines.append(
                f"\n{index}. Cancelar “{event.title}”"
                + (" e toda a série" if event.recurrence_rule else "")
            )
            continue
        if intent.intent == "update_reminder":
            if event is None:
                raise ValueError("Missing target")
            lines.append(
                f"\n{index}. Lembretes de “{event.title}”: {reminder_label(intent, event)}. Data e horário mantidos."
            )
            continue
        action = "Adicionar" if event is None else "Alterar"
        title = intent.title or (event.title if event else "Compromisso")
        lines.append(f"\n{index}. {action}: {title}")
        if item.get("title_only") and event is not None:
            lines.append(f"Nome atual: {event.title}. Data e horário mantidos.")
        if intent.start_at:
            local = utc(intent.start_at).astimezone(zone)
            lines.append(f"🕒 {format_calendar_date(local.date())} às {local:%H:%M}")
        lines.append(f"🔁 {recurrence_label(intent.recurrence_rule)}")
        lines.append(f"🔔 {reminder_label(intent, event)}")
        if event and event.recurrence_rule:
            lines.append("A alteração vale para toda a série.")
    lines.append(
        f"\nPosso salvar assim? Responda SIM ou CONFIRMAR {code} em até 5 minutos. Você também pode corrigir os dados ou responder CANCELAR."
    )
    return "\n".join(lines)


async def review_creations(session, user, inbound, parsed):
    items = (
        [ParsedIntent.model_validate(item.model_dump()) for item in parsed.events]
        if parsed.intent == "create_events"
        else [parsed]
    )
    questions = []
    for index, item in enumerate(items, 1):
        label = f"{index}. {item.title or 'Compromisso sem nome'}"
        if not item.title:
            questions.append(f"{label}: qual é o nome?")
        if item.reminder_mode in {"custom", "exclusive"} and not has_reminder_preference(item):
            questions.append(f"{label}: quantos minutos ou horas antes você quer ser lembrado? 🔔")
        if item.start_at is None:
            missing = (
                "horário"
                if item.recurrence_rule or item.event_date
                else "dia"
                if item.event_time
                else "dia e horário"
            )
            questions.append(f"{label}: qual é o {missing}?")
        elif item.start_at <= datetime.now(UTC):
            questions.append(f"{label}: informe uma data/horário futuro.")
        if item.confidence < 0.65 and item.title and item.start_at:
            questions.append(f"{label}: pode repetir os dias e o horário para eu entender melhor?")
    if questions:
        await store_draft(session, user, parsed)
        return "Guardei o pedido, mas ainda não adicionei nada. 📅\n" + "\n".join(questions)
    plans = [
        await prepare_plan(session, user, inbound, item, item_index=index)
        for index, item in enumerate(items)
    ]
    plan = (
        plans[0]
        if len(plans) == 1
        else {"kind": "batch", "plans": plans, "completed": 0, "replies": []}
    )
    return await ask_confirmation(session, user, parsed, plan)
