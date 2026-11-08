"""Errors raised by the Halto SDK. Same names and meanings as the TypeScript SDK."""
from __future__ import annotations

from typing import Any, Optional


class HaltoError(Exception):
    def __init__(self, message: str, code: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class HaltoBlockedError(HaltoError):
    """Halto decided the action must not happen."""

    def __init__(self, reason: str, action_id: Optional[str]) -> None:
        super().__init__(f"Blocked by Halto: {reason}", "blocked")
        self.reason = reason
        self.action_id = action_id


class HaltoApprovalPendingError(HaltoError):
    """A human must approve first. Retry the same check after approval (or use wait_for_approval)."""

    def __init__(self, approval_id: str, reason: str, expires_at: Optional[str] = None) -> None:
        super().__init__(f"Waiting for human approval ({approval_id}): {reason}", "approval_pending")
        self.approval_id = approval_id
        self.reason = reason
        self.expires_at = expires_at


class HaltoApprovalRejectedError(HaltoError):
    """A human rejected the action, or nobody answered in time."""

    def __init__(self, approval_id: str, approval_status: str) -> None:
        super().__init__(f"Approval {approval_status}: the action must not run", f"approval_{approval_status}")
        self.approval_id = approval_id
        self.approval_status = approval_status


class HaltoAuthError(HaltoError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "unauthorized", 401)


class HaltoRateLimitError(HaltoError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(f"Rate limited, retry after {retry_after_seconds}s", "rate_limited", 429)
        self.retry_after_seconds = retry_after_seconds


class HaltoValidationError(HaltoError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "invalid_request", 400)


class HaltoUnavailableError(HaltoError):
    """Halto could not be reached. With fail_mode "closed" (default) the action must not run."""

    def __init__(self, message: str, cause: Any = None) -> None:
        super().__init__(message, "unavailable")
        self.__cause__ = cause if isinstance(cause, BaseException) else None
