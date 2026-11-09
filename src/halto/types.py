"""Plain data types returned by the SDK."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from .errors import HaltoUnavailableError

Decision = Literal["allow", "block", "pending"]


@dataclass
class ContentSource:
    """Untrusted content the agent read (an email, a web page…): Halto checks it for hidden instructions."""

    content: str = ""
    type: str = "other"
    origin: Optional[str] = None
    trusted: bool = False

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "content": self.content}
        if self.origin:
            out["origin"] = self.origin
        if self.trusted:
            out["trusted"] = True
        return out


@dataclass
class Risk:
    score: int
    level: str


@dataclass
class CheckResult:
    decision: Decision
    reason: str
    action_id: Optional[str]
    risk: Risk
    category: str
    session_tainted: bool
    policy_version: int
    approval_id: Optional[str] = None
    approval_expires_at: Optional[str] = None
    #: Halto Fix: {"status": "suggested" | "approved", "fields": [...], "issues": [...]} when Halto proposed a corrected text.
    fix: Optional[Dict[str, Any]] = None
    #: The parameters to send to the tool instead of your own: the text the owner approved (Halto Fix) and the real
    #: values behind data-guard placeholders. Halto answers every "allow" with them; inside guard() they are always set
    #: (your own params when Halto sent none). Never hand them back to your model.
    final_params: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ApprovalStatus:
    id: str
    status: Literal["pending", "approved", "rejected", "expired"]
    reason: str
    decided_by: Optional[str]
    channel: Optional[str]
    expires_at: Optional[str]


DECISIONS = ("allow", "pending", "block")


def parse_check(r: Any) -> CheckResult:
    # A proxy page, an empty body or a broken deployment answering 200 is not a decision: never act on it.
    if not isinstance(r, dict) or r.get("decision") not in DECISIONS:
        raise HaltoUnavailableError("Halto answered without a valid decision (allow, pending or block) — not running the action")
    risk = r.get("risk") or {}
    return CheckResult(
        decision=r["decision"],
        reason=r.get("reason", ""),
        action_id=r.get("action_id"),
        approval_id=r.get("approval_id"),
        approval_expires_at=r.get("approval_expires_at"),
        risk=Risk(score=int(risk.get("score", 0)), level=str(risk.get("level", "low"))),
        category=r.get("category", "other"),
        session_tainted=bool(r.get("session_tainted")),
        policy_version=int(r.get("policy_version", 0)),
        fix=r.get("fix"),
        final_params=r.get("final_params"),
        raw=r,
    )


def parse_approval(r: Dict[str, Any]) -> ApprovalStatus:
    return ApprovalStatus(
        id=r["id"],
        status=r["status"],
        reason=r.get("reason", ""),
        decided_by=r.get("decided_by"),
        channel=r.get("channel"),
        expires_at=r.get("expires_at"),
    )




def sources_json(sources: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
    if not sources:
        return None
    return [s.to_json() if isinstance(s, ContentSource) else dict(s) for s in sources]


@dataclass
class CleanResult:
    """Halto Fix: the text to give your model, with hidden commands and secrets removed."""

    content: str
    changed: bool
    removed: List[Dict[str, Any]] = field(default_factory=list)
    risk_score: int = 0


@dataclass
class ProtectResult(CleanResult):
    """Data guard: the text for your model, with personal and confidential values replaced by placeholders."""

    protected: Dict[str, int] = field(default_factory=dict)
    protection: str = "on"


def parse_protect(r: Dict[str, Any]) -> ProtectResult:
    c = parse_clean(r)
    return ProtectResult(content=c.content, changed=c.changed, removed=c.removed, risk_score=c.risk_score, protected=dict(r.get("protected") or {}), protection=str(r.get("protection") or "on"))


def parse_clean(r: Dict[str, Any]) -> CleanResult:
    return CleanResult(content=r.get("content", ""), changed=bool(r.get("changed")), removed=list(r.get("removed") or []), risk_score=int(r.get("risk_score") or 0))
