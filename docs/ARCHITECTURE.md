# Backend Architecture

```text
backend/
  backend.py                  # FastAPI app, middleware, route registration, lifespan
  config.py                   # env/runtime config and atomic config writes
  llm_models.py               # provider model catalog and validation
  schemas.py                  # request DTOs
  excel_service.py / email_service.py

  routes/                     # HTTP edge; thin JSON/SSE handlers
    health.py
    config.py
    schema.py
    analyze.py
    explain_cell.py
    export.py
    email.py
    assets.py
    rag_feedback.py

  services/                   # request orchestration
    analyze_pipeline.py       # /api/analyze SSE pipeline
    mdx_execution.py          # legacy one-shot/repair execution helpers
    tm1_health.py
    model_profile.py
    embedding_sync.py
    cell_explain_service.py

  ai/                         # LLM-facing logic, prompts, providers, tools
    agent/                    # current top-level analysis agent
      loop.py                 # run_agent generator; cube discovery + MDX execution
      prompt.py
      tools.py
    mdx/
      context.py              # MdxContext frozen dataclass
      normalize.py            # normalize_mdx, validate_generated_mdx
      generation.py           # repair_cube_mdx for the legacy repair loop
      directives.py           # cube-context/profile directive builders
    intent/                   # preflight, clarification, follow-up helpers
    output/                   # narrative/profile generation
    prompts/                  # external prompt fragments
    providers/                # OpenAI/Anthropic/DeepSeek implementations
    retrieval/                # embeddings and past-query RAG store
    schema/                   # TM1/domain validators and classifiers
    tools/                    # agent tool registry and handlers

  tm1/
    service.py                # live TM1py wrappers and structured previews
    cache/                    # SQLite schema cache

  semantic/                   # deterministic cube summaries
  util/                       # small shared helpers
```

## Layering Rules

Allowed import direction is:

```text
routes -> services -> ai/providers
routes -> services -> ai -> tm1/cache
routes -> services -> tm1/cache
shared leaf modules -> util/config/schemas
```

Forbidden:

- `tm1/*` must not import from `ai/*`.
- `ai/*` must not import from `routes/*` or `services/*`, except for narrow legacy imports in the MDX execution repair path.
- `services/*` must not import from `routes/*`.
- Cross-route imports should be avoided; promote shared logic to `services/`.
- Provider-specific SDK use belongs in `ai/providers/*`.

Run `lint-imports` when `import-linter` is installed to check the contracts in `pyproject.toml`.

## Request Flow: `/api/analyze`

`analyze_sse_gen` in `services/analyze_pipeline.py` runs the current analysis flow:

1. Load the current semantic profile and check for client disconnects.
2. Run deterministic early clarification with `is_unclear_question` and `find_clarifications`.
3. Apply follow-up context with `effective_question_from_history`.
4. Call `run_agent` from `ai/agent/loop.py`.
   The agent searches RAG first, discovers cubes, loads schema, resolves elements, writes MDX, executes it, and retries through tool feedback until it finds rows or returns a clarification.
5. Build a structured preview with `ai.tools.preview.build_cube_preview`.
6. Stream narrative analysis through `stream_financial_analysis`.
7. Send the final SSE payload with sources, preview rows, generated MDX, analysis, and suggestions.

The SSE generator checks `_client_disconnected(request)` between major stages so long-running work stops when the client closes the connection.

## Key Types And State

- `MdxContext` (`ai/mdx/context.py`) bundles question, cube schema, history, profile, grounded members, and similar queries for MDX validation/repair helpers.
- `AgentResult` (`ai/agent/loop.py`) is the current agent output: rows, layout, MDX, cube, schema, reasoning, steps, optional clarification, and optional error.
- `_CubeResult` (`services/analyze_pipeline.py`) converts the agent result into frontend source payloads and token-limited narrative input.
- Runtime state lives under `backend/data/`: schema cache, runtime config, model catalogs, generated semantic profiles, and cube summaries.

## Runtime State

- `TM1_CONFIG` and `LLM_CONFIG` are module-level dicts in `config.py`; callers should use accessor functions instead of capturing the dicts.
- TM1 health state is encapsulated by `services/tm1_health.py`.
- Homepage suggestions are cached in `routes/schema.py` and invalidated after schema/config changes.
- Schema metadata is stored in `backend/data/schema_cache.db`.
- Semantic profiles are stored in `backend/data/model_profiles/`.

## Prompts

LLM prompt fragments live under `backend/ai/prompts/`. The MDX hard rules are kept in `mdx_hard_rules.md` so they can be edited without changing Python code.
