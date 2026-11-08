"""CrewAI: wrap a tool so every call is checked by Halto first, and its answer is protected.

    from halto import Halto
    from halto.integrations.crewai import guard_tools

    halto = Halto(api_key="hk_…", base_url="https://halto.example.com")
    agent = Agent(role="Support", tools=guard_tools([SendEmailTool(), ReadInboxTool()], halto), …)

Each call: Halto checks the model's arguments; the tool runs only on "allow", with Halto's ``final_params`` (the
text the owner approved, the real values behind placeholders); its answer reaches the model through Halto's data
guard (personal data as placeholders, hidden commands removed) and marks the session as "has read outside content".
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Union

from ..client import Halto
from ._common import guarded_call, new_session_id

try:  # pragma: no cover - import guard
    from crewai.tools import BaseTool
except ImportError as exc:  # pragma: no cover
    raise ImportError("halto.integrations.crewai needs crewai: pip install 'halto[crewai]'") from exc


def guard_tool(
    tool: BaseTool,
    halto: Halto,
    *,
    namespace: str = "crewai",
    wait_for_approval: Union[bool, float] = False,
    raise_on_block: bool = False,
    session_id: Optional[str] = None,
    protect_output: bool = True,
    reads_outside_content: bool = True,
) -> BaseTool:
    """Return a tool with the same name, description and arguments that asks Halto before running ``tool``.
    The options mean the same as in :func:`halto.integrations.langchain.guard_tool`."""
    inner = tool

    class HaltoGuardedTool(BaseTool):  # type: ignore[misc, valid-type]
        name: str = inner.name
        description: str = inner.description
        args_schema: Any = inner.args_schema

        def _run(self, **kwargs: Any) -> Any:
            return guarded_call(
                halto,
                namespace,
                inner.name,
                kwargs,
                lambda params: inner.run(**params),
                wait_for_approval=wait_for_approval,
                raise_on_block=raise_on_block,
                session_id=session_id,
                protect_output=protect_output,
                reads_outside_content=reads_outside_content,
            )

    return HaltoGuardedTool()


def guard_tools(tools: Iterable[BaseTool], halto: Halto, *, session_id: Optional[str] = None, **kwargs: Any) -> List[BaseTool]:
    """Wrap several tools with one fresh Halto session (call it once per crew run)."""
    sid = session_id or new_session_id()
    return [guard_tool(t, halto, session_id=sid, **kwargs) for t in tools]
