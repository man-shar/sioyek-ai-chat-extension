"""PyQt5 dialog utilities for displaying AI responses and history."""

from __future__ import annotations

import html
import sys
from typing import Iterable, Mapping, Optional

from PyQt5 import QtCore, QtWidgets


class ResponseDialog(QtWidgets.QDialog):
    """Dialog showing the active response alongside previous sessions."""

    history_selected = QtCore.pyqtSignal(int)

    def __init__(
        self,
        data: Mapping[str, str],
        history: Optional[Iterable[Mapping[str, str]]] = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sioyek AI Helper")
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self._history_items: dict[int, QtWidgets.QListWidgetItem] = {}

        layout = QtWidgets.QVBoxLayout(self)

        outer_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        history_container = QtWidgets.QWidget()
        history_layout = QtWidgets.QVBoxLayout(history_container)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(6)

        history_label = QtWidgets.QLabel("<b>History</b>")
        history_label.setTextFormat(QtCore.Qt.RichText)
        history_layout.addWidget(history_label)

        self.history_list = QtWidgets.QListWidget()
        self.history_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.history_list.itemSelectionChanged.connect(self._on_history_selection_changed)
        self.history_list.setMinimumWidth(220)
        history_layout.addWidget(self.history_list)
        outer_splitter.addWidget(history_container)

        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        file_path = data.get("file_path", "")
        escaped_path = html.escape(file_path)
        self.file_label = QtWidgets.QLabel(f"<b>File:</b> {escaped_path}")
        self.file_label.setTextFormat(QtCore.Qt.RichText)
        self.file_label.setWordWrap(True)
        right_layout.addWidget(self.file_label)

        content_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        selected_widget, self.selected_field = self._create_section(
            "Selected Text", data.get("selected_text", "")
        )
        question_widget, self.question_field = self._create_section(
            "Question", data.get("question", "")
        )
        answer_widget, self.answer_field = self._create_section(
            "Answer", data.get("reply", "")
        )
        content_splitter.addWidget(selected_widget)
        content_splitter.addWidget(question_widget)
        content_splitter.addWidget(answer_widget)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 1)
        content_splitter.setStretchFactor(2, 2)
        right_layout.addWidget(content_splitter)

        self.status_label = QtWidgets.QLabel("Waiting for response…")
        right_layout.addWidget(self.status_label)

        self.context_group = QtWidgets.QGroupBox("Context Passed")
        self.context_group.setCheckable(True)
        self.context_group.setChecked(False)
        context_layout = QtWidgets.QVBoxLayout(self.context_group)
        context_layout.setContentsMargins(8, 8, 8, 8)
        context_layout.setSpacing(6)

        self.metadata_label = QtWidgets.QLabel("")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextFormat(QtCore.Qt.RichText)
        context_layout.addWidget(self.metadata_label)

        self.context_field = QtWidgets.QPlainTextEdit("(empty)")
        self.context_field.setReadOnly(True)
        self.context_field.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        context_layout.addWidget(self.context_field)

        right_layout.addWidget(self.context_group)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        right_layout.addWidget(button_box)

        right_layout.setStretchFactor(content_splitter, 1)

        outer_splitter.addWidget(right_container)
        outer_splitter.setStretchFactor(0, 0)
        outer_splitter.setStretchFactor(1, 1)
        layout.addWidget(outer_splitter)

        self.resize(900, 560)

        if history:
            self.set_history(history)

        self._streaming_locked = False

    # ------------------------------------------------------------------
    # History helpers
    # ------------------------------------------------------------------
    def set_history(
        self,
        sessions: Iterable[Mapping[str, str]],
        active_session_id: Optional[int] = None,
    ) -> None:
        self.history_list.blockSignals(True)
        self.history_list.clear()
        self._history_items.clear()
        for session in sessions:
            session_id = int(session.get("id", 0))
            item = QtWidgets.QListWidgetItem(self._format_history_entry(session))
            item.setData(QtCore.Qt.UserRole, session_id)
            self.history_list.addItem(item)
            self._history_items[session_id] = item
        self.history_list.blockSignals(False)
        if active_session_id is not None and active_session_id in self._history_items:
            self.select_history(active_session_id)

    def update_history_entry(self, session: Mapping[str, str]) -> None:
        session_id = int(session.get("id", 0))
        item = self._history_items.get(session_id)
        if item is None:
            item = QtWidgets.QListWidgetItem(self._format_history_entry(session))
            item.setData(QtCore.Qt.UserRole, session_id)
            self.history_list.insertItem(0, item)
            self._history_items[session_id] = item
        else:
            item.setText(self._format_history_entry(session))

    def select_history(self, session_id: int) -> None:
        item = self._history_items.get(session_id)
        if not item:
            return
        self.history_list.blockSignals(True)
        self.history_list.setCurrentItem(item)
        self.history_list.blockSignals(False)

    def clear_history_selection(self) -> None:
        self.history_list.blockSignals(True)
        self.history_list.clearSelection()
        self.history_list.blockSignals(False)

    def set_history_enabled(self, enabled: bool) -> None:
        self.history_list.setEnabled(enabled)

    def _on_history_selection_changed(self) -> None:
        if self._streaming_locked:
            return
        item = self.history_list.currentItem()
        if not item:
            return
        session_id = item.data(QtCore.Qt.UserRole)
        if session_id:
            self.history_selected.emit(int(session_id))

    # ------------------------------------------------------------------
    # Streaming & content helpers
    # ------------------------------------------------------------------
    def set_streaming_locked(self, locked: bool) -> None:
        self._streaming_locked = locked
        self.set_history_enabled(not locked)

    def set_selected_text(self, text: str) -> None:
        self._set_text(self.selected_field, text)

    def set_question_text(self, text: str) -> None:
        self._set_text(self.question_field, text)

    def update_answer(self, text: str) -> None:
        self._set_text(self.answer_field, text)
        self.answer_field.verticalScrollBar().setValue(
            self.answer_field.verticalScrollBar().maximum()
        )

    def reset_answer(self, text: str = "") -> None:
        self._set_text(self.answer_field, text)

    def set_status_message(self, message: str) -> None:
        self.status_label.setText(message)

    def display_session(
        self,
        selection_text: str,
        question_text: str,
        answer_text: str,
        status_message: str,
        metadata: Optional[Mapping[str, str]] = None,
        context_snippet: str = "",
    ) -> None:
        self.set_selected_text(selection_text)
        self.set_question_text(question_text)
        self.update_answer(answer_text)
        self.set_status_message(status_message)
        self.set_context(metadata or {}, context_snippet)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_history_entry(session: Mapping[str, str]) -> str:
        created = session.get("updated_at") or session.get("created_at") or ""
        created = created.replace("T", " ")[:16]
        question = (session.get("question") or "").strip()
        selection = (session.get("selection_text") or "").strip()
        preview = question or selection or "(no prompt)"
        answer_preview = (session.get("answer_preview") or "").strip()
        if len(preview) > 80:
            preview = preview[:77] + "…"
        if answer_preview:
            snippet = answer_preview.replace("\n", " ")
            if len(snippet) > 80:
                snippet = snippet[:77] + "…"
            return f"{created}\nQ: {preview}\nA: {snippet}"
        return f"{created}\nQ: {preview}"

    @staticmethod
    def _create_section(
        title: str, text: str
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QPlainTextEdit]:
        container = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        label = QtWidgets.QLabel(f"<b>{title}</b>")
        label.setTextFormat(QtCore.Qt.RichText)
        vbox.addWidget(label)

        field = QtWidgets.QPlainTextEdit(text or "(empty)")
        field.setReadOnly(True)
        field.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        vbox.addWidget(field)

        return container, field

    @staticmethod
    def _set_text(field: QtWidgets.QPlainTextEdit, text: str) -> None:
        content = text if text else "(empty)"
        field.setPlainText(content)

    def set_context(self, metadata: Mapping[str, str], snippet: str) -> None:
        snippet = snippet.strip()
        if not metadata and not snippet:
            self.context_group.setChecked(False)
            self.context_group.setEnabled(False)
            self.metadata_label.setText("<i>No additional context provided.</i>")
            self.context_field.setPlainText("(empty)")
            return

        lines = []
        for key, value in metadata.items():
            if value:
                lines.append(f"<b>{html.escape(str(key).title())}:</b> {html.escape(str(value))}")
        metadata_html = "<br/>".join(lines) if lines else "<i>No metadata available.</i>"
        self.metadata_label.setText(metadata_html)
        self.context_field.setPlainText(snippet or "(empty)")
        self.context_group.setEnabled(True)
        if snippet or metadata_html:
            self.context_group.setChecked(True)


def show_response_dialog(data: Mapping[str, str]) -> None:
    """Compatibility helper; shows a dialog as a standalone action."""
    app = QtWidgets.QApplication.instance()
    created = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created = True

    dialog = ResponseDialog(data)
    dialog.exec_()

    if created:
        app.quit()
