"""Explicit portable export/restore, authenticated using the connector's Slack OAuth."""

import argparse
import getpass
import os
from pathlib import Path

import httpx
from nacl.exceptions import CryptoError

from .archive import WorkInput
from .connector import LocalCapture
from .transfer import read_export


def restore(path, passphrase, connection, *, allow_owner_remap=False, transport=None):
    # Verify the complete authenticated stream before any upload. A truncated
    # file must not produce a silently partial restoration.
    total = 0
    with path.open() as file:
        for record in read_export(file, passphrase):
            if record['owner'] != connection['owner'] and not allow_owner_remap:
                raise ValueError('Export belongs to another Slack identity; review --allow-owner-remap explicitly.')
            WorkInput.model_validate(record['original'])
            total += 1
    counts = {'verified': total, 'restored': 0, 'duplicates': 0, 'excluded': 0}
    with path.open() as file, httpx.Client(base_url=connection['url'], transport=transport, timeout=60,
            headers={'Authorization': 'Bearer ' + connection['access_token']}) as client:
        for record in read_export(file, passphrase):
            body = WorkInput.model_validate(record['original']).model_copy(update={'historical': True})
            response = client.post('/api/memories', json=body.model_dump(mode='json'))
            if response.is_success:
                counts['duplicates' if response.json().get('duplicate') else 'restored'] += 1
            elif response.status_code in {400, 409}:
                # Existing conflicting/tombstoned IDs are never overwritten.
                counts['excluded'] += 1
            else:
                raise ValueError('Restore interrupted. Reconnect or wait, then rerun the same import safely.')
    return counts


def main():
    parser = argparse.ArgumentParser(description='Export/restore your own work through the litemem API.')
    parser.add_argument('--directory', type=Path, default=Path.home() / '.local/share/litemem')
    parser.add_argument('command', choices=['export', 'restore'])
    parser.add_argument('file', type=Path)
    parser.add_argument('--allow-owner-remap', action='store_true',
                        help='Deliberately import an export from a different Slack identity into the connected account.')
    args = parser.parse_args()
    local = LocalCapture(args.directory)
    try:
        local.flush(limit=0)  # Refreshes OAuth without uploading queued captures.
        connection = local.get('connection')
        if not connection or local.get('status') not in {'connected', 'outbox_full'}:
            raise ValueError('Connect to your destination litemem with litemem-connector first.')
        passphrase = getpass.getpass('Export passphrase (not your Slack password): ')
        if args.command == 'export':
            if passphrase != getpass.getpass('Confirm export passphrase: '):
                raise ValueError('The passphrases do not match.')
            # Exclusive creation avoids replacing an existing backup by mistake.
            fd = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, 'wb') as output, httpx.Client(timeout=120) as client:
                    with client.stream('POST', connection['url'] + '/api/export',
                            headers={'Authorization': 'Bearer ' + connection['access_token']},
                            json={'encrypted': True, 'passphrase': passphrase}) as response:
                        if not response.is_success:
                            raise ValueError('Export failed. Check the connection and passphrase length.')
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                with args.file.open() as file:
                    count = sum(1 for _ in read_export(file, passphrase))
                print(f'Export verified: {count} records. Keep the passphrase separately from this file.')
            except BaseException:
                args.file.unlink(missing_ok=True)  # Only the just-created incomplete file.
                raise
        else:
            counts = restore(args.file, passphrase, connection, allow_owner_remap=args.allow_owner_remap)
            print('Restore completed: ' + ', '.join(f'{key}={value}' for key, value in counts.items()))
            print('Imported work is private. Derived views and embeddings rebuild through the destination gateway.')
    except (ValueError, KeyError, OSError, CryptoError, httpx.HTTPError):
        print('Transfer could not complete. Check the file, passphrase, account and connection. No existing record was overwritten.')
        raise SystemExit(1) from None
    finally:
        local.close()


if __name__ == '__main__':
    main()
