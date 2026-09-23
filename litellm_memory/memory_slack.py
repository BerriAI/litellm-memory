"""Slack adapter with private DM / company-public channel boundaries."""

import asyncio
import logging
import time

from .archive import Reader
from .slack import Slack

log = logging.getLogger(__name__)


class MemorySlack(Slack):
    def __init__(self, settings, store, archive):
        super().__init__(settings, store)
        self.archive = archive
        self.model_settings = settings
        self.member_checks = {}

    def eligible(self, user, user_id):
        return bool(user.get('id') == user_id and not user.get('deleted') and not user.get('is_bot')
                    and not user.get('is_restricted') and not user.get('is_ultra_restricted')
                    and user.get('team_id') == self.settings.slack_team_id
                    and user.get('profile', {}).get('email', '').casefold().rsplit('@', 1)[-1]
                    == self.settings.allowed_email_domain.casefold())

    def explicitly_ineligible(self, user):
        # user_change profile payloads may omit email. Missing event fields
        # invalidate the cache; they are not evidence that the member left.
        email = user.get('profile', {}).get('email')
        return bool(user.get('deleted') or user.get('is_bot') or user.get('is_restricted')
                    or user.get('is_ultra_restricted')
                    or (user.get('team_id') and user['team_id'] != self.settings.slack_team_id)
                    or (email is not None and email.casefold().rsplit('@', 1)[-1]
                        != self.settings.allowed_email_domain.casefold()))

    async def verify_member(self, owner, *, fresh=False):
        team, user_id = owner.split(':', 1)
        if team != self.settings.slack_team_id:
            return False
        if not await self.token():
            # Before Slack installation, no real OIDC member can sign in. This
            # also lets isolated API fixtures run without external credentials.
            if self.settings.slack_client_id:
                raise PermissionError('Reconnect the workspace Slack app before continuing.')
            return True
        if not fresh and self.member_checks.get(owner, 0) > time.monotonic():
            return True
        try:
            user = (await self.api('users.info', {'user': user_id})).get('user', {})
        except Exception:
            raise PermissionError('Slack membership could not be verified. Please try again shortly.') from None
        if not self.eligible(user, user_id):
            self.member_checks.pop(owner, None)
            if await self.archive.member(owner):
                await self.archive.leave(owner)
            return False
        if len(self.member_checks) > 10000:
            self.member_checks = {k: v for k, v in self.member_checks.items() if v > time.monotonic()}
        self.member_checks[owner] = time.monotonic() + 300
        if await self.archive.member(owner):
            profile = user.get('profile', {})
            await self.archive.enroll(owner, {'name': profile.get('display_name') or profile.get('real_name')
                                            or user.get('real_name') or user.get('name') or user_id,
                                            'avatar': profile.get('image_192') or profile.get('image_72') or '',
                                            'email': profile.get('email', '')})
        return True

    async def reconcile_members(self):
        """Expire positive checks even when someone no longer visits LiteLLM Memory."""
        slots = asyncio.Semaphore(4)
        async def check(owner):
            async with slots:
                try:
                    await self.verify_member(owner)
                except Exception as error:
                    log.warning('Membership refresh unavailable (%s)', type(error).__name__)
        while True:
            try:
                rows = await (await self.archive.db.execute('SELECT id FROM members WHERE active=1')).fetchall()
                await asyncio.gather(*(check(row['id']) for row in rows))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning('Membership refresh unavailable (%s)', type(error).__name__)
            await asyncio.sleep(60)

    async def prepare_payload(self, payload):
        return {**payload, "model": self.model_settings.slack_model or self.model_settings.litellm_model,
                "model_config_revision": self.model_settings.model_config_revision}

    async def permitted(self, payload):
        owner = f"{payload['team']}:{payload['user']}"
        if not await self.archive.member(owner):
            return False
        if self.settings.registration_mode == "owner_only" and payload["user"] != self.settings.slack_owner_user_id:
            return False
        if not await self.verify_member(owner, fresh=True):
            return False
        return await super().permitted(payload)

    async def reader(self, payload):
        if not await self.permitted(payload):
            raise PermissionError("This Slack conversation is not authorized.")
        owner = f"{payload['team']}:{payload['user']}"
        # Read fresh conversation metadata. A C/D prefix or message-provided flag alone
        # cannot grant private access. A named private channel can gain future members.
        data = await self.api("conversations.info", {"channel": payload["channel"]})
        info = data.get("channel", {})
        if info.get("is_im") and info.get("user") == payload["user"]:
            return Reader(owner, "owner")
        # Authorize the requester and the owner-approved conversation, not its
        # other participants. Every channel/MPIM uses shared publications only,
        # including channels with apps, unenrolled people or external members.
        return Reader(owner, "public")
