from dataclasses import dataclass

from openai import APIConnectionError, AuthenticationError, PermissionDeniedError, RateLimitError


@dataclass(frozen=True)
class Failure:
    message: str
    retryable: bool = False


def explain_error(error: Exception) -> Failure:
    # Inspect provider details locally, but never send raw upstream bodies or credentials to users.
    if isinstance(error, ExceptionGroup):
        # MCP transports use task groups. Keep billing/auth failures terminal
        # even when session cleanup wraps the original provider exception.
        failures = [explain_error(child) for child in error.exceptions]
        return next((failure for failure in failures if not failure.retryable), failures[0])
    if isinstance(error, RateLimitError):
        detail = str(error).lower()
        if any(marker in detail for marker in (
            "no credits remaining", "insufficient_quota", "exceeded your current quota",
            "credit balance is too low", "exceeded your budget",
        )):
            return Failure("The configured model route reports no API credits. Your workspace admin needs to check that "
                           "route’s billing or select a funded route before I can answer.")
        return Failure("The model provider is rate-limiting requests. Please try again shortly.", True)
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return Failure("LiteLLM Memory’s model credentials were rejected. Your workspace admin needs to check the gateway configuration.")
    if isinstance(error, TimeoutError):
        return Failure("This answer took too long. Try a more specific question or ask about one topic at a time.")
    if isinstance(error, APIConnectionError):
        return Failure("LiteLLM Memory couldn’t connect to the model provider. Please try again in a moment.", True)
    return Failure("LiteLLM Memory couldn’t finish this request. Please try again in a moment.", True)
