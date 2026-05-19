"""Conversation-history formatting for prompt construction."""

from ...config import HISTORY_ANALYSIS_MAX_CHARS, HISTORY_WINDOW


def conversation_context(history: list[dict] | None) -> str:
    """Format the last few turns for inclusion in an LLM prompt."""
    if not history:
        return "No previous messages."

    items = []
    for message in history[-HISTORY_WINDOW:]:
        question = str(message.get("question", "")).strip()
        analysis = str(message.get("analysis", "")).strip()
        cube = str(message.get("chosen_cube", "")).strip()
        if analysis and len(analysis) > HISTORY_ANALYSIS_MAX_CHARS:
            analysis = analysis[:HISTORY_ANALYSIS_MAX_CHARS] + "..."
        cube_tag = f" [cube: {cube}]" if cube else ""
        items.append(f"User:{cube_tag} {question}\nAssistant: {analysis}")
    return "\n\n".join(items)
