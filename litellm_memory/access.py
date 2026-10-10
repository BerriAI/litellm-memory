from dataclasses import dataclass

from fastapi import HTTPException

from .config import Settings


@dataclass(frozen=True)
class Principal:
    source: str
    user_id: str = ""
    team_id: str = ""

    @property
    def identity(self) -> str:
        return f"{self.team_id}:{self.user_id}"


class AccessPolicy:
    """Slack requester and installation-owner checks."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.allowed = {x.strip() for x in settings.allowed_slack_users.split(",") if x.strip()}

    def authorize(self, principal: Principal) -> None:
        if not principal.user_id:
            raise HTTPException(401, "Sign in with Slack to use LiteLLM Memory.")
        if principal.identity not in self.allowed:
            raise HTTPException(403, "Your account does not have access to LiteLLM Memory.")

    def owner(self, principal: Principal) -> bool:
        return bool(self.settings.slack_owner_user_id) and (
            principal.user_id == self.settings.slack_owner_user_id
            and principal.team_id == self.settings.slack_team_id
        )
