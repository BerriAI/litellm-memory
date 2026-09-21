import json

import pytest
from nacl.exceptions import CryptoError

from moegent.crypto import KeyVault, decrypt_blob, encrypt_blob, new_key


def test_records_are_authenticated_and_not_readable_with_another_owner_key():
    key = new_key()
    value = {"id": "record", "content": "private work, not a log entry"}
    blob = encrypt_blob(value, key)
    assert "private" not in json.dumps(blob)
    assert decrypt_blob(blob, key) == value
    with pytest.raises(CryptoError):
        decrypt_blob(blob, new_key())
    first = "B" if blob["encryptedData"][0] == "A" else "A"
    blob["encryptedData"] = first + blob["encryptedData"][1:]
    with pytest.raises(CryptoError):
        decrypt_blob(blob, key)


def test_wrapped_keys_are_bound_to_owner_and_survive_restart(tmp_path):
    key = new_key()
    vault = KeyVault(tmp_path)
    wrapped = vault.wrap("owner-a", key)
    assert KeyVault(tmp_path).unwrap("owner-a", wrapped) == key
    with pytest.raises(ValueError):
        vault.unwrap("owner-b", wrapped)
    text = vault.seal_operational({"question": "private question"})
    assert "private question" not in text
    assert vault.open_operational(text)["question"] == "private question"
