"""Portable encrypted exports using libsodium's authenticated streaming format."""

import json

from nacl import bindings, pwhash, utils

from .crypto import b64, encode, unb64

FORMAT = 'litellm-memory-transfer'
SUPPORTED_FORMATS = (FORMAT, 'litemem-transfer')
MAX_LINE = 1_000_000


def derive(passphrase, salt):
    if len(passphrase) < 12 or len(passphrase) > 1024:
        raise ValueError('Use an export passphrase between 12 and 1,024 characters.')
    return pwhash.argon2id.kdf(bindings.crypto_secretstream_xchacha20poly1305_KEYBYTES,
                              passphrase.encode(), salt, opslimit=pwhash.argon2id.OPSLIMIT_INTERACTIVE,
                              memlimit=pwhash.argon2id.MEMLIMIT_INTERACTIVE)


class ExportStream:
    def __init__(self, passphrase, owner):
        salt = utils.random(pwhash.argon2id.SALTBYTES)
        key = derive(passphrase, salt)
        self.state = bindings.crypto_secretstream_xchacha20poly1305_state()
        header = bindings.crypto_secretstream_xchacha20poly1305_init_push(self.state, key)
        self.header = {'format': FORMAT, 'version': 1, 'owner': owner, 'kdf': 'argon2id-interactive',
                       'cipher': 'secretstream-xchacha20poly1305', 'salt': b64(salt), 'header': b64(header)}
        self.aad = encode(self.header)
        self.count = 0

    def line(self, value):
        ciphertext = bindings.crypto_secretstream_xchacha20poly1305_push(self.state, encode(value), ad=self.aad)
        self.count += 1
        return b64(ciphertext) + '\n'

    def finish(self):
        final = bindings.crypto_secretstream_xchacha20poly1305_push(self.state, encode({'records': self.count}),
            ad=self.aad, tag=bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL)
        return b64(final) + '\n'


def read_export(file, passphrase):
    """Yield authenticated records; exhaust the iterator to verify completeness."""
    raw = file.readline(MAX_LINE + 1)
    header = json.loads(raw)
    if (len(raw) > MAX_LINE or not isinstance(header, dict) or header.get('format') not in SUPPORTED_FORMATS
            or header.get('version') != 1
            or header.get('kdf') != 'argon2id-interactive' or header.get('cipher') != 'secretstream-xchacha20poly1305'):
        raise ValueError('Unsupported memory export format.')
    key = derive(passphrase, unb64(header['salt']))
    state = bindings.crypto_secretstream_xchacha20poly1305_state()
    bindings.crypto_secretstream_xchacha20poly1305_init_pull(state, unb64(header['header']), key)
    count = 0
    while True:
        line = file.readline(MAX_LINE + 1)
        if not line or len(line) > MAX_LINE:
            raise ValueError('The export is incomplete or has an oversized record.')
        data, tag = bindings.crypto_secretstream_xchacha20poly1305_pull(state, unb64(line.strip()), ad=encode(header))
        value = json.loads(data)
        if tag == bindings.crypto_secretstream_xchacha20poly1305_TAG_FINAL:
            if value != {'records': count} or file.read(1):
                raise ValueError('The export was truncated or has unexpected trailing data.')
            return
        if (tag != bindings.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE or not isinstance(value, dict)
                or value.get('owner') != header['owner']):
            raise ValueError('Export record identity is inconsistent.')
        count += 1
        yield value
