# RAG Memory Foundation

## Goal

Add a v0.1 retrieval and advertiser memory foundation to the agent workflow. The graph should explicitly retrieve relevant campaign knowledge after planning, attach retrieval results to graph state, and cite retrieved RAG documents, historical cases, and advertiser memory in the final strategy. The implementation should be offline-testable now and shaped so it can later swap to PostgreSQL + pgvector.

## Context

- Relevant files:
  - `src/ads_growth_agent/graph.py` owns LangGraph node orchestration and final strategy assembly.
  - `src/ads_growth_agent/contracts.py` defines `SourceCitation`.
  - `src/ads_growth_agent/evaluation.py` defines required node path expectations.
  - `tests/test_graph_workflow.py` and `tests/test_strategy_api_cli.py` assert node paths.
  - `README.md` already describes RAG and advertiser memory as part of the product direction.
- Current behavior:
  - The workflow has no retrieval node.
  - Final strategy cites mock tool sources only.
  - RAG is mentioned in docs but not implemented.
- Constraints:
  - Do not require Docker/Postgres for this slice.
  - Keep retrieval deterministic and locally testable.
  - Preserve draft-only tool behavior and existing tool count semantics.
  - Avoid broad infrastructure work until the agent contract is stable.

## Plan

- [x] Add typed retrieval contracts and an in-memory knowledge store.
- [x] Add seeded strategy documents, historical cases, and advertiser memory.
- [x] Add a `retriever` LangGraph node after planner and before tool execution.
- [x] Include retrieved sources in final strategy citations and assumptions.
- [x] Update required node paths and tests.
- [x] Run lint, tests, compile checks, then commit and push.

## Decisions

- Decision: Start with an in-memory store behind a retrieval interface.
  Reason: This proves the product workflow and contracts without requiring Docker/Postgres availability.
- Decision: Add `advertiser_memory` as a first-class source citation type.
  Reason: Advertiser profile memory should be distinguishable from generic RAG docs and historical cases.
- Decision: Make retrieval a real graph node.
  Reason: RAG quality and latency should be observable in node paths and LangSmith traces.

## Discoveries

- Discovery: `SourceCitation` currently allows `mock_tool`, `assumption`, `rag_document`, and `historical_case`.
  Evidence: `src/ads_growth_agent/contracts.py` defines the literal source types.
- Discovery: Existing tests assert the direct `planner -> tool_executor` path.
  Evidence: `tests/test_graph_workflow.py` and `tests/test_strategy_api_cli.py` assert node paths without retrieval.
- Discovery: Retrieval is now explicit graph state, not a tool result.
  Evidence: `_retriever_node` writes `knowledge` and `artifacts["knowledge"]` without adding to `tool_results`.
- Discovery: Local eval now checks retrieval grounding directly.
  Evidence: `evaluate_retrieval_grounding` requires at least one `rag_document`, `historical_case`, or `advertiser_memory` source citation.
- Discovery: Full verification passes after adding the RAG/memory foundation.
  Evidence: Full pytest reports 46 passed; ruff reports all checks passed; compileall completes successfully.

## Verification

- [x] `.venv/bin/python -m pytest`
  Result: 46 passed.
- [x] `.venv/bin/python -m ruff check .`
  Result: All checks passed.
- [x] `.venv/bin/python -m compileall src tests`
  Result: Completed successfully.

## Final Status

Complete. The graph now has an explicit `retriever` node, deterministic local knowledge retrieval, retrieved source citations, and retrieval-grounding evaluation.
