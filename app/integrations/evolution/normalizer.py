import re

from app.core.config import settings
from app.integrations.evolution.schemas import NormalizedMessage


def normalize_webhook(payload: dict) -> list[NormalizedMessage]:
    if not isinstance(payload, dict):
        raise ValueError("Webhook must be an object")
    event = str(payload.get("event", "")).lower().replace("_", ".")
    if event != "messages.upsert":
        return []
    data = payload.get("data")
    messages = data.get("messages", [data]) if isinstance(data, dict) else data
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list) or len(messages) > 20:
        raise ValueError("Invalid message batch")
    result = []
    for item in messages:
        if not isinstance(item, dict):
            raise ValueError("Invalid message")
        key, body = item.get("key", {}), item.get("message", {})
        if not isinstance(key, dict) or not isinstance(body, dict):
            raise ValueError("Invalid message envelope")
        jid = key.get("remoteJid", "")
        if key.get("fromMe", False) is not False:
            continue
        phone_jid = jid
        if isinstance(jid, str) and re.fullmatch(r"[0-9]{1,32}@lid", jid):
            # Baileys supplies the alternate phone identity in the authenticated envelope.
            # Never interpret the digits of a LID as a phone or use a group participant.
            phone_jid = key.get("remoteJidAlt", "")
        if not isinstance(phone_jid, str) or not re.fullmatch(
            r"[1-9][0-9]{7,14}@s\.whatsapp\.net", phone_jid
        ):
            continue
        if not settings.whatsapp_allowed(phone_jid.split("@")[0]):
            continue
        message_id = key.get("id")
        if not isinstance(message_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_.:-]{1,160}", message_id
        ):
            raise ValueError("Invalid message ID")
        extended = body.get("extendedTextMessage", {})
        text = body.get("conversation") or (
            extended.get("text") if isinstance(extended, dict) else None
        )
        if text is not None and (not isinstance(text, str) or len(text) > settings.max_text_chars):
            raise ValueError("Invalid or oversized text")
        audio = body.get("audioMessage")
        media = None
        if audio is not None:
            if not isinstance(audio, dict):
                raise ValueError("Invalid audio envelope")
            seconds = audio.get("seconds")
            if (
                not isinstance(seconds, (int, float))
                or isinstance(seconds, bool)
                or not 0 < seconds <= settings.max_audio_seconds
            ):
                raise ValueError("Invalid audio duration")
            mime = str(audio.get("mimetype", "")).split(";")[0]
            if mime not in {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav"}:
                raise ValueError("Unsupported audio type")
            # Fetch the stored message by key; never relay an attacker-controlled URL.
            media = {
                "key": {"id": message_id, "remoteJid": jid, "fromMe": False},
                "seconds": seconds,
                "mimetype": mime,
            }
        if not text and not media:
            continue
        timestamp = item.get("messageTimestamp")
        if timestamp is not None and (
            not isinstance(timestamp, int) or isinstance(timestamp, bool)
        ):
            raise ValueError("Invalid timestamp")
        name = item.get("pushName")
        result.append(
            NormalizedMessage(
                message_id=message_id,
                remote_jid=jid,
                phone=phone_jid.split("@")[0],
                push_name=name[:120] if isinstance(name, str) else None,
                timestamp=timestamp,
                message_type="audio" if media else "text",
                text=text,
                media=media,
            )
        )
    return result
