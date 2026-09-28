import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.events.categories import EventCategory

IntentName = Literal[
    "create_event",
    "create_events",
    "update_event",
    "update_reminder",
    "delete_event",
    "list_events",
    "find_event",
    "find_free_time",
    "help",
    "small_talk",
    "unknown",
]
MissingField = Literal[
    "title", "start_at", "end_at", "target_event", "reminder_minutes", "reminder_offsets_minutes"
]


class EventIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    intent: IntentName
    category: EventCategory = EventCategory.OTHER
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Nome do evento a criar; em renomeação, o NOVO nome informado pelo usuário.",
    )
    start_at: datetime | None = None
    event_date: date | None = None
    event_time: str = Field(
        default="",
        pattern=r"^(([01][0-9]|2[0-3]):[0-5][0-9])?$",
        description="Horário local HH:MM quando informado, mesmo sem dia. Use string vazia se não houver horário. Nunca suponha hoje para completar uma hora sem data.",
    )
    date_uncertain: bool = Field(
        default=False,
        description="true se o dia for ambíguo, aproximado, contraditório ou pouco claro. Preencha event_date/start_at com a data provável apenas se houver base na mensagem; sem base, deixe nulo. false para dia explícito ou confirmado pelo usuário.",
    )
    end_at: datetime | None = None
    recurrence_rule: str | None = Field(
        default=None,
        description="FREQ=DAILY ou FREQ=WEEKLY;BYDAY=MO,TU,FR para vários dias semanais. Dias: MO,TU,WE,TH,FR,SA,SU. null sem repetição.",
    )
    reminder_minutes: int | None = Field(
        default=None,
        ge=1,
        le=10080,
        description="Compatibilidade com pedidos antigos de uma única antecedência. Novos pedidos usam reminder_offsets_minutes e reminder_mode.",
    )
    reminder_offsets_minutes: list[Annotated[int, Field(ge=0, le=10080, strict=True)]] = Field(
        default_factory=list,
        max_length=10,
        description="Antecedências DESTE evento em minutos: 12h, 1h e 15min = [720,60,15]. 0 significa no início, somente quando pedido. Até 10 valores. [] quando não houver nova preferência.",
    )
    reminder_mode: Literal["preserve", "standard", "custom", "exclusive"] = Field(
        default="preserve",
        description="exclusive: apenas/somente/só os avisos pedidos, sem acrescentar 5min ou início. custom: avisos pedidos mais os padrões de 5min e início. standard: restaurar padrão da categoria. preserve: manter a preferência atual ou padrão na criação.",
    )
    target_event: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Nome ATUAL do evento a localizar, alterar ou cancelar; nunca o novo nome.",
    )
    description: str | None = Field(default=None, max_length=2000)
    missing_fields: list[MissingField] = Field(default_factory=list, max_length=5)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def normalize_reminder_preference(self):
        self.reminder_offsets_minutes = sorted(set(self.reminder_offsets_minutes), reverse=True)
        if self.reminder_mode == "preserve" and (
            self.reminder_offsets_minutes or self.reminder_minutes is not None
        ):
            self.reminder_mode = "custom"
        if self.reminder_mode == "standard" and (
            self.reminder_offsets_minutes or self.reminder_minutes is not None
        ):
            raise ValueError("Standard reminders cannot also specify custom offsets")
        return self

    @field_validator("start_at")
    @classmethod
    def start_time_must_have_timezone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("start_at must include a timezone offset")
        return value

    @field_validator("end_at")
    @classmethod
    def end_time_must_have_timezone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("end_at must include a timezone offset")
        return value

    @model_validator(mode="after")
    def event_end_must_follow_start(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self

    @field_validator("recurrence_rule")
    @classmethod
    def recurrence_must_be_supported_rule(cls, value):
        if value is not None and not re.fullmatch(
            r"FREQ=DAILY|FREQ=WEEKLY;BYDAY=(?:MO|TU|WE|TH|FR|SA|SU)(?:,(?:MO|TU|WE|TH|FR|SA|SU)){0,6}",
            value,
        ):
            raise ValueError("Only daily or weekly recurrence on selected weekdays is supported")
        if value and value.startswith("FREQ=WEEKLY"):
            days = set(value.split("BYDAY=")[1].split(","))
            value = "FREQ=WEEKLY;BYDAY=" + ",".join(
                day for day in ("MO", "TU", "WE", "TH", "FR", "SA", "SU") if day in days
            )
        return value


class ParsedIntent(EventIntent):
    events: list[EventIntent] = Field(
        default_factory=list,
        max_length=10,
        description="Apenas para create_events: lista COMPLETA de até 10 eventos a criar, cada um com intent=create_event e confidence. Uma série semanal com vários dias é um único evento.",
    )

    @model_validator(mode="after")
    def validate_batch(self):
        if self.intent == "create_events":
            if not self.events or any(event.intent != "create_event" for event in self.events):
                raise ValueError("A creation batch must contain only creation requests")
        elif self.events:
            raise ValueError("Events are only allowed in a creation batch")
        return self
