"""Open the AI history dialog for a highlight near the clicked position."""

from __future__ import annotations

import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from sioyek.sioyek import Document, DocumentPos

from database import DatabaseManager
from ask_ai import (
    LOG_PATH,
    _clean_path,
    _parse_document_position,
    _open_history_window,
    set_status,
)


def _log(message: str) -> None:
    print(message, flush=True)


def _absolute_from_document(path: str, pos: DocumentPos) -> tuple[float, float]:
    document = Document(path, None)
    try:
        absolute = document.to_absolute(pos)
    finally:
        document.close()
    return absolute.offset_x, absolute.offset_y


def main(argv: list[str]) -> int:
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as log_file:
        with redirect_stdout(log_file), redirect_stderr(log_file):
            timestamp = datetime.now(timezone.utc).isoformat()
            print(f"--- sioyek_ai.show_history invocation {timestamp} ---")
            print(f"argv: {argv}")

            try:
                if len(argv) < 6:
                    print(
                        "Usage: python -m sioyek_ai.show_history <sioyek_path> <file_path> "
                        "<mouse_pos_document> <local_database> <shared_database>",
                        file=sys.stderr,
                    )
                    return 1

                sioyek_path = _clean_path(argv[1])
                file_path = _clean_path(argv[2])
                mouse_raw = argv[3]
                local_database = _clean_path(argv[4])
                shared_database = _clean_path(argv[5])

                if not local_database or not shared_database:
                    set_status(sioyek_path, "AI history error: missing database paths")
                    return 1

                doc_position = _parse_document_position(mouse_raw)
                if doc_position is None:
                    set_status(sioyek_path, "AI history error: unable to parse position")
                    return 1
                _log(
                    "[history] mouse tokens: "
                    + str(
                        {
                            "raw": mouse_raw,
                            "page": doc_position.page,
                            "offset_x": doc_position.offset_x,
                            "offset_y": doc_position.offset_y,
                        }
                    )
                )

                try:
                    abs_x, abs_y = _absolute_from_document(file_path, doc_position)
                    _log(f"[history] absolute coordinates: {abs_x}, {abs_y}")
                except Exception as exc:
                    set_status(sioyek_path, f"AI history error: {exc}")
                    _log(f"[history] coordinate conversion failed: {exc}")
                    return 1

                manager = DatabaseManager(local_database, shared_database)
                try:
                    document_hash = manager.get_document_hash(file_path)
                    highlight = manager.find_highlight_near(
                        document_hash,
                        abs_x,
                        abs_y,
                        tolerance=40.0,
                        highlight_type="v",
                        require_ai=True,
                    )

                    session = manager.get_session_by_highlight(highlight.id) if highlight else None

                    selection_text = ""
                    question_text = ""
                    status_message = "Viewing saved conversation."
                    active_session_id = None
                    metadata = {}
                    context_snippet = ""
                    notification_message = None

                    if highlight is None:
                        notification_message = (
                            "No AI highlight found near that click. Showing document history."
                        )
                        status_message = "No saved conversation near this location."
                    elif session is None:
                        notification_message = (
                            "That highlight has no saved chat yet. Showing document history."
                        )
                        status_message = "No chat recorded for this highlight."
                        selection_text = highlight.desc or ""
                    else:
                        selection_text = session.selection_text
                        question_text = session.question
                        active_session_id = session.id
                        metadata = session.metadata or {}
                        context_snippet = session.context_snippet or ""

                    has_history = _open_history_window(
                        manager,
                        file_path=file_path,
                        document_hash=document_hash,
                        selection_text=selection_text,
                        question_text=question_text,
                        status_message=status_message,
                        active_session_id=active_session_id,
                        metadata=metadata,
                        context_snippet=context_snippet,
                        notification_message=notification_message,
                    )
                    if notification_message:
                        set_status(sioyek_path, notification_message)
                    elif has_history:
                        set_status(sioyek_path, "AI history opened")
                    else:
                        set_status(sioyek_path, "AI history: no saved conversations yet")
                    return 0
                finally:
                    manager.close()

            except Exception as exc:
                print("Unhandled exception:")
                print("".join(traceback.format_exception(exc)))
                set_status(_clean_path(argv[1] if len(argv) > 1 else ""), f"AI history error: {exc}")
                return 1
            finally:
                print()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
