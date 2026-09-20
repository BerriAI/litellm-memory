"""Slack adapter with private DM / company-public channel boundaries."""

import time

from .archive import Reader
from .slack import Slack


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
        return True

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
        if info.get("is_ext_shared") or info.get("is_org_shared") or info.get("is_shared"):
            raise PermissionError("Open the dashboard to ask in a conversation with external participants.")
        # Confirm every channel participant is a current, non-guest workspace member.
        cursor = ""
        while True:
            page = await self.api("conversations.members", {"channel": payload["channel"], "cursor": cursor, "limit": 200})
            for user_id in page.get("members", []):
                if user_id == await self.bot_id():
                    continue
                user = (await self.api("users.info", {"user": user_id})).get("user", {})
                if (not self.eligible(user, user_id)
                        or not await self.archive.member(f"{payload['team']}:{user_id}")):
                    raise PermissionError("Open the dashboard for this question.")
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        return Reader(owner, "public")
