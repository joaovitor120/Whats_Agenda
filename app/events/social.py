"""Low-cost replies for purely social messages; mixed agenda requests pass through."""

import re
import unicodedata

SOCIAL_PHRASES = (
    "oi",
    "oii",
    "oiii",
    "ola",
    "opa",
    "oie",
    "ei",
    "hey",
    "hello",
    "bom dia",
    "boa tarde",
    "boa noite",
    "e ai",
    "eai",
    "e ae",
    "tudo bem",
    "tudo bom",
    "tudo certo",
    "tudo tranquilo",
    "beleza",
    "blz",
    "como vai",
    "como voce esta",
    "como voce ta",
    "como vc esta",
    "como vc ta",
    "como esta",
    "como ta",
    "como estao as coisas",
    "com voce",
    "com vc",
    "e voce",
    "e vc",
    "obrigado",
    "obrigada",
    "muito obrigado",
    "muito obrigada",
    "valeu",
    "agradeco",
    "tchau",
    "ate mais",
    "ate logo",
    "falou",
    "abraco",
)
_PHRASES = "|".join(re.escape(p) for p in sorted(SOCIAL_PHRASES, key=len, reverse=True))
SOCIAL_MESSAGE = re.compile(rf"(?:{_PHRASES})(?:\s+(?:{_PHRASES}))*")
WELLBEING_QUESTION = re.compile(
    r"\b(?:tudo (?:bem|bom|certo|tranquilo)|"
    r"como (?:(?:voce|vc) )?(?:vai|esta|ta)|como estao as coisas|"
    r"(?:e|com) (?:voce|vc))\b"
)


def normalize_social(text: str) -> str:
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())


def social_reply(text: str) -> str | None:
    normalized = normalize_social(text)
    if len(normalized) > 240 or not SOCIAL_MESSAGE.fullmatch(normalized):
        return None
    return format_social_reply(text)


def format_social_reply(text: str) -> str:
    normalized = normalize_social(text)
    if re.search(r"\b(tchau|ate mais|ate logo|falou)\b", normalized):
        return "Até mais! 👋 Foi bom falar com você. Quando precisar organizar sua agenda, é só me chamar! 📅"
    if re.search(r"\b(obrigad[oa]|valeu|agradeco)\b", normalized):
        return "Por nada! 😊 Pode contar comigo para organizar seus compromissos. 📅"
    greeting = "Oi"
    for phrase in ("bom dia", "boa tarde", "boa noite"):
        if phrase in normalized:
            greeting = phrase.capitalize()
            break
    asks_wellbeing = WELLBEING_QUESTION.search(normalized) or (
        "?" in text and re.search(r"\b(?:beleza|blz)\b", normalized)
    )
    wellbeing = " Tudo certo por aqui! E com você?" if asks_wellbeing else ""
    return f"{greeting}! 😊{wellbeing} Estou por aqui para te ajudar com sua agenda. 📅"
