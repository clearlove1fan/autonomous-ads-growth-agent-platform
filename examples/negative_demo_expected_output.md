# Negative Demo Expected Output

Run:

```bash
python scripts/verify_negative_demos.py
```

Expected excerpt:

```text
Negative demo verification passed
Safe failure: llm_planner rejected invalid tool plan with LLM_PLANNER_INVALID_TOOL_PLAN; node_path=planner
Idempotency conflict: HTTP 409 IDEMPOTENCY_KEY_REUSED
Performance event conflict: HTTP 409 PERFORMANCE_EVENT_ID_CONFLICT for evt_negative_demo_conflict
Result: unsafe or conflicting requests return structured errors and do not execute actions.
```

The negative verifier covers three high-risk paths:

- Invalid LLM planner output is blocked before any ads tool action executes.
- Reusing an idempotency key with a conflicting request returns a structured
  conflict.
- Reusing a campaign performance `event_id` with a different payload returns a
  structured conflict.
