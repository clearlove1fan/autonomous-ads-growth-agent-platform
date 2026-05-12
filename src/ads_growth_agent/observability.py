from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from langsmith import traceable, tracing_context

from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import RunMetadata, ToolResult, ToolRunSummary


@dataclass(frozen=True)
class RunContext:
    run_id: str
    strategy_id: str | None
    trace_id: str
    langsmith_project: str
    tracing_enabled: bool


def create_run_context(
    *,
    run_id: str | None = None,
    strategy_id: str | None = None,
    settings: Settings | None = None,
) -> RunContext:
    settings = settings or get_settings()
    return RunContext(
        run_id=run_id or f"run_{uuid4().hex[:16]}",
        strategy_id=strategy_id,
        trace_id=f"trace_{uuid4().hex}",
        langsmith_project=settings.langsmith_project,
        tracing_enabled=settings.langsmith_tracing,
    )


@contextmanager
def graph_tracing_context(run_context: RunContext, *, advertiser_id: str) -> Iterator[None]:
    with tracing_context(
        project_name=run_context.langsmith_project,
        enabled=run_context.tracing_enabled,
        tags=["ads-growth-agent", "langgraph", "v0.1"],
        metadata={
            "run_id": run_context.run_id,
            "execution_id": run_context.run_id,
            "strategy_id": run_context.strategy_id,
            "trace_id": run_context.trace_id,
            "advertiser_id": advertiser_id,
        },
    ):
        yield


@traceable(
    name="growth_strategy_graph",
    process_inputs=lambda inputs: _trace_inputs(inputs),
    process_outputs=lambda outputs: _trace_outputs(outputs),
)
def invoke_traced_graph(graph, initial_state: dict, config: dict | None = None):
    if config is None:
        return graph.invoke(initial_state)
    return graph.invoke(initial_state, config=config)


def build_run_metadata(
    run_context: RunContext,
    *,
    node_path: list[str],
    tool_results: list[ToolResult],
    error_summary: list[str] | None = None,
) -> RunMetadata:
    tool_summaries = [
        ToolRunSummary(
            tool_name=result.tool_name,
            success=result.success,
            latency_ms=result.latency_ms,
            error_code=result.error.code if result.error else None,
        )
        for result in tool_results
    ]
    return RunMetadata(
        run_id=run_context.run_id,
        execution_id=run_context.run_id,
        strategy_id=run_context.strategy_id,
        trace_id=run_context.trace_id,
        langsmith_project=run_context.langsmith_project,
        tracing_enabled=run_context.tracing_enabled,
        node_path=node_path,
        tool_count=len(tool_results),
        failed_tool_count=sum(1 for result in tool_results if not result.success),
        tool_summaries=tool_summaries,
        error_summary=error_summary or [],
    )


def _trace_inputs(inputs: dict) -> dict:
    initial_state = inputs.get("initial_state", {})
    brief = initial_state.get("brief")
    return {
        "advertiser_id": getattr(brief, "advertiser_id", None),
        "objective": getattr(brief, "objective", None),
        "product_category": getattr(brief, "product_category", None),
    }


def _trace_outputs(outputs: dict) -> dict:
    if not isinstance(outputs, dict):
        return {
            "node_path": [],
            "tool_count": 0,
            "failed_tool_count": 0,
        }

    tool_results = outputs.get("tool_results", [])
    return {
        "node_path": outputs.get("node_path", []),
        "tool_count": len(tool_results),
        "failed_tool_count": sum(1 for result in tool_results if not result.success),
    }
