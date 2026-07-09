"""Ingest LongMemEval haystack sessions into memdio StorageManager."""

import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor

from memdio.core.storage import StorageManager


def _normalize_extracted_memories(extracted) -> tuple[list[str], list[str]]:
    if hasattr(extracted, "facts") and hasattr(extracted, "preferences"):
        return list(extracted.facts), list(extracted.preferences)
    return list(extracted), []


def format_session(session: list[dict], session_date: str | None = None) -> str:
    """Format a session's turns into a single text block for storage."""
    lines = []
    if session_date:
        lines.append(f"[Date: {session_date}]")
    for turn in session:
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def ingest_question(
    question: dict, base_dir: str | None = None, extractor=None
) -> tuple[StorageManager, str]:
    """Ingest all haystack sessions for a single question into an isolated StorageManager.

    If ``extractor`` (a callable ``(session_text, date) -> list[str]`` of atomic facts)
    is provided, each session is distilled into discrete fact-memories instead of the
    raw session dump. Extraction runs in parallel across sessions (LLM-bound).

    Returns (storage_manager, db_path) so caller can clean up.
    """
    if base_dir is None:
        base_dir = tempfile.mkdtemp(prefix="memdio_bench_")

    qid = question["question_id"]
    db_dir = os.path.join(base_dir, qid)

    storage = StorageManager(base_path=db_dir)

    sessions = question.get("haystack_sessions", [])
    dates = question.get("haystack_dates", [])

    if extractor is None:
        for i, session in enumerate(sessions):
            date = dates[i] if i < len(dates) else None
            text = format_session(session, session_date=date)
            if text.strip():
                storage.store(text, document_date=date)
        return storage, db_dir

    # Hybrid mode: store the raw session (for detail questions) AND its distilled
    # atomic facts (for aggregation/temporal questions). Extraction runs in parallel.
    def _extract(i_session):
        i, session = i_session
        date = dates[i] if i < len(dates) else None
        text = format_session(session, session_date=date)
        if not text.strip():
            return date, "", []
        return date, text, _normalize_extracted_memories(extractor(text, date))

    with ThreadPoolExecutor(max_workers=8) as pool:
        per_session = list(pool.map(_extract, enumerate(sessions)))

    for date, text, extracted in per_session:
        facts, preferences = extracted
        if text.strip():
            storage.store(text, tags="session", document_date=date, detect=False)
        for fact in facts:
            content = f"[{date}] {fact}" if date else fact
            storage.store(content, tags="fact", document_date=date, detect=False)
        for preference in preferences:
            content = f"[{date}] {preference}" if date else preference
            storage.store(content, tags="preference", document_date=date, detect=False)

    return storage, db_dir


def cleanup_question_db(db_dir: str):
    """Remove temporary database directory."""
    if os.path.exists(db_dir):
        shutil.rmtree(db_dir, ignore_errors=True)
