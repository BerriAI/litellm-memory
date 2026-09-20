from dataclasses import dataclass

from openai import APIConnectionError, AuthenticationError, PermissionDeniedError, RateLimitError


@dataclass(frozen=True)
class Failure:
    message: str
    retryable: bool = False


def explain_error(error: Exception) -> Failure:
    # Inspect provider details locally, but never send raw upstream bodies or credentials to users.
    if isinstance(error, RateLimitError):
        detail = str(error).lower()
        if any(marker in detail for marker in (
            "no credits remaining", "insufficient_quota", "exceeded your current quota",
            "credit balance is too low", "exceeded your budget",
        )):
            return Failure("The configured model route reports no API credits. Moe needs to check that "
                           "route’s billing or select a funded route before I can answer.")
        return Failure("The model provider is rate-limiting requests. Please try again shortly.", True)
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return Failure("litemem’s model credentials were rejected. Moe needs to check the gateway configuration.")
    if isinstance(error, TimeoutError):
        return Failure("This answer took too long. Try a more specific question or ask about one topic at a time.")
    if isinstance(error, APIConnectionError):
        return Failure("litemem couldn’t connect to the model provider. Please try again in a moment.", True)
    return Failure("litemem couldn’t finish this request. Please try again in a moment.", True)
