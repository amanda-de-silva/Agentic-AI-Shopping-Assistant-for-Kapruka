"""Application-level exception types."""
from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, *, code: str = "app_error", status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class MCPToolError(AppError):
    """An MCP tool executed but returned an error result.

    This is deliberately different from a transport/timeout failure. A tool-level
    error normally means the upstream system understood the request and rejected
    it, so callers can safely mark the application attempt as failed instead of
    pretending the external order result is unknown.
    """

    def __init__(
        self,
        tool_name: str,
        message: str,
        *,
        remote_code: str | None = None,
        raw_message: str | None = None,
    ):
        display_message = message.strip() or "unknown MCP tool error"
        super().__init__(
            f"MCP tool '{tool_name}' failed: {display_message}",
            code="mcp_tool_error",
            status_code=502,
        )
        self.tool_name = tool_name
        self.remote_code = remote_code
        self.raw_message = raw_message or display_message

    @property
    def detail(self) -> str:
        """Return the useful upstream error without the local wrapper."""
        message = self.raw_message.strip()
        if self.remote_code:
            return f"[{self.remote_code}] {message}"
        return message


class RateLimitExceeded(AppError):
    def __init__(self, scope: str, retry_after_seconds: float):
        super().__init__(
            f"Rate limit exceeded for '{scope}', retry after {retry_after_seconds:.1f}s",
            code="rate_limit_exceeded",
            status_code=429,
        )
        self.scope = scope
        self.retry_after_seconds = retry_after_seconds


class CartError(AppError):
    def __init__(self, message: str):
        super().__init__(message, code="cart_error", status_code=400)


class CheckoutError(AppError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "checkout_error"):
        super().__init__(message, code=code, status_code=status_code)


class ProfileNotFoundError(AppError):
    def __init__(self, profile_id: str):
        super().__init__(
            f"Recipient profile '{profile_id}' not found",
            code="profile_not_found",
            status_code=404,
        )


class SafetyViolationError(AppError):
    def __init__(self, message: str):
        super().__init__(
            message,
            code="safety_violation",
            status_code=409,
        )