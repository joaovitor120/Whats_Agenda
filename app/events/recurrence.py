"""Shared recurrence labels and next-occurrence calculation in the user's timezone."""

from datetime import timedelta

DAY_NAMES = dict(
    MO="segunda", TU="terça", WE="quarta", TH="quinta", FR="sexta", SA="sábado", SU="domingo"
)


def recurrence_label(rule):
    if rule == "FREQ=DAILY":
        return "todos os dias"
    if rule and rule.startswith("FREQ=WEEKLY;BYDAY="):
        return "toda semana: " + ", ".join(
            DAY_NAMES[day] for day in rule.split("BYDAY=")[1].split(",")
        )
    return "sem repetição"


def next_occurrence(rule, clock, reference):
    days = list(DAY_NAMES)
    selected = set(rule.split("BYDAY=")[1].split(",")) if "BYDAY=" in rule else set(days)
    for offset in range(8):
        at = (reference + timedelta(days=offset)).replace(
            hour=clock.hour, minute=clock.minute, second=0, microsecond=0
        )
        if days[at.weekday()] in selected and at > reference:
            return at
    raise ValueError("No future occurrence")
