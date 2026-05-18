# Phase 1 Demo Expected Output

Run:

```bash
python scripts/verify_phase1_demo.py
```

Expected excerpt:

```text
Phase 1 MVP demo verification passed
Demo case: phase1_fitness_app_underperforming_feedback
Input: I want to use a $2000 budget to promote a fitness app in the United States and increase trial registrations over 14 days.
Intake: heuristic -> advertiser=adv_fitness_001, objective=registrations, budget=USD 2000.00
Graph path: planner -> retriever -> tool_executor -> critic -> finalizer
Strategy: strategy_beea88d2fbb05ce4 -> draft=draft_adv_fitness_001_fitness_app_registrations (draft)
Forecast: 111 conversions at CPA USD 18.00
Feedback: underperforming -> matched_rules=strategy_beea88d2fbb05ce4:rule:cpa_guardrail, strategy_beea88d2fbb05ce4:rule:pacing
Recommendations: adjust_budget, refresh_creative (draft-only, human approval required)
Sources: advertiser_memory, historical_case, mock_tool, rag_document
```

The full raw JSON remains available through:

```bash
ads-growth-agent demo
```

The curated verifier intentionally checks the product contract instead of
snapshotting dynamic fields such as `run_id` and `trace_id`.
