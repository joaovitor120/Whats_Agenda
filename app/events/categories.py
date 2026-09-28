from enum import StrEnum


class EventCategory(StrEnum):
    MEETING = "meeting"
    EXAM = "exam"
    DEADLINE = "deadline"
    MEDICAL_APPOINTMENT = "medical_appointment"
    CLASS = "class"
    PERSONAL = "personal"
    GYM = "gym"
    TRAVEL = "travel"
    FLIGHT = "flight"
    TRANSPORT = "transport"
    BIRTHDAY = "birthday"
    PAYMENT = "payment"
    TASK = "task"
    PARTY = "party"
    INTERVIEW = "interview"
    PRESENTATION = "presentation"
    OTHER = "other"


CATEGORY_KEYWORDS = {
    EventCategory.EXAM: ("prova", "exame", "teste"),
    EventCategory.MEETING: ("reunião", "reuniao", "meeting"),
    EventCategory.MEDICAL_APPOINTMENT: ("consulta", "dentista", "médic", "medic"),
    EventCategory.INTERVIEW: ("entrevista",),
    EventCategory.PAYMENT: ("vencimento", "pagar", "fatura", "cartão", "cartao"),
    EventCategory.FLIGHT: ("voo", "embarque"),
    EventCategory.TRAVEL: ("viagem", "viajar"),
    EventCategory.GYM: ("academia", "treino"),
    EventCategory.CLASS: ("aula", "classe"),
    EventCategory.DEADLINE: ("prazo", "entrega", "deadline"),
    EventCategory.PRESENTATION: ("apresentação", "apresentacao"),
    EventCategory.BIRTHDAY: ("aniversário", "aniversario"),
    EventCategory.PARTY: ("festa",),
}
