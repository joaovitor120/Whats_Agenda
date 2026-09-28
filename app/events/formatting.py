from datetime import date, datetime

WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def format_calendar_date(value: date) -> str:
    return f"{value:%d/%m/%Y} ({WEEKDAYS[value.weekday()]})"


def format_local_datetime(value: datetime) -> str:
    clock = value.strftime("%Hh") if value.minute == 0 else value.strftime("%Hh%M")
    return f"{value:%d/%m às }{clock}"
