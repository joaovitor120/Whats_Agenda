"""Relative calendar days use the user's local reference, never the server's date."""

import re
import unicodedata
from datetime import timedelta

WEEKDAYS = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}


def folded_date_text(text):
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


def relative_date(text, reference):
    normalized = folded_date_text(text)
    if re.search(r"\b(?:proxima semana|semana que vem|semana seguinte)\b", normalized):
        return None
    offsets = {"hoje": 0, "amanha": 1, "depois de amanha": 2}
    candidates = {
        reference.date() + timedelta(days=offsets[match.group()])
        for match in re.finditer(r"\b(?:depois de amanha|amanha|hoje)\b", normalized)
    }
    weekdays = "|".join(WEEKDAYS)
    for match in re.finditer(
        rf"\b(?:neste|nesta|nesse|nessa|este|esta)\s+({weekdays})(?:-feira)?\b",
        normalized,
    ):
        # Including today matters: 'neste domingo' on Sunday must not jump a week.
        days = (WEEKDAYS[match[1]] - reference.weekday()) % 7
        candidates.add(reference.date() + timedelta(days=days))
    numbers = {"um": 1, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7}
    for match in re.finditer(
        r"\b(?:daqui a|em)\s+(\d{1,3}|um|dois|tres|quatro|cinco|seis|sete)\s+dias?\b", normalized
    ):
        word = match[1]
        days = int(word) if word.isdigit() else numbers[word]
        if 0 <= days <= 730:
            candidates.add(reference.date() + timedelta(days=days))
    # Conflicting expressions require interpretation/clarification rather than choosing the first.
    return next(iter(candidates)) if len(candidates) == 1 else None


def anchor_relative_day(parsed, text, reference):
    """Enforce a clear day for one non-recurring request after model interpretation."""
    if parsed.intent not in {"create_event", "update_event", "list_events", "find_free_time"}:
        return parsed
    if parsed.recurrence_rule:
        return parsed
    # An explicit calendar date may qualify or contradict a relative expression;
    # let the interpreter clarify it instead of overriding it here.
    if re.search(
        r"\bdia\s+\d{1,2}\b|\b\d{1,2}\s*(?:[/.-]\s*\d{1,2}|de\s+\w+)\b",
        folded_date_text(text),
    ):
        return parsed
    day = relative_date(text, reference)
    if day is None:
        return parsed
    values = parsed.model_dump()
    values["event_date"] = day
    if parsed.start_at:
        local = parsed.start_at.astimezone(reference.tzinfo)
        corrected = local.replace(year=day.year, month=day.month, day=day.day)
        values["start_at"] = corrected
        if parsed.end_at:
            values["end_at"] = corrected + (parsed.end_at - parsed.start_at)
    return type(parsed).model_validate(values)


def ambiguous_date_language(text):
    return bool(
        re.search(
            r"\b(?:talvez|acho que|nao lembro|nao tenho certeza|semana que vem|proxima semana)\b",
            folded_date_text(text),
        )
    )
