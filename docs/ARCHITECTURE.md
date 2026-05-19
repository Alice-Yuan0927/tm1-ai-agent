# Backend architecture

```
backend/
├── backend.py                   # FastAPI app + middleware + route registration (lifespan)
├── config.py                    # env / runtime config + atomic config writes
├── llm_models.py                # provider model catalog + validation
├── schemas.py                   # request DTOs
├── response_messages.py
├── excel_service.py / email_service.py
│
├── routes/                      # HTTP edge — thin, returns JSON / SSE
│   ├── health.py                # /api/health
│   ├── config.py                # /api/tm1-config, /api/llm-models/refresh
│   ├── schema.py                # /api/sync-schema, /api/suggestions, /api/views, /api/model-profile/generate
│   ├── analyze.py               # /api/analyze
│   ├── export.py                # /api/export-excel
│   ├── email.py                 # /api/send-email
│   └── assets.py                # static asset serving
│
├── services/                    # request orchestration — wires ai + tm1 together
│   ├── tm1_health.py            # TM1 health state (probe / cache / lock)
│   ├── model_profile.py         # load/save/generate semantic profile
│   └── analyze_pipeline.py      # SSE analysis pipeline (stages + dataclasses)
│
├── ai/                          # LLM-facing logic — prompts, planning, providers
│   ├── __init__.py              # stable public re-export surface
│   ├── prompts/                 # externalised prompt text + meta-rules
│   │   ├── mdx_hard_rules.md   # numbered MDX hard rules (editable without Python)
│   │   └── prompt_rules.py     # GENERAL_AGENT_CONTRACT + meta-rules
│   ├── providers/               # LLMProvider Protocol + OpenAI/Anthropic impls
│   │   ├── base.py              # Protocol definition
│   │   ├── openai_provider.py
│   │   └── anthropic_provider.py
│   ├── mdx/                     # MDX generation pipeline
│   │   ├── context.py           # MdxContext frozen dataclass
│   │   ├── normalize.py         # normalize_mdx, validate_generated_mdx
│   │   ├── generation.py        # generate_cube_mdx, repair_cube_mdx (LLM)
│   │   ├── planner.py           # rule-based MDX fast path (no LLM)
│   │   └── directives.py        # cube-context / profile directive builders
│   ├── intent/                  # Query understanding — no LLM unless needed
│   │   ├── preflight.py         # is_unclear_question (deterministic)
│   │   ├── clarification.py     # find_clarifications / find_schema_clarification
│   │   ├── attribute_intent.py  # follow-up "show employee names" detection
│   │   ├── query_intent.py      # rule-based intent classifier + schema pre-filter
│   │   └── cube_selection.py    # select_cubes_with_profile
│   ├── schema/                  # TM1/domain knowledge — no LLM calls
│   │   ├── tm1_lexicon.py       # SCENARIO / STATEMENT regex patterns
│   │   ├── dim_roles.py         # content-based dim role classification
│   │   ├── finance_semantics.py # finance ontology matcher
│   │   └── result_validators.py # post-execution shape/focus/statement/entity checks
│   ├── retrieval/               # Embedding search + past-query RAG
│   │   ├── embeddings.py        # embed_texts, ensure_element_embeddings, search_by_embedding
│   │   └── rag.py               # SQLite FTS past-query store (init_db, save_query, retrieve_similar)
│   └── output/                  # Conversation + narrative + profile generation
│       ├── conversation.py      # conversation_context formatter
│       ├── narrative.py         # stream_financial_analysis, parse_suggestions
│       └── semantic_profile.py  # generate_semantic_profile
│
├── tm1/
│   ├── service.py               # live TM1py wrappers (execute MDX, get_cube_schema fallback)
│   └── cache/                   # SQLite schema cache (split from monolith)
│       ├── db.py                # connect(), DDL, migrations, is_empty
│       ├── sync.py              # sync_schema — pull from TM1, replace cache
│       ├── defaults.py          # default_element picker + Sys Parameter probe
│       ├── read.py              # cube_schema_cached, alias / attribute / metadata readers
│       └── member_search.py     # FTS-backed question-to-member matching
│
└── util/
    ├── io.py                    # atomic_write_text / atomic_write_json
    └── llm_json.py              # parse_llm_json (strip ``` fences + json.loads)
```

## Layering rules

Allowed import directions (top → bottom):

```
routes  ──►  services  ──►  ai  ──►  ai/providers
              │              │
              └────►  tm1 ───┘
                       │
                       └──► tm1/cache
                       │
                       └──► util (shared, leaf)
```

Forbidden:

- `tm1/*` MUST NOT import from `ai/*` (currently honored).
- `ai/*` MUST NOT import from `routes/*` or `services/*`.
- `services/*` MUST NOT import from `routes/*`.
- Cross-route imports inside `routes/*` are limited to module-level helpers
  (e.g. `routes/config.py` imports `clear_suggestions_cache` from
  `routes/schema.py`). New cross-route imports should be promoted to `services/`.
- Provider-specific code lives only in `ai/providers/*`. The rest of `ai/*`
  uses `complete_text` / `stream_text` from `ai/providers/__init__.py`.

Run `lint-imports` (see `pyproject.toml`) to enforce these as CI checks.

## Request flow: /api/analyze

`analyze_sse_gen` (in `services/analyze_pipeline.py`) walks four stages:

1. **Preflight + clarification** — `is_unclear_question`, `find_clarifications`.
   No TM1 / no LLM unless needed.
2. **Cube selection** — `_select_cubes_stage`: either honors user-scoped cubes
   or calls `select_cubes_with_profile`, with a `find_schema_clarification`
   gate for high-risk ambiguous questions.
3. **Per-cube processing** — `_process_cube` per selected cube:
   - request-scoped schema read (`_RequestSchemaCache`)
   - intent detection + focused schema (`detect_query_intent`)
   - member grounding (`resolve_question_members`, `find_question_element_matches`,
     `search_by_embedding`)
   - try rule-based plan (`try_plan_mdx`) → else LLM (`generate_cube_mdx`)
   - execute + repair loop (`_execute_mdx_with_repair`) with deterministic
     validators after each attempt
   - structured preview build
4. **Narrative streaming** — `_stream_analysis` → `stream_financial_analysis`
   (LLM), token-budgeted via `_source_for_ai`.

All four stages cooperatively respect `_client_disconnected(request)` so
SSE generations stop early if the client closes the connection.

## Key dataclasses

- `MdxContext` (`ai/mdx_context.py`) — frozen bundle of `(question, cube_schema,
  history, model_profile, grounded_members, similar_queries)`. Passed to
  `generate_cube_mdx`, `repair_cube_mdx`, and the per-cube validators.
- `_CubeResult` (`services/analyze_pipeline.py`) — internal per-cube payload
  with `to_source_dict()` that strips internal fields before sending to the
  frontend.

## Config + state

- `TM1_CONFIG`, `LLM_CONFIG` are module-level dicts updated in place by
  `update_tm1_config`. Read via the accessor functions in `config.py`
  (`get_llm_model`, `get_llm_provider`, …). Callers must not capture the dict.
- `_tm1_health` is encapsulated in `services/tm1_health.py` behind
  `update()`, `get()`, `probe_tm1_name()`, etc. — no direct mutation outside
  the module.
- `_suggestions_cache` lives in `routes/schema.py` and is cleared via
  `clear_suggestions_cache()` from both schema sync and config update.

## Externalised prompts

LLM prompt fragments live as text files under `backend/ai/prompts/` and are
loaded at import time. Non-engineers can edit `mdx_hard_rules.md` without
touching Python.
