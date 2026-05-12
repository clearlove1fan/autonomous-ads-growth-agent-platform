# Retrieval Grounding Evaluation

## Goal

Make RAG and advertiser-memory grounding stricter and more product credible.
The workflow should not pass evaluation simply because it cites any retrieved
source; curated cases should verify that retrieved citations are relevant to the
advertiser brief and meet explicit source expectations.

## Context

- Relevant files:
  - `src/ads_growth_agent/knowledge.py`
  - `src/ads_growth_agent/persistence/knowledge_store.py`
  - `src/ads_growth_agent/evaluation.py`
  - `examples/eval_cases.json`
  - `tests/test_knowledge.py`
  - `tests/test_evaluation.py`
  - `tests/integration/test_postgres_knowledge_store.py`
- Current behavior:
  - In-memory and Postgres retrieval return top-k candidates by relevance.
  - Evaluation only checks that at least one RAG, historical, or advertiser-memory source is cited.
  - The local eval suite can pass even when non-fitness briefs cite low-relevance fitness sources.
- Constraints:
  - Keep default local demos deterministic and model-key-free.
  - Avoid broad schema migrations for this slice.
  - Preserve the existing product workflow and API shape unless stricter validation requires a small typed contract change.

## Plan

- [x] Add this ExecPlan.
- [x] Add a minimum retrieval relevance threshold to `KnowledgeQuery` and both retrieval stores.
- [x] Extend eval expectations with required retrieved source IDs/types and minimum retrieval relevance.
- [x] Update curated eval cases so fitness, skincare, and SaaS cases assert domain-appropriate grounding.
- [x] Add regression tests proving unrelated low-relevance sources are filtered and eval fails missing required sources.
- [x] Update README/RFC notes where helpful.
- [x] Run targeted tests, full tests, ruff, and the deterministic E2E smoke path.

## Decisions

- Decision: Start with relevance-threshold filtering instead of a new ranking engine.
  Reason: The current system already has deterministic relevance scores. Filtering low-confidence candidates is a small, testable step that prevents obvious irrelevant citations without adding embeddings or external services.

## Discoveries

- Discovery: The existing retrieval grounding evaluator is too permissive.
  Evidence: `ads-growth-agent eval examples/eval_cases.json` passed while skincare and SaaS cases cited fitness/app-registration sources as retrieved grounding.

- Discovery: Low-relevance lexical overlap was enough to pollute top-k retrieval.
  Evidence: Before threshold filtering, the skincare case returned
  `case:fitness:trial_registration_creative_loop:v1` at relevance `0.2214`,
  and the SaaS case returned the same fitness case at relevance `0.27`.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/python -m pytest tests/test_knowledge.py tests/test_evaluation.py` passed with 12 passed.
- [x] Full pytest:
  Result: `.venv/bin/python -m pytest` passed with 121 passed and 12 skipped.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Deterministic E2E smoke:
  Result: `.venv/bin/python -m pytest -m e2e` passed with 3 passed.
- [x] Local eval CLI:
  Result: `.venv/bin/ads-growth-agent eval examples/eval_cases.json` passed with 3/3 cases; skincare and SaaS now cite only their expected domain playbooks.
- [x] Live Postgres integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/python -m pytest tests/integration` passed with 12 passed. Docker Postgres was stopped with `docker compose down`.

## Final Status

Completed. Retrieval now carries a minimum relevance threshold, both in-memory
and Postgres stores filter low-confidence candidates, curated eval cases assert
expected source IDs/types, and regression tests cover both local and Postgres
retrieval paths. The remaining production hardening work is to replace the
deterministic lexical score with hybrid vector + FTS ranking once embedding
generation is introduced.
