from app.integrations.evolution.normalizer import normalize_webhook


def test_normalizes_v2_text_payload():
    payload = {
        "event": "messages.upsert",
        "data": {
            "messages": [
                {
                    "key": {
                        "id": "abc",
                        "remoteJid": "5511999999999@s.whatsapp.net",
                        "fromMe": False,
                    },
                    "pushName": "Ana",
                    "messageTimestamp": 123,
                    "message": {"conversation": "Oi"},
                }
            ]
        },
    }
    result = normalize_webhook(payload)
    assert len(result) == 1
    assert result[0].message_id == "abc"
    assert result[0].phone == "5511999999999"
    assert result[0].text == "Oi"


def test_ignores_from_me_groups_and_non_message_events():
    payload = {
        "event": "messages.upsert",
        "data": {
            "messages": [
                {
                    "key": {"id": "a", "remoteJid": "5511@s.whatsapp.net", "fromMe": True},
                    "message": {"conversation": "x"},
                },
                {"key": {"id": "b", "remoteJid": "120@g.us"}, "message": {"conversation": "x"}},
            ]
        },
    }
    assert normalize_webhook(payload) == []
    assert normalize_webhook({"event": "CONNECTION_UPDATE", "data": {}}) == []
