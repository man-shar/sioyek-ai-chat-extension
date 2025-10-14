"""Command-line helper to query OpenAI for highlighted text in sioyek."""

from __future__ import annotations

import os
import re
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PyQt5 import QtCore, QtWidgets
from dotenv import load_dotenv
from openai import OpenAI

from sioyek.sioyek import Document, DocumentPos, Sioyek as SioyekApp

from database import DatabaseManager, SessionSummary
from ui import ResponseDialog

import fitz

DEFAULT_MODEL = "gpt-4o-mini"
SYSTEM_PROMPT = (
    "You assist with reading PDFs. Answer briefly and focus on the selected text."
)
LOG_PATH = Path(__file__).resolve().parent.parent / "logs.txt"


def _log(message: str) -> None:
    """Write a message to the redirected log."""
    print(message, flush=True)


def _load_environment() -> None:
    """Load environment variables from .env without overriding existing values."""
    load_dotenv(override=False)
    project_root = Path(__file__).resolve().parent.parent
    dotenv_path = project_root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)


def _strip_quotes(value: str) -> str:
    """Remove surrounding quotes from placeholder-expanded strings."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _clean_path(path: str) -> str:
    return _strip_quotes(path)


def _parse_document_position(raw: str) -> Optional[DocumentPos]:
    raw = _strip_quotes(raw)
    if not raw:
        return None
    tokens = raw.replace(",", " ").split()
    if len(tokens) < 3:
        _log(f"[coords] insufficient tokens: {raw}")
        return None
    try:
        page = int(float(tokens[0]))
        offset_x = float(tokens[1])
        offset_y = float(tokens[2])
    except ValueError:
        _log(f"[coords] parse error: {raw}")
        return None
    return DocumentPos(page=page, offset_x=offset_x, offset_y=offset_y)


def _reload_viewer(sioyek_path: str) -> None:
    try:
        SioyekApp(sioyek_path).reload()
    except Exception as exc:  # pragma: no cover - defensive logging
        _log(f"[sioyek] reload failed: {exc}")


def _create_highlight(
    manager: DatabaseManager,
    file_path: str,
    document_hash: str,
    selection_text: str,
    begin_pos: Optional[DocumentPos],
    end_pos: Optional[DocumentPos],
    sioyek_path: str,
    highlight_type: str = "v",
) -> Optional[int]:
    if begin_pos is None or end_pos is None:
        _log("[highlight] missing coordinates; skipping highlight creation")
        return None

    begin_abs, end_abs = _convert_selection_to_absolute(file_path, begin_pos, end_pos)
    if begin_abs is None or end_abs is None:
        return None

    highlight_id = manager.insert_highlight(
        document_path=document_hash,
        selection_text=selection_text,
        highlight_type=highlight_type,
        begin_x=begin_abs.offset_x,
        begin_y=begin_abs.offset_y,
        end_x=end_abs.offset_x,
        end_y=end_abs.offset_y,
    )
    _log(f"[highlight] created id={highlight_id}")
    _reload_viewer(sioyek_path)
    return highlight_id


def _convert_selection_to_absolute(
    file_path: str,
    begin_pos: Optional[DocumentPos],
    end_pos: Optional[DocumentPos],
):
    if begin_pos is None or end_pos is None:
        return None, None

    try:
        document = Document(file_path, None)
        try:

            def adjust(pos: DocumentPos) -> DocumentPos:
                page_width = document.page_widths[pos.page]
                return DocumentPos(
                    pos.page, pos.offset_x - (page_width / 2.0), pos.offset_y
                )

            begin_abs = document.to_absolute(adjust(begin_pos))
            end_abs = document.to_absolute(adjust(end_pos))
        finally:
            document.close()
    except Exception as exc:
        _log(f"[coords] conversion failed: {exc}")
        return None, None
    return begin_abs, end_abs


def _find_session_for_selection(
    manager: DatabaseManager,
    file_path: str,
    document_hash: str,
    begin_pos: Optional[DocumentPos],
    end_pos: Optional[DocumentPos],
    highlight_type: str = "v",
) -> Optional[int]:
    begin_abs, end_abs = _convert_selection_to_absolute(file_path, begin_pos, end_pos)
    if begin_abs is None or end_abs is None:
        return None

    center_x = (begin_abs.offset_x + end_abs.offset_x) / 2.0
    center_y = (begin_abs.offset_y + end_abs.offset_y) / 2.0
    highlight = manager.find_highlight_near(
        document_hash,
        center_x,
        center_y,
        tolerance=40.0,
        highlight_type=highlight_type,
        require_ai=True,
    )
    if highlight is None:
        return None
    session = manager.get_session_by_highlight(highlight.id)
    return session.id if session else None


def _session_summary_to_dict(summary: SessionSummary) -> Dict[str, Any]:
    return {
        "id": summary.id,
        "highlight_id": summary.highlight_id,
        "document_path": summary.document_path,
        "selection_text": summary.selection_text,
        "question": summary.question,
        "answer_preview": summary.answer_preview,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
        "context_snippet": summary.context_snippet,
        "metadata": summary.metadata,
    }


def _join_assistant_messages(messages) -> str:
    parts = [msg.content for msg in messages if msg.role == "assistant" and msg.content]
    return "\n\n".join(parts)


def _first_user_message(messages, fallback: str) -> str:
    for msg in messages:
        if msg.role == "user" and msg.content:
            return msg.content
    return fallback


def _open_history_window(
    manager: DatabaseManager,
    file_path: str,
    document_hash: str,
    selection_text: str,
    question_text: str,
    status_message: str,
    active_session_id: Optional[int] = None,
    metadata: Optional[Dict[str, str]] = None,
    context_snippet: str = "",
) -> bool:
    history_summaries = manager.list_sessions_for_document(document_hash)
    history_payload = [
        _session_summary_to_dict(summary) for summary in history_summaries
    ]

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True

    dialog = ResponseDialog(
        {
            "file_path": file_path,
            "selected_text": selection_text,
            "question": question_text,
            "reply": "",
        },
        history=history_payload,
    )
    dialog.set_history(history_payload, active_session_id=active_session_id)
    dialog.set_streaming_locked(False)

    def show_session(session_id: int) -> None:
        summary = manager.get_session_summary(session_id)
        messages = manager.get_messages(session_id)
        answer_text = _join_assistant_messages(messages) or summary.answer_preview
        question_value = _first_user_message(messages, summary.question)
        dialog.display_session(
            selection_text=summary.selection_text,
            question_text=question_value,
            answer_text=answer_text or "(empty)",
            status_message="Viewing saved conversation.",
            metadata=summary.metadata,
            context_snippet=summary.context_snippet,
        )

    if active_session_id:
        try:
            show_session(active_session_id)
        except Exception as exc:
            _log(f"[history] failed to load session {active_session_id}: {exc}")
            dialog.display_session(
                selection_text=selection_text,
                question_text=question_text,
                answer_text="",
                status_message=status_message,
                metadata=metadata or {},
                context_snippet=context_snippet,
            )
    else:
        dialog.display_session(
            selection_text=selection_text,
            question_text=question_text,
            answer_text="",
            status_message=status_message,
            metadata=metadata or {},
            context_snippet=context_snippet,
        )

    def on_history_selected(session_id: int) -> None:
        show_session(session_id)

    dialog.history_selected.connect(on_history_selected)
    dialog.exec_()

    if created_app:
        app.quit()

    return bool(history_summaries)


def _shorten_text(text: str, max_chars: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _extract_abstract_from_text(text: str) -> Optional[str]:
    pattern = re.compile(r"(?i)\babstract\b[:\s]*")
    match = pattern.search(text)
    if not match:
        return None
    snippet = text[match.end() :]
    terminators = [
        "\n\n",
        "\nIntroduction",
        "\nINTRODUCTION",
        "\n1 ",
        "\nI. ",
    ]
    end_index = None
    for term in terminators:
        idx = snippet.find(term)
        if idx != -1 and (end_index is None or idx < end_index):
            end_index = idx
    if end_index is None:
        end_index = len(snippet)
    abstract = snippet[:end_index].strip()
    return abstract if abstract else None


def _extract_metadata(doc: fitz.Document, file_path: str) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    raw_meta = doc.metadata or {}
    title = (raw_meta.get("title") or "").strip()
    if title:
        metadata["title"] = title
    metadata["file_name"] = Path(file_path).name
    metadata.setdefault("title", Path(file_path).stem)

    abstract: Optional[str] = None
    for page_index in range(min(3, doc.page_count)):
        try:
            page_text = doc.load_page(page_index).get_text("text")
        except Exception:
            continue
        abstract = _extract_abstract_from_text(page_text)
        if abstract:
            break
    if abstract:
        metadata["abstract"] = _shorten_text(abstract, 1200)

    return metadata


def _extract_context_snippet(
    doc: fitz.Document,
    selection_begin: Optional[DocumentPos],
    selection_end: Optional[DocumentPos],
    selection_text: str,
    context_window: int = 500,
) -> str:
    if selection_begin is None:
        return ""

    page_index = max(0, min(selection_begin.page, doc.page_count - 1))
    try:
        page = doc.load_page(page_index)
    except Exception:
        return ""

    clean_selection = (selection_text or "").strip()
    try:
        page_text = page.get_text("text")
    except Exception:
        page_text = ""

    snippet = ""
    if clean_selection:
        lower_page = page_text.lower()
        lower_selection = clean_selection.lower()
        idx = lower_page.find(lower_selection)
        if idx != -1:
            start = max(0, idx - context_window)
            end = min(len(page_text), idx + len(clean_selection) + context_window)
            snippet = page_text[start:end].strip()

    if not snippet:
        end_pos = selection_end or selection_begin
        page_width = doc.page_widths[selection_begin.page]
        left = min(selection_begin.offset_x, end_pos.offset_x) + page_width / 2
        right = max(selection_begin.offset_x, end_pos.offset_x) + page_width / 2
        top = min(selection_begin.offset_y, end_pos.offset_y)
        bottom = max(selection_begin.offset_y, end_pos.offset_y)
        margin = 60
        rect = fitz.Rect(
            max(0.0, left - margin),
            max(0.0, top - margin),
            right + margin,
            bottom + margin,
        )
        try:
            snippet = page.get_text("text", clip=rect).strip()
        except Exception:
            snippet = ""

    if not snippet:
        snippet = page_text.strip()

    return _shorten_text(snippet, 1200)


def _gather_document_context(
    file_path: str,
    selection_begin: Optional[DocumentPos],
    selection_end: Optional[DocumentPos],
    selection_text: str,
) -> Tuple[str, Dict[str, str]]:
    context_snippet = ""
    metadata: Dict[str, str] = {}
    try:
        doc = fitz.open(file_path)
        try:
            metadata = _extract_metadata(doc, file_path)
            context_snippet = _extract_context_snippet(
                doc, selection_begin, selection_end, selection_text
            )
        finally:
            doc.close()
    except Exception as exc:
        _log(f"[context] failed to gather metadata: {exc}")
    return context_snippet, metadata


def set_status(sioyek_path: str, message: str) -> None:
    """Send a status-bar message to sioyek using the command-line interface."""
    _log(f"[set_status] message length={len(message)}")
    if not sioyek_path:
        _log("[set_status] skipped: missing sioyek_path")
        return

    import subprocess

    executable = _clean_path(sioyek_path)

    result = subprocess.run(
        [
            executable,
            "--execute-command",
            "set_status_string",
            "--execute-command-data",
            message,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        _log(f"[set_status stdout] {result.stdout.strip()}")
    if result.stderr:
        _log(f"[set_status stderr] {result.stderr.strip()}")
    if result.returncode:
        _log(f"[set_status returncode] {result.returncode}")


def prepare_openai_request(
    prompt: str,
    doc_path: str,
    question: str | None = None,
    context_snippet: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> tuple[str, str, list[dict[str, str]]]:
    """Build the parameters needed for the OpenAI streaming request."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set; add it to .env or environment")

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    question_content = question.strip() if question else ""
    _log(
        "[openai] preparing request "
        + str(
            {
                "model": model,
                "prompt_chars": len(prompt),
                "has_question": bool(question_content),
            }
        )
    )

    metadata = metadata or {}
    context_snippet = (context_snippet or "").strip()

    user_content = [
        f"Document path: {doc_path or 'unknown'}",
    ]
    title = metadata.get("title")
    if title:
        user_content.append(f"Document title: {title}")

    file_name = metadata.get("file_name")
    if file_name and file_name != title:
        user_content.append(f"File name: {file_name}")

    abstract = metadata.get("abstract")
    if abstract:
        user_content.append("Document abstract:\n" + abstract)

    if context_snippet:
        user_content.append("Context snippet:\n" + context_snippet)

    user_content.extend(
        [
            "",
            "Selected text:",
            prompt.strip(),
        ]
    )
    if question_content:
        user_content.extend(["", "User question:", question_content])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_content)},
    ]

    return api_key, model, messages


