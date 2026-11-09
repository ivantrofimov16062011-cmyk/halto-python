"""Sync and async clients. Same behaviour as the TypeScript SDK (packages/sdk/src/index.ts)."""
from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar, Union
from urllib.parse import quote, urlsplit

import httpx

from . import __version__
from .errors import (
    HaltoApprovalPendingError,
    HaltoApprovalRejectedError,
    HaltoAuthError,
    HaltoBlockedError,
    HaltoError,
    HaltoRateLimitError,
    HaltoUnavailableError,
    HaltoValidationError,
)
from .types import ApprovalStatus, CheckResult, CleanResult, ContentSource, ProtectResult, parse_approval, parse_check, parse_clean, parse_protect, sources_json

T = TypeVar("T")
Source = Union[ContentSource, Dict[str, Any]]
Wait = Union[bool, float]  # True = wait up to 10 minutes; a number = wait up to that many seconds

DEFAULT_URL = "https://www.halto.tech"


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _check_base_url(raw: str) -> str:
    """The base URL without a trailing slash. Only https; plain http only for a Halto on this computer (the key travels with every request)."""
    url = str(raw).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS:
        return url
    raise HaltoError("Halto: base_url must start with https:// (plain http:// is only allowed for localhost)", "config")


def _approval_path(approval_id: str) -> str:
    return f"/v1/approvals/{quote(str(approval_id), safe='')}"


def _backoff(attempt: int) -> float:
    return min(0.2 * 2**attempt, 2.0) + random.random() * 0.1


def _wait_seconds(wait: Wait) -> float:
    return 600.0 if wait is True else float(wait)


def _clean_body(content: str, type: str, origin: Optional[str], session_id: Optional[str]) -> Dict[str, Any]:
    body: Dict[str, Any] = {"content": content, "type": type}
    if origin:
        body["origin"] = origin
    if session_id:
        body["session_id"] = session_id
    return body


def _fill_final_params(r: CheckResult, params: Optional[Dict[str, Any]]) -> None:
    """Inside guard() final_params is always what the tool should get: Halto's, or the caller's own when Halto sent none."""
    if r.final_params is None:
        r.final_params = dict(params or {})


def _check_body(
    tool: str,
    action: str,
    params: Optional[Dict[str, Any]],
    session_id: Optional[str],
    cost: Optional[float],
    currency: Optional[str],
    category: Optional[str],
    content_sources: Optional[List[Source]],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"tool": tool, "action": action, "params": params or {}}
    for key, value in (("session_id", session_id), ("cost", cost), ("currency", currency), ("category", category)):
        if value is not None:
            body[key] = value
    sources = sources_json(content_sources)
    if sources:
        body["content_sources"] = sources
    return body


def _response_error(status: int, data: Dict[str, Any], headers: httpx.Headers, attempt: int, retries: int) -> Tuple[Optional[float], Optional[HaltoError]]:
    """(seconds to wait before retrying, None) or (None, error to raise)."""
    err = data.get("error") if isinstance(data, dict) else None
    message = (err or {}).get("message") or f"HTTP {status}"
    if status == 401:
        return None, HaltoAuthError(message)
    if status in (400, 413):
        return None, HaltoValidationError(message)
    if status == 404:
        return None, HaltoError(message, "not_found", 404)
    if status == 429:
        retry_after = float((err or {}).get("retry_after") or headers.get("retry-after") or 1)
        if attempt < retries:
            return min(retry_after, 10.0), None
        return None, HaltoRateLimitError(retry_after)
    if status >= 500:
        if attempt < retries:
            return _backoff(attempt), None
        return None, HaltoUnavailableError(message)
    return None, HaltoError(message, (err or {}).get("code", "error"), status)


class _Base:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        *,
        timeout: float = 5.0,
        retries: int = 2,
        fail_mode: str = "closed",
    ) -> None:
        self.api_key = api_key or os.environ.get("HALTO_API_KEY", "")
        if not self.api_key:
            raise HaltoError("Halto: api_key is required (or set HALTO_API_KEY)", "config")
        # If Halto can't be reached, the action doesn't run: "open" (allow and warn) is gone since 0.2.0, and "closed"
        # is still accepted for code written before it.
        if fail_mode != "closed":
            raise HaltoError("Halto: fail_mode \"open\" is gone: if Halto can't be reached, the action doesn't run", "config")
        self.base_url = _check_base_url(base_url or os.environ.get("HALTO_URL") or DEFAULT_URL)
        self.timeout = timeout
        self.retries = retries

    def _headers(self) -> Dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}", "content-type": "application/json", "x-halto-client": f"sdk-py/{__version__}"}


