"""Shared by the framework wrappers (LangChain, CrewAI).

What a wrapped tool does on every call:

1. asks Halto (``/v1/check``) with the model's arguments and the wrapper's session id;
2. runs the tool only on "allow", with ``final_params``: the text the owner approved (Halto Fix) and the real values
   behind data-guard placeholders, never the model's own arguments;
3. sends the tool's answer through Halto's data guard (``/v1/clean`` with ``protect``) before the model sees it:
   personal and confidential values become placeholders and hidden commands are removed. For tools that read outside
   content (the default) the same session id goes along, so the session counts as "has read outside content" and the
   next risky action is checked as such (prompt-injection protection).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Optional

from ..client import Halto
from ..errors import HaltoApprovalPendingError, HaltoApprovalRejectedError, HaltoBlockedError, HaltoError
from ..types import CheckResult

#: /v1/clean takes at most 200,000 characters (and 512 KB): longer tool answers are cut before Halto reads them.
MAX_OUTPUT_CHARS = 100_000


def new_session_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def default_session_id(halto: Halto) -> str:
    """One session per Halto client: every tool wrapped with the same client shares it unless given its own, so what
    one tool read taints the checks of the others."""
    sid = getattr(halto, "_integrations_session_id", None)
    if not sid:
        sid = new_session_id()
        setattr(halto, "_integrations_session_id", sid)
    return sid


def guess_source_type(name: str) -> str:
    n = name.lower()
    for words, kind in (
        (("mail", "inbox", "message"), "email"),
        (("web", "url", "page", "browse", "scrape", "fetch", "search"), "web"),
        (("doc", "file", "pdf", "attachment"), "document"),
        (("form", "submission"), "form"),
        (("review",), "review"),
    ):
        if any(w in n for w in words):
            return kind
    return "other"


def output_text(value: Any) -> str:
    """The tool's answer as the text the model would see."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", "replace")
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n[… cut by Halto: the tool's answer was too long]"
    return text


def guarded_call(
    halto: Halto,
    namespace: str,
    name: str,
    args: Dict[str, Any],
    run: Callable[[Dict[str, Any]], Any],
    *,
    wait_for_approval: Any,
    raise_on_block: bool,
    session_id: Optional[str] = None,
    protect_output: bool = True,
    reads_outside_content: bool = True,
) -> Any:
    """Check with Halto, run the tool with ``final_params``, and protect its answer. By default a stop is returned to
    the model as text, so the agent can explain it to the user instead of crashing; set raise_on_block=True to raise
    instead."""
    sid = session_id or default_session_id(halto)

    def runner(r: CheckResult) -> Any:
        # final_params, not the model's arguments: an owner's "approve with Halto's changes" and the real values
        # behind placeholders only exist there.
        return run(r.final_params if r.final_params is not None else args)

    try:
        out = halto.guard(namespace, name, args, runner, wait_for_approval=wait_for_approval, session_id=sid)
    except HaltoBlockedError as err:
        if raise_on_block:
            raise
        return f"Halto blocked this action: {err.reason}. Do not try it again; tell the user why it was stopped."
    except HaltoApprovalPendingError as err:
        if raise_on_block:
            raise
        return f"This action needs a human's approval first ({err.reason}). Approval id: {err.approval_id}. Tell the user it is waiting for approval."
    except HaltoApprovalRejectedError as err:
        if raise_on_block:
            raise
        return f"A human did not approve this action ({err.approval_status}). Do not perform it."

    if not protect_output or out is None:
        return out
    try:
        protected = halto.protect(
            output_text(out),
            type=guess_source_type(name) if reads_outside_content else "api",
            origin=f"{namespace}.{name}",
            session_id=sid if reads_outside_content else None,
        )
    except HaltoError as err:
        # Never fall back to the unprotected answer: the model would see the real data.
        if raise_on_block:
            raise
        return f"The tool ran, but Halto could not check its answer ({err}), so it is withheld. Tell the user Halto is unavailable; do not run the tool again."
    return protected.content
