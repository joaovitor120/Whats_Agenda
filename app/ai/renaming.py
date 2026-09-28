"""Explicit title changes have their own extraction and clarification rules."""

import re

from app.ai.schemas import MissingField, ParsedIntent

_VERB = r"(?:trocar|troque|troca|mudar|mude|muda|alterar|altere|altera)"
_NAME = r"(?:nome|t[ií]tulo)"
_EVENT = r"(?:(?:o\s+)?(?:evento|compromisso)\s+)?"
_RENAME = re.compile(
    rf"\b(?:renome\w*|{_VERB}\s+(?:(?:apenas|s[oó]|somente)\s+)?"
    rf"(?:(?:o|no)\s+)?{_NAME}\b)",
    re.I,
)
_PAIR_PATTERNS = (
    rf"{_VERB}\s+(?:(?:apenas|s[oó]|somente)\s+)?(?:o\s+|no\s+)?"
    rf"{_NAME}\s+(?:de|do|da)\s+{_EVENT}(?P<old>.+?)\s+(?:para|pra|por)\s+(?P<new>.+)",
    rf"\brenome(?:ar|ie|ia)\s+{_EVENT}(?P<old>.+?)\s+(?:para|pra)\s+(?P<new>.+)",
    rf"\b{_NAME}\s+atual\s+[eé]\s+(?P<old>.+?)\s*[,;]?\s+e\s+"
    rf"(?:o\s+)?(?:novo(?:\s+{_NAME})?|{_NAME}\s+novo)\s+(?:[eé]|ser[aá])\s+(?P<new>.+)",
)


def is_rename_request(text: str) -> bool:
    return bool(_RENAME.search(text)) or bool(
        re.search(rf"\b{_NAME}\s+atual\b.*\bnovo\b", text, re.I)
    )


def starts_new_request(text: str) -> bool:
    """Let an explicit new operation leave an incomplete title conversation."""
    return bool(
        re.match(
            r"\s*(?:por favor[, ]*)?(?:(?:quero|preciso|gostaria de|pode)\s+)?"
            r"(?:criar|crie|adicionar|adicione|agendar|agende|marcar|marque|"
            r"cancelar|cancele|excluir|exclua|remarcar|remarque|listar|liste|mostrar|mostre)\b",
            text,
            re.I,
        )
    )


def clean_name(value: str) -> str | None:
    value = re.sub(r"[,;]?\s+por favor[.!?]*$", "", value, flags=re.I)
    value = value.strip(" \t\r\n.,!?\"'“”‘’")
    if not value or len(value) > 255:
        return None
    if re.fullmatch(
        r"(?:(?:um|uma|o|a|esse|essa|este|esta|meu|minha)\s+)?"
        r"(?:evento|compromisso)(?:\s+na\s+(?:minha\s+)?agenda)?|"
        r"(?:isso|aquilo|ele|ela|outro|outro nome)",
        value,
        re.I,
    ):
        return None
    return value


def rename_intent(target: str | None, title: str | None, confidence: float) -> ParsedIntent:
    missing: list[MissingField] = []
    if not target:
        missing.append("target_event")
    if not title:
        missing.append("title")
    return ParsedIntent(
        intent="update_event",
        target_event=target,
        title=title,
        missing_fields=missing,
        confidence=confidence,
    )


def explicit_rename(text: str) -> ParsedIntent | None:
    # Compound instructions need model interpretation; never include them in a title.
    if re.search(
        r"\s+e\s+(?:quero\s+)?(?:mude|mudar|altere|alterar|troque|trocar|remarque|cancele)\b",
        text,
        re.I,
    ):
        return None
    for pattern in _PAIR_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            target = clean_name(match["old"])
            title = clean_name(match["new"])
            # Multiple unquoted separators are ambiguous (e.g. a title with "para").
            if target and title and not re.search(r"\s+(?:para|pra|por)\s+", title, re.I):
                return rename_intent(target, title, 0.99)
    return None


def constrain_rename(parsed: ParsedIntent, previous: ParsedIntent | None) -> ParsedIntent:
    # A model's creation/date response must never turn a title request into an insertion.
    target = parsed.target_event if parsed.intent == "update_event" else None
    title = parsed.title if parsed.intent == "update_event" else None
    target = clean_name(target) if target else None
    title = clean_name(title) if title else None
    if previous:
        target = target or previous.target_event
        title = title or previous.title
    return rename_intent(target, title, parsed.confidence)
