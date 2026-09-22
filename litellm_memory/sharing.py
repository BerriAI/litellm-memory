"""Owner-only sharing review using existing classification results."""

import hashlib

from .crypto import encode
from .processing import criteria_for


class Sharing:
    def __init__(self, archive, settings):
        self.archive, self.settings = archive, settings

    async def matches(self, owner, record_id):
        archive = self.archive
        async with archive.owner_lock(owner):
            row = await archive.row(record_id)
            if not row or row['owner'] != owner or row['deleted']:
                raise LookupError('Memory not found')
            if row['status'] != 'ready':
                return {'rules': [], 'notice': 'Privacy checks are not complete for this memory.'}
            rules = await archive.rules(owner)
            criteria = criteria_for(rules, self.settings.jev_model)
            labels = await archive.cached_labels(row, criteria, require_agent=False)
            matches = [{'id': rule['id'], 'instruction': rule['instruction']} for rule in rules
                       if labels.get('rule_' + rule['id'], {}).get('noul', 0) >= self.settings.jev_private_threshold]
            return {'rules': matches, 'notice': '' if matches else 'No current private topic matches this memory.'}

    async def _review(self, owner):
        archive = self.archive
        member = await archive.member(owner)
        if not member:
            raise PermissionError('Account is unavailable')
        if not archive.sharing_enabled:
            raise PermissionError('Company sharing is off. Your workspace admin can enable it in Settings.')
        if not member['agent_enabled']:
            raise PermissionError('Resume your personal agent in Connections before sharing existing memories.')
        rows = await (await archive.db.execute("""SELECT * FROM records
            WHERE owner=? AND deleted=0 AND visibility='private' ORDER BY id""", (owner,))).fetchall()
        counts = dict(eligible=0, filtered=0, processing=0, failed=0, excluded=0)
        eligible, revisions = [], []
        for row in rows:
            row = dict(row)
            revisions.append([row['id'], row['updated'], row['status'], row['content_hash']])
            if row['status'] == 'failed':
                counts['failed'] += 1
            elif row['status'] != 'ready':
                counts['processing'] += 1
            else:
                payload = await archive.decode(row)
                if payload.get('privacy_blocked') is True:
                    counts['filtered'] += 1
                elif (payload.get('privacy_blocked') is not False or not payload.get('summary')
                      or not await archive.publication_allowed({**row, 'intent': 'public'}, payload)):
                    counts['excluded'] += 1
                else:
                    eligible.append(row['id'])
                    counts['eligible'] += 1
        # Bind confirmation to this owner, policy and exact reviewed set. A new
        # memory or policy change requires another preview, never broader access.
        revision = hashlib.sha256(encode([owner, member['policy_version'], revisions])).hexdigest()
        return {'revision': revision, 'counts': counts}, eligible

    async def review(self, owner):
        async with self.archive.owner_lock(owner):
            return (await self._review(owner))[0]

    async def share_existing(self, owner, revision):
        archive = self.archive
        async with archive.owner_lock(owner):
            review, ids = await self._review(owner)
            if review['revision'] != revision:
                raise ValueError('Your memories or sharing rules changed. Review existing memories again before sharing.')
            if ids:
                # Queue only reviewed, eligible records. The worker reuses cached
                # classifications and rechecks current policy before publication.
                await archive.db.executemany("""UPDATE records SET intent='public', status='pending',
                    attempts=0,next_attempt=0,error=NULL WHERE id=? AND owner=? AND deleted=0""",
                    [(record_id, owner) for record_id in ids])
                await archive.db.execute('UPDATE members SET policy_version=policy_version+1 WHERE id=?', (owner,))
                await archive.db.commit()
            return {'queued': len(ids), 'counts': review['counts']}
