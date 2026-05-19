"""Pick the 2-3 cubes most relevant to the user's question."""

import json

from ...config import CUBE_SELECT_MAX_TOKENS, get_llm_temperature
from ...util.llm_json import parse_llm_json
from ..output.conversation import conversation_context
from ..prompts.prompt_rules import GENERAL_AGENT_CONTRACT
from ..providers import complete_text


def select_cubes(question: str, cubes: list[dict], history: list[dict] | None = None) -> dict:
    return select_cubes_with_profile(question, cubes, history, model_profile=None)


def select_cubes_with_profile(
    question: str,
    cubes: list[dict],
    history: list[dict] | None = None,
    model_profile: dict | None = None,
) -> dict:
    profile_section = (
        f"\nSemantic model profile for business-term interpretation:\n"
        f"{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
        if model_profile else ""
    )
    prompt = f"""You are an expert TM1 / IBM Planning Analytics consultant.

{GENERAL_AGENT_CONTRACT}

Available cubes:
{json.dumps(cubes, indent=2, ensure_ascii=False)}
{profile_section}

User question: "{question}"

Previous conversation:
{conversation_context(history)}

Select 2 to 3 cubes: the best-matching primary source first, then 1-2 alternatives as fallbacks.
Alternatives are essential - if the primary cube returns no data, the system will automatically try the next one.
Choose alternatives that cover the same topic from a different angle.

Use the previous conversation to resolve follow-up references such as "this",
"that category", or "show by month". Do not let previous broad questions override
a narrower current question.

Critical: if the user requests a breakdown such as "by X", the primary cube
MUST expose X or a clear business equivalent as a dimension, attribute, or
dimension element pattern in the cube metadata above. Use semantic business
matching for natural-language equivalents (for example role/person/entity,
organization/unit/location/category concepts), but do not pick a cube that lacks
a plausible equivalent breakdown as the primary source.

Use metric and cube preferences from the semantic model profile when available.
If the profile has no guidance for a business term, choose the cube whose own
dimensions, attributes, measures, and description best support the requested
metric and breakdown.

For financial statement questions, use finance_semantics from the semantic
model profile when available. For example, an income_statement/P&L question
should prefer the mapped primary_cube and require the mapped line_item_dimension
to be available for statement rows.

Critical: if the user is asking to display names, labels, or descriptions of entities (employees, cost centres, etc.) that appeared in a previous result, select THE SAME CUBE as the previous turn (shown as [cube: ...] in conversation above). TM1 element display names come from dimension attributes and are applied automatically - there is no separate "names" cube to query.

Reply ONLY with valid JSON - no markdown, no extra text:
{{
  "cubes": [
    {{
      "cube": "<exact cube name from the list>",
      "reasoning": "<max 12 words>"
    }}
  ],
  "reasoning": "<max 12 words>"
}}"""

    try:
        raw = complete_text(
            prompt,
            max_tokens=CUBE_SELECT_MAX_TOKENS,
            temperature=get_llm_temperature("cube_select_temperature"),
        )
        return parse_llm_json(raw)  # type: ignore[return-value]
    except json.JSONDecodeError as exc:
        try:
            repair_prompt = f"""Return ONLY valid compact JSON for this cube selection result.
No markdown. No extra text. Keep every reasoning under 8 words.

Original invalid JSON:
{raw}

Required shape:
{{"cubes":[{{"cube":"<exact cube name>","reasoning":"<short>"}}],"reasoning":"<short>"}}"""
            repaired = complete_text(
                repair_prompt,
                max_tokens=max(CUBE_SELECT_MAX_TOKENS, 900),
                temperature=0,
            )
            return parse_llm_json(repaired)  # type: ignore[return-value]
        except Exception:
            raise RuntimeError(f"AI cube selection parsing failed. Raw: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI cube selection error: {exc}") from exc
