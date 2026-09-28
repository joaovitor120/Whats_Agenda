from pydantic import BaseModel


class NormalizedMessage(BaseModel):
    message_id: str
    remote_jid: str
    phone: str
    push_name: str | None = None
    timestamp: int | None = None
    message_type: str
    text: str | None = None
    media: dict | None = None
    from_me: bool = False
