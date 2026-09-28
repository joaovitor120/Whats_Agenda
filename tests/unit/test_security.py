from app.core.security import decrypt_token, encrypt_token, hash_state


def test_token_encryption_roundtrip_and_state_hash():
    encrypted = encrypt_token("sensitive-token")
    assert encrypted != "sensitive-token"
    assert decrypt_token(encrypted) == "sensitive-token"
    assert hash_state("state") != "state"
