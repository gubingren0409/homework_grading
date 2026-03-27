import uuid
from contextvars import ContextVar, Token
from typing import Dict, Optional


_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")
_task_id_ctx: ContextVar[str] = ContextVar("task_id", default="-")
_component_ctx: ContextVar[str] = ContextVar("component", default="-")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def get_task_id() -> str:
    return _task_id_ctx.get()


def get_component() -> str:
    return _component_ctx.get()


def set_trace_id(trace_id: str) -> Token:
    return _trace_id_ctx.set(trace_id)


def set_task_id(task_id: str) -> Token:
    return _task_id_ctx.set(task_id)


def set_component(component: str) -> Token:
    return _component_ctx.set(component)


def bind_context(
    *,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    component: Optional[str] = None,
) -> Dict[str, Token]:
    tokens: Dict[str, Token] = {}
    if trace_id is not None:
        tokens["trace_id"] = set_trace_id(trace_id)
    if task_id is not None:
        tokens["task_id"] = set_task_id(task_id)
    if component is not None:
        tokens["component"] = set_component(component)
    return tokens


def reset_context(tokens: Dict[str, Token]) -> None:
    if "component" in tokens:
        _component_ctx.reset(tokens["component"])
    if "task_id" in tokens:
        _task_id_ctx.reset(tokens["task_id"])
    if "trace_id" in tokens:
        _trace_id_ctx.reset(tokens["trace_id"])


def log_context() -> Dict[str, str]:
    return {
        "trace_id": get_trace_id(),
        "task_id": get_task_id(),
        "component": get_component(),
    }


def outbound_trace_headers() -> Dict[str, str]:
    trace_id = get_trace_id()
    if trace_id == "-":
        return {}
    return {"X-Trace-Id": trace_id}