class Halto(_Base):
    """Synchronous client.

    >>> halto = Halto(api_key="hk_…", base_url="https://halto.example.com")
    >>> r = halto.check("stripe", "refund", {"amount": 400, "currency": "EUR"})
    >>> r.decision  # "allow", "pending" or "block"
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, *, client: Optional[httpx.Client] = None, **kwargs: Any) -> None:
        super().__init__(api_key, base_url, **kwargs)
        self._client = client or httpx.Client(timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Halto":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def check(
        self,
        tool: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        session_id: Optional[str] = None,
        cost: Optional[float] = None,
        currency: Optional[str] = None,
        category: Optional[str] = None,
        content_sources: Optional[List[Source]] = None,
    ) -> CheckResult:
        """Ask Halto whether an action may run. Never raises for block/pending: inspect ``decision``."""
        body = _check_body(tool, action, params, session_id, cost, currency, category, content_sources)
        return parse_check(self._request("POST", "/v1/check", body))

    def clean(self, content: str, *, type: str = "other", origin: Optional[str] = None, session_id: Optional[str] = None) -> CleanResult:
        """Halto Fix: clean what the agent is about to read (email, web page, document) before your model sees it."""
        return parse_clean(self._request("POST", "/v1/clean", _clean_body(content, type, origin, session_id)))

    def protect(self, content: str, *, type: str = "other", origin: Optional[str] = None, session_id: Optional[str] = None) -> ProtectResult:
        """Data guard: the text for your model with personal and confidential values replaced by placeholders
        ([[EMAIL_K7Q2M]]). Send the model's actions through check() and give the tool result.final_params, which
        hold the real values only where the owner's rules let them go. Never falls back to the unprotected text."""
        return parse_protect(self._request("POST", "/v1/clean", {**_clean_body(content, type, origin, session_id), "protect": True}))

    def get_approval(self, approval_id: str) -> ApprovalStatus:
        return parse_approval(self._request("GET", _approval_path(approval_id)))

    def wait_for_approval(self, approval_id: str, *, timeout: float = 600.0, interval: float = 3.0) -> ApprovalStatus:
        """Poll until a human decides (or timeout). For long-running workers, not serverless functions."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_approval(approval_id)
            if status.status != "pending" or time.monotonic() + interval > deadline:
                return status
            time.sleep(interval)
            interval = min(interval * 1.5, 15.0)

    def guard(self, tool: str, action: str, params: Optional[Dict[str, Any]], fn: Callable[[CheckResult], T], *, wait_for_approval: Wait = False, **check_kwargs: Any) -> T:
        """Run ``fn`` only if Halto allows it, and report the outcome.

        ``fn`` gets the check result: send ``r.final_params`` to the tool, not your own ``params``. They hold the
        text the owner approved and the real values behind placeholders (your own params when Halto sent none).

        Raises HaltoBlockedError, HaltoApprovalPendingError (when not waiting) or HaltoApprovalRejectedError.
        """
        r = self.check(tool, action, params, **check_kwargs)
        if r.decision == "pending" and r.approval_id:
            if not wait_for_approval:
                raise HaltoApprovalPendingError(r.approval_id, r.reason, r.approval_expires_at)
            s = self.wait_for_approval(r.approval_id, timeout=_wait_seconds(wait_for_approval))
            if s.status == "pending":
                raise HaltoApprovalPendingError(r.approval_id, r.reason, r.approval_expires_at)
            if s.status != "approved":
                raise HaltoApprovalRejectedError(r.approval_id, s.status)
            r = self.check(tool, action, params, **check_kwargs)  # the approval is used by this re-check
        if r.decision == "block":
            raise HaltoBlockedError(r.reason, r.action_id)
        if r.decision != "allow":
            raise HaltoApprovalPendingError(r.approval_id or "", r.reason)
        _fill_final_params(r, params)
        try:
            out = fn(r)
        except Exception as err:
            if r.action_id:
                self._report_quietly(action_id=r.action_id, outcome="failure", detail=str(err)[:500])
            raise
        if r.action_id:
            self._report_quietly(action_id=r.action_id, outcome="success")
        return out

    def report(self, *, action_id: Optional[str] = None, outcome: Optional[str] = None, detail: Optional[str] = None, cost: Optional[float] = None, currency: Optional[str] = None, cost_kind: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
        """Report the outcome and/or cost (AI tokens, API calls, purchases) after an action."""
        body = {k: v for k, v in dict(action_id=action_id, outcome=outcome, detail=detail, cost=cost, currency=currency, cost_kind=cost_kind, note=note).items() if v is not None}
        return self._request("POST", "/v1/report", body)

    def session(self, session_id: Optional[str] = None) -> "Session":
        """A session-scoped helper: content read with ``read()`` is sent with the next check, so it taints later actions."""
        return Session(self, session_id or f"run-{uuid.uuid4().hex[:12]}")

    def _report_quietly(self, **kwargs: Any) -> None:
        try:
            self.report(**kwargs)
        except HaltoError:
            pass

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            try:
                res = self._client.request(method, f"{self.base_url}{path}", headers=self._headers(), json=body, timeout=self.timeout)
            except httpx.HTTPError as err:
                last = err
                if attempt < self.retries:
                    time.sleep(_backoff(attempt))
                    continue
                raise HaltoUnavailableError(f"Could not reach Halto at {self.base_url}: {err}", err) from err
            try:
                data = res.json()
            except ValueError:
                data = {}
            if res.is_success:
                return data
            delay, error = _response_error(res.status_code, data, res.headers, attempt, self.retries)
            if error:
                raise error
            time.sleep(delay or 0)
        raise HaltoUnavailableError("Halto request failed", last)


class Session:
    def __init__(self, client: Halto, session_id: str) -> None:
        self.client = client
        self.id = session_id
        self._pending: List[Source] = []

    def read(self, content: str, *, type: str = "other", origin: Optional[str] = None, trusted: bool = False) -> None:
        """Record untrusted content the agent read (an email, a web page…); it is sent with the next check."""
        self._pending.append(ContentSource(content=content, type=type, origin=origin, trusted=trusted))

    def _take(self, extra: Optional[List[Source]]) -> List[Source]:
        sources, self._pending = [*(extra or []), *self._pending], []
        return sources

    def check(self, tool: str, action: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> CheckResult:
        return self.client.check(tool, action, params, session_id=self.id, content_sources=self._take(kwargs.pop("content_sources", None)), **kwargs)

    def guard(self, tool: str, action: str, params: Optional[Dict[str, Any]], fn: Callable[[CheckResult], T], **kwargs: Any) -> T:
        return self.client.guard(tool, action, params, fn, session_id=self.id, content_sources=self._take(kwargs.pop("content_sources", None)), **kwargs)

    def protect(self, content: str, *, type: str = "other", origin: Optional[str] = None) -> ProtectResult:
        """Data guard for this session: the text for your model with placeholders; the original taints the session."""
        return self.client.protect(content, type=type, origin=origin, session_id=self.id)

    def clean(self, content: str, *, type: str = "other", origin: Optional[str] = None) -> CleanResult:
        """Halto Fix for this session: hidden commands removed; the original still taints the session."""
        return self.client.clean(content, type=type, origin=origin, session_id=self.id)


class AsyncHalto(_Base):
    """Asynchronous client with the same methods as :class:`Halto` (awaitable)."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, *, client: Optional[httpx.AsyncClient] = None, **kwargs: Any) -> None:
        super().__init__(api_key, base_url, **kwargs)
        self._client = client or httpx.AsyncClient(timeout=self.timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncHalto":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def check(self, tool: str, action: str, params: Optional[Dict[str, Any]] = None, *, session_id: Optional[str] = None, cost: Optional[float] = None, currency: Optional[str] = None, category: Optional[str] = None, content_sources: Optional[List[Source]] = None) -> CheckResult:
        body = _check_body(tool, action, params, session_id, cost, currency, category, content_sources)
        return parse_check(await self._request("POST", "/v1/check", body))

    async def clean(self, content: str, *, type: str = "other", origin: Optional[str] = None, session_id: Optional[str] = None) -> CleanResult:
        """Halto Fix: clean what the agent is about to read (email, web page, document) before your model sees it."""
        return parse_clean(await self._request("POST", "/v1/clean", _clean_body(content, type, origin, session_id)))

    async def protect(self, content: str, *, type: str = "other", origin: Optional[str] = None, session_id: Optional[str] = None) -> ProtectResult:
        """Data guard: the text for your model with personal and confidential values replaced by placeholders
        ([[EMAIL_K7Q2M]]). Send the model's actions through check() and give the tool result.final_params, which
        hold the real values only where the owner's rules let them go. Never falls back to the unprotected text."""
        return parse_protect(await self._request("POST", "/v1/clean", {**_clean_body(content, type, origin, session_id), "protect": True}))

    async def get_approval(self, approval_id: str) -> ApprovalStatus:
        return parse_approval(await self._request("GET", _approval_path(approval_id)))

    async def wait_for_approval(self, approval_id: str, *, timeout: float = 600.0, interval: float = 3.0) -> ApprovalStatus:
        deadline = time.monotonic() + timeout
        while True:
            status = await self.get_approval(approval_id)
            if status.status != "pending" or time.monotonic() + interval > deadline:
                return status
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 15.0)

    async def guard(self, tool: str, action: str, params: Optional[Dict[str, Any]], fn: Callable[[CheckResult], Union[T, Awaitable[T]]], *, wait_for_approval: Wait = False, **check_kwargs: Any) -> T:
        r = await self.check(tool, action, params, **check_kwargs)
        if r.decision == "pending" and r.approval_id:
            if not wait_for_approval:
                raise HaltoApprovalPendingError(r.approval_id, r.reason, r.approval_expires_at)
            s = await self.wait_for_approval(r.approval_id, timeout=_wait_seconds(wait_for_approval))
            if s.status == "pending":
                raise HaltoApprovalPendingError(r.approval_id, r.reason, r.approval_expires_at)
            if s.status != "approved":
                raise HaltoApprovalRejectedError(r.approval_id, s.status)
            r = await self.check(tool, action, params, **check_kwargs)
        if r.decision == "block":
            raise HaltoBlockedError(r.reason, r.action_id)
        if r.decision != "allow":
            raise HaltoApprovalPendingError(r.approval_id or "", r.reason)
        _fill_final_params(r, params)
        try:
            out = fn(r)
            if asyncio.iscoroutine(out) or isinstance(out, Awaitable):
                out = await out  # type: ignore[misc]
        except Exception as err:
            if r.action_id:
                await self._report_quietly(action_id=r.action_id, outcome="failure", detail=str(err)[:500])
            raise
        if r.action_id:
            await self._report_quietly(action_id=r.action_id, outcome="success")
        return out  # type: ignore[return-value]

    async def report(self, *, action_id: Optional[str] = None, outcome: Optional[str] = None, detail: Optional[str] = None, cost: Optional[float] = None, currency: Optional[str] = None, cost_kind: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
        body = {k: v for k, v in dict(action_id=action_id, outcome=outcome, detail=detail, cost=cost, currency=currency, cost_kind=cost_kind, note=note).items() if v is not None}
        return await self._request("POST", "/v1/report", body)

    async def _report_quietly(self, **kwargs: Any) -> None:
        try:
            await self.report(**kwargs)
        except HaltoError:
            pass

    async def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last: Optional[BaseException] = None
        for attempt in range(self.retries + 1):
            try:
                res = await self._client.request(method, f"{self.base_url}{path}", headers=self._headers(), json=body, timeout=self.timeout)
            except httpx.HTTPError as err:
                last = err
                if attempt < self.retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise HaltoUnavailableError(f"Could not reach Halto at {self.base_url}: {err}", err) from err
            try:
                data = res.json()
            except ValueError:
                data = {}
            if res.is_success:
                return data
            delay, error = _response_error(res.status_code, data, res.headers, attempt, self.retries)
            if error:
                raise error
            await asyncio.sleep(delay or 0)
        raise HaltoUnavailableError("Halto request failed", last)