class OpenAIStreamWorker(QtCore.QObject):
    """Background worker that streams chat completion tokens."""

    chunk_received = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()
    failed = QtCore.pyqtSignal(str)
    cancelled = QtCore.pyqtSignal()

    def __init__(
        self,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self._stop_requested = False
        self._stream = None

    @QtCore.pyqtSlot()
    def run(self) -> None:
        client = OpenAI(api_key=self.api_key)
        buffer = ""
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                stream=True,
            )
            self._stream = stream

            for chunk in stream:
                if self._stop_requested:
                    self.cancelled.emit()
                    return

                if not chunk.choices:
                    continue

                delta = getattr(chunk.choices[0], "delta", None)
                if not delta:
                    continue

                content = getattr(delta, "content", None)
                if isinstance(content, list):
                    piece = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                    )
                elif isinstance(content, str):
                    piece = content
                else:
                    piece = ""

                if not piece:
                    continue

                buffer += piece
                self.chunk_received.emit(buffer)

            self.finished.emit()
        except Exception as exc:  # pragma: no cover - defensive logging
            self.failed.emit(str(exc))
        finally:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:  # pragma: no cover - defensive logging
                    pass
                self._stream = None

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._stop_requested = True
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:  # pragma: no cover - defensive logging
                pass


def _execute(argv: list[str]) -> int:
    _load_environment()

    if len(argv) < 9:
        print(
            "Usage: python -m sioyek_ai.ask_ai <sioyek_path> <selected_text> <file_path> "
            "<question> <selection_begin_document> <selection_end_document> "
            "<local_database> <shared_database>",
            file=sys.stderr,
        )
        return 1

    sioyek_path = _clean_path(argv[1])
    selected_text = _strip_quotes(argv[2])
    file_path = _clean_path(argv[3])
    question_text = _strip_quotes(argv[4])
    selection_begin_raw = argv[5]
    selection_end_raw = argv[6]
    local_database = _clean_path(argv[7])
    shared_database = _clean_path(argv[8])

    selection_begin = _parse_document_position(selection_begin_raw)
    selection_end = _parse_document_position(selection_end_raw)

    _log(
        "[args] "
        + str(
            {
                "selected_chars": len(selected_text),
                "question_chars": len(question_text),
                "file_path": file_path,
                "local_database": local_database,
                "shared_database": shared_database,
            }
        )
    )

    if not selected_text:
        set_status(sioyek_path, "AI: highlight text first")
        return 0

    if not local_database or not shared_database:
        set_status(sioyek_path, "AI error: missing database paths")
        return 1

    context_snippet, doc_metadata = _gather_document_context(
        file_path, selection_begin, selection_end, selected_text
    )

    manager: Optional[DatabaseManager] = None
    highlight_id: Optional[int] = None
    session_id: Optional[int] = None
    doc_hash: Optional[str] = None

    try:
        manager = DatabaseManager(local_database, shared_database)
        doc_hash = manager.get_document_hash(file_path)

        if not question_text:
            active_session_id = _find_session_for_selection(
                manager,
                file_path=file_path,
                document_hash=doc_hash,
                begin_pos=selection_begin,
                end_pos=selection_end,
            )
            has_history = _open_history_window(
                manager,
                file_path=file_path,
                document_hash=doc_hash,
                selection_text=selected_text,
                question_text=question_text,
                status_message="Select a saved conversation or ask a question.",
                active_session_id=active_session_id,
                metadata=doc_metadata,
                context_snippet=context_snippet,
            )
            if active_session_id or has_history:
                set_status(sioyek_path, "AI history opened")
            else:
                set_status(sioyek_path, "AI history: no saved conversations yet")
            return 0

        try:
            api_key, model, messages = prepare_openai_request(
                selected_text,
                file_path,
                question_text,
                context_snippet=context_snippet,
                metadata=doc_metadata,
            )
        except Exception as exc:
            set_status(sioyek_path, f"AI error: {exc}")
            _log("[openai] preparation failed: " + str(exc))
            return 1

        highlight_id = _create_highlight(
            manager,
            file_path=file_path,
            document_hash=doc_hash,
            selection_text=selected_text,
            begin_pos=selection_begin,
            end_pos=selection_end,
            sioyek_path=sioyek_path,
        )

        try:
            session_summary = manager.create_session(
                highlight_id,
                doc_hash,
                selected_text,
                question_text,
                context_snippet=context_snippet,
                metadata=doc_metadata,
            )
        except Exception:
            if highlight_id is not None:
                manager.delete_highlight(highlight_id)
                _reload_viewer(sioyek_path)
            raise

        session_id = session_summary.id
        if question_text:
            manager.insert_message(session_id, "user", question_text)

        history_summaries = manager.list_sessions_for_document(doc_hash)
        history_payload = [
            _session_summary_to_dict(summary) for summary in history_summaries
        ]

        app = QtWidgets.QApplication.instance()
        created_app = False
        if app is None:
            app = QtWidgets.QApplication(sys.argv)
            created_app = True

        dialog = ResponseDialog(
            {
                "file_path": file_path,
                "selected_text": selected_text,
                "question": question_text,
                "reply": "",
            },
            history=history_payload,
        )
        dialog.set_history(history_payload, active_session_id=session_id)
        dialog.set_status_message("Contacting OpenAI…")
        dialog.set_streaming_locked(True)
        dialog.set_context(doc_metadata, context_snippet)

        def refresh_history(active: Optional[int] = None) -> None:
            summaries = manager.list_sessions_for_document(doc_hash)
            payload = [_session_summary_to_dict(summary) for summary in summaries]
            dialog.set_history(payload, active_session_id=active)

        def on_history_selected(requested_id: int) -> None:
            summary = manager.get_session_summary(requested_id)
            messages_list = manager.get_messages(requested_id)
            answer_text = (
                _join_assistant_messages(messages_list) or summary.answer_preview
            )
            question_value = _first_user_message(messages_list, summary.question)
            dialog.display_session(
                selection_text=summary.selection_text,
                question_text=question_value,
                answer_text=answer_text or "(empty)",
                status_message="Viewing saved conversation.",
                metadata=summary.metadata,
                context_snippet=summary.context_snippet,
            )

        dialog.history_selected.connect(on_history_selected)

        worker = OpenAIStreamWorker(api_key, model, messages)
        thread = QtCore.QThread()
        worker.moveToThread(thread)

        result: Dict[str, Any] = {
            "text": "",
            "error": None,
            "cancelled": False,
            "completed": False,
        }

        def on_chunk(text: str) -> None:
            result["text"] = text
            dialog.update_answer(text)
            dialog.set_status_message("Streaming response…")
            _log(f"[stream] chars={len(text)}")

        def on_finished() -> None:
            result["completed"] = True
            final_text = result.get("text", "") or ""
            if session_id is not None and final_text:
                manager.insert_message(session_id, "assistant", final_text)
                manager.update_session_preview(session_id, final_text)
            dialog.set_status_message("Response complete.")
            dialog.set_streaming_locked(False)
            refresh_history(active=session_id)
            set_status(sioyek_path, "AI reply ready")
            _log("[stream] finished")
            thread.quit()

        def cleanup_failure(message: str, status: str) -> None:
            if session_id is not None:
                manager.delete_session(session_id)
            if highlight_id is not None:
                manager.delete_highlight(highlight_id)
                _reload_viewer(sioyek_path)
            dialog.set_streaming_locked(False)
            refresh_history()
            dialog.set_status_message(status)
            thread.quit()

        def on_failed(message: str) -> None:
            result["error"] = message
            set_status(sioyek_path, f"AI error: {message}")
            _log("[stream] failed: " + message)
            cleanup_failure(f"Error: {message}", f"Error: {message}")

        def on_cancelled() -> None:
            result["cancelled"] = True
            set_status(sioyek_path, "AI request cancelled")
            _log("[stream] cancelled")
            cleanup_failure("Request cancelled.", "Request cancelled.")

        worker.chunk_received.connect(on_chunk, QtCore.Qt.QueuedConnection)
        worker.finished.connect(on_finished, QtCore.Qt.QueuedConnection)
        worker.failed.connect(on_failed, QtCore.Qt.QueuedConnection)
        worker.cancelled.connect(on_cancelled, QtCore.Qt.QueuedConnection)

        thread.started.connect(worker.run)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        worker.cancelled.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        def request_stop() -> None:
            if thread.isRunning():
                worker.stop()

        dialog.finished.connect(request_stop)

        set_status(sioyek_path, "AI: streaming…")
        thread.start()
        dialog.exec_()

        if thread.isRunning():
            worker.stop()
            thread.quit()
        thread.wait()

        if created_app:
            app.quit()

        if result.get("error"):
            return 1

        if result.get("cancelled") and not result.get("completed"):
            return 1

        reply = result.get("text", "") or ""
        if not reply:
            set_status(sioyek_path, "AI: no response")
            return 0

        _log("[result] reply shown via Qt dialog")
        print(reply)
        return 0

    finally:
        if manager is not None:
            manager.close()

    reply = result.get("text", "") or ""
    if not reply:
        set_status(sioyek_path, "AI: no response")
        return 0

    _log("[result] reply shown via Qt dialog")
    print(reply)
    return 0


def main(argv: list[str]) -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        with redirect_stdout(log_file), redirect_stderr(log_file):
            timestamp = datetime.now(timezone.utc).isoformat()
            print(f"--- sioyek_ai.ask_ai invocation {timestamp} ---")
            print(f"argv: {argv}")
            try:
                exit_code = _execute(argv)
            except Exception as exc:  # pragma: no cover - defensive logging
                print("Unhandled exception:")
                print("".join(traceback.format_exception(exc)))
                exit_code = 1
            print(f"exit_code: {exit_code}")
            print()
            return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
