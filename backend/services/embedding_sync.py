"""Background post-sync tasks: embeddings and cube summaries."""

import logging
import threading

from ..ai.retrieval.embeddings import ensure_element_embeddings
from ..semantic.cube_summary import refresh_all_summaries

_log = logging.getLogger(__name__)


def trigger_embedding_sync() -> None:
    """Fire ensure_element_embeddings() in a daemon thread so sync never blocks."""

    def _run() -> None:
        try:
            n = ensure_element_embeddings()
            if n:
                _log.info("[embeddings] background sync complete: %d new embeddings", n)
        except Exception as exc:
            _log.warning("[embeddings] background sync failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


def trigger_summary_refresh(model_profile: dict | None = None) -> None:
    """Regenerate cube summaries in a daemon thread after schema sync."""

    def _run() -> None:
        try:
            results = refresh_all_summaries(model_profile=model_profile)
            _log.info("[cube-summary] background refresh complete: %d summaries", len(results))
        except Exception as exc:
            _log.warning("[cube-summary] background refresh failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
