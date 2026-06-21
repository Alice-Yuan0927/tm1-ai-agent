# Anomaly Module — Design Rationale

## The one rule that drives everything

> **Rules detect. The LLM interprets.**
> The LLM is used at *config-time* (to draft rules) and at *result-time* (to write commentary), **never at runtime to judge whether a number is anomalous.**

If you remember nothing else, remember that detection is deterministic. Every later phase should be checked against this line before it ships.

## Why not "just let the LLM find the anomalies"?

The detector ([detector.py](detector.py), [variance.py](variance.py),[rules/builtin.py](rules/builtin.py)) is pure threshold logic — negative
values, FTE > Headcount, a measure diverging from Budget past both a `%` and a `$` gate. **All of this could be written in TM1 TurboIntegrator.**
That is the point, not a weakness:

On the *detection* step, rules beat an LLM on every axis that matters here:

| | Rules / TI | LLM on raw numbers |
|---|---|---|
| Deterministic | ✅ | ❌ |
| Auditable / explainable | ✅ | ❌ |
| Cost at 10k–100k rows | ~free | expensive, often infeasible |
| Hallucination risk | none | real |
| Good with tabular finance data | ✅ | weak vs. stats/rules |

For FP&A numbers you *want* a check that fires the same way every time and can be pointed to in a review. So we do **not** ask an LLM "is this row weird." Rules do that.

## So where is the AI value, then?

In the two places rules and TI **cannot** reach — the upstream and the downstream of detection.

### Upstream — authoring rules (config-time)

`POST /api/anomaly/rules/{cube}/suggest` → [rules/suggest.py](rules/suggest.py)

TI requires a developer to hand-write every check. Instead the LLM reads
the cube schema and **drafts a RuleSet YAML** that a business user then
tunes. This lowers the barrier to *writing* rules; it does not replace
*executing* them. The drafted YAML is still reviewed, versioned, and run
deterministically.

### Downstream — interpreting flags (result-time)

[commentary.py](commentary.py)

A scan can emit 50 raw flags. TI can tell you "CostCenter X is 18% over
budget" but cannot turn fifty of those into "this period's labor-cost risk
is concentrated in two BUs; look at X first." The LLM **interprets,
prioritises, and narrates** — outputting a headline, a ≤2-sentence
interpretation, and a `suggested_action` per flag.

Guarded by `validate_commentary`: every meaningful number in the
commentary must already appear in the source flags, or it is reported as
suspicious. The model rephrases and ranks; it is not allowed to introduce
figures.

## Why reimplement the rules in Python instead of TI?

This decision is about engineering, not AI:

- Rules are version-controlled YAML (`data/anomaly_rules/*.yaml`) —
  diffable, reviewable, reusable across cubes.
- Cross-version comparison (current vs. budget / prior_week) is clean in
  Python, awkward in TI.
- Dismissal memory, severity scoring, and top-N suppression are stateful
  logic that is hard to test and maintain inside a TM1 process.
- Commentary needs an LLM call, which belongs in the app layer, not the
  TM1 server.

## Guardrail for future phases

When Phase 3 (scan execution), Phase 4 (rule suggestion), or Phase 5
(Teams push) land, the failure mode to avoid is **scope-creeping the LLM
into the detection path** — e.g. "feed the rows to the model and ask what
looks off." If a feature needs that, prefer a statistical detector
(z-score, seasonal baseline) expressed as a new rule layer, so the result
stays deterministic and auditable.
