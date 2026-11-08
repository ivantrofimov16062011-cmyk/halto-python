"""Halto: the safety gateway for AI agents. Check every risky action before your agent takes it."""

__version__ = "0.1.0"

from .client import AsyncHalto, Halto, Session  # noqa: E402
from .errors import (  # noqa: E402
    HaltoApprovalPendingError,
    HaltoApprovalRejectedError,
    HaltoAuthError,
    HaltoBlockedError,
    HaltoError,
    HaltoRateLimitError,
    HaltoUnavailableError,
    HaltoValidationError,
)
from .types import ApprovalStatus, CheckResult, CleanResult, ContentSource, ProtectResult, Risk  # noqa: E402

__all__ = [
    "__version__",
    "Halto",
    "AsyncHalto",
    "Session",
    "CheckResult",
    "CleanResult",
    "ProtectResult",
    "ApprovalStatus",
    "ContentSource",
    "Risk",
    "HaltoError",
    "HaltoBlockedError",
    "HaltoApprovalPendingError",
    "HaltoApprovalRejectedError",
    "HaltoAuthError",
    "HaltoRateLimitError",
    "HaltoValidationError",
    "HaltoUnavailableError",
]
