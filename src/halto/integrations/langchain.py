"""LangChain: wrap any tool so every call is checked by Halto first, and its answer is protected.

    from halto import Halto
    from halto.integrations.langchain import guard_tools

    halto = Halto(api_key="hk_…", base_url="https://halto.example.com")
    safe_tools = guard_tools(tools, halto)          # one Halto session for this run
    agent = create_react_agent(llm, safe_tools)

Each call: Halto checks the model's arguments; the tool runs only on "allow", with Halto's ``final_params`` (the
text the owner approved, the real values behind placeholders); its answer reaches the model through Halto's data
guard (personal data as placeholders, hidden commands removed) and marks the session as "has read outside content".
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Union

from ..client import Halto
from ._common import guarded_call, new_session_id

try:  # pragma: no cover - import guard
    from langchain_core.tools import BaseTool, StructuredTool
except ImportError as exc:  # pragma: no cover
    raise ImportError("halto.integrations.langchain needs langchain-core: pip install 'halto[langchain]'") from exc


def guard_tool(
    tool: BaseTool,
    halto: Halto,
    *,
    namespace: str = "langchain",
    wait_for_approval: Union[bool, float] = False,
    raise_on_block: bool = False,
    session_id: Optional[str] = None,
    protect_output: bool = True,
    reads_outside_content: bool = True,
) -> StructuredTool:
    """Return a copy of ``tool`` that asks Halto before running. Halto sees tool ``namespace`` and action ``tool.name``,
    with the call's arguments as parameters, so rules like "refunds over €100 need my approval" apply.

    ``session_id``: links this tool's checks with what the other tools read. Without it, every tool wrapped with the
    same ``halto`` client shares one session; use :func:`guard_tools` (or pass an id) per conversation.
    ``protect_output``: send the tool's answer through Halto's data guard before the model sees it (default on; the
    answer then reaches the model as text). ``reads_outside_content``: the answer counts as outside content for the
    prompt-injection protection (default on; turn off for tools that only return your own trusted data).
    """

    def run(**kwargs: Any) -> Any:
        return guarded_call(
            halto,
            namespace,
            tool.name,
            kwargs,
            lambda params: tool.invoke(params),
            wait_for_approval=wait_for_approval,
            raise_on_block=raise_on_block,
            session_id=session_id,
            protect_output=protect_output,
            reads_outside_content=reads_outside_content,
        )

    return StructuredTool.from_function(func=run, name=tool.name, description=tool.description, args_schema=tool.args_schema)


def guard_tools(tools: Iterable[BaseTool], halto: Halto, *, session_id: Optional[str] = None, **kwargs: Any) -> List[StructuredTool]:
    """Wrap several tools with one fresh Halto session (call it once per conversation or run)."""
    sid = session_id or new_session_id()
    return [guard_tool(t, halto, session_id=sid, **kwargs) for t in tools]
