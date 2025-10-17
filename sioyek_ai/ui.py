"""PyQt5 dialog utilities for displaying AI responses and history."""

from __future__ import annotations

import html
import sys
from typing import Iterable, Mapping, Optional

from PyQt5 import QtCore, QtGui, QtWidgets


class CollapsibleSection(QtWidgets.QWidget):
    """Reusable collapsible container with a header toggle."""

    toggled = QtCore.pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        parent: QtWidgets.QWidget | None = None,
        expanded: bool = False,
    ) -> None:
        super().__init__(parent)
        self._toggle = QtWidgets.QToolButton(text=title)
        self._toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow
        )
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        self._toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self._toggle.clicked.connect(self._on_clicked)

        self._content = QtWidgets.QWidget()
        self._content.setVisible(expanded)
        self._content_layout = QtWidgets.QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 4, 0, 0)
        self._content_layout.setSpacing(6)

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(4)
        root_layout.addWidget(self._toggle)
        root_layout.addWidget(self._content)

    def content_layout(self) -> QtWidgets.QVBoxLayout:
        return self._content_layout

    def is_expanded(self) -> bool:
        return self._content.isVisible()

    def set_expanded(self, expanded: bool) -> None:
        currently = self._content.isVisible()
        if currently == expanded:
            self._toggle.setChecked(expanded)
            self._toggle.setArrowType(
                QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow
            )
            return
        self._toggle.setChecked(expanded)
        self._content.setVisible(expanded)
        self._toggle.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow
        )
        self.toggled.emit(expanded)

    def setEnabled(self, enabled: bool) -> None:  # noqa: D401 - Qt override
        super().setEnabled(enabled)
        self._toggle.setEnabled(enabled)
        if not enabled:
            self._content.setVisible(False)
            self._toggle.setChecked(False)
            self._toggle.setArrowType(QtCore.Qt.RightArrow)

    def _on_clicked(self, checked: bool) -> None:
        self.set_expanded(checked)


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
        self._notification_timer = QtCore.QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.timeout.connect(self.hide_notification)

        layout = QtWidgets.QVBoxLayout(self)

        self._notification_label = QtWidgets.QLabel("")
        self._notification_label.setObjectName("notificationLabel")
        self._notification_label.setWordWrap(False)
        self._notification_label.setTextFormat(QtCore.Qt.PlainText)
        self._notification_label.setVisible(False)
        self._notification_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._notification_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        self._notification_label.setStyleSheet(
            "QLabel#notificationLabel {"
            " color: #5a4200;"
            " background-color: #fff6d5;"
            " border: 1px solid #f0c36d;"
            " border-radius: 4px;"
            " padding: 3px 8px;"
            " margin: 0 0 4px 0;"
            " font-size: 11px;"
            "}"
        )
        self._notification_label.setMinimumHeight(
            self._notification_label.fontMetrics().height() + 6
        )
        layout.addWidget(self._notification_label)

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
        self.history_list.itemSelectionChanged.connect(
            self._on_history_selection_changed
        )
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

        self.context_section = CollapsibleSection("Context Passed", expanded=False)
        context_layout = self.context_section.content_layout()

        self.metadata_label = QtWidgets.QLabel("")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextFormat(QtCore.Qt.RichText)
        context_layout.addWidget(self.metadata_label)

        self.context_placeholder = QtWidgets.QLabel(
            "<i>No context snippet provided.</i>"
        )
        self.context_placeholder.setTextFormat(QtCore.Qt.RichText)
        self.context_placeholder.setWordWrap(True)
        context_layout.addWidget(self.context_placeholder)

        self.context_field = QtWidgets.QPlainTextEdit("")
        self.context_field.setReadOnly(True)
        self.context_field.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        context_layout.addWidget(self.context_field)
        self.context_placeholder.hide()
        self.context_field.hide()

        right_layout.addWidget(self.context_section)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        right_layout.addWidget(button_box)

        right_layout.setStretchFactor(content_splitter, 1)

        right_scroll = QtWidgets.QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_container)

        outer_splitter.addWidget(right_scroll)
        outer_splitter.setStretchFactor(0, 0)
        outer_splitter.setStretchFactor(1, 1)
        layout.addWidget(outer_splitter)

        self.resize(900, 560)

        if history:
            self.set_history(history)

        self._streaming_locked = False
        self._context_initialized = False
        self._notification_text: str = ""

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

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def show_notification(self, message: str, timeout_ms: int = 5000) -> None:
        if not message:
            self.hide_notification()
            return
        self._notification_text = message
        self._update_notification_elision()
        self._notification_label.setToolTip(message)
        self._notification_label.setVisible(True)
        self._notification_timer.stop()
        QtCore.QTimer.singleShot(0, self._update_notification_elision)
        if timeout_ms > 0:
            self._notification_timer.start(timeout_ms)

    def hide_notification(self) -> None:
        self._notification_timer.stop()
        self._notification_text = ""
        self._notification_label.clear()
        self._notification_label.setVisible(False)

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
            self.metadata_label.setText("<i>No additional context provided.</i>")
            self.context_placeholder.hide()
            self.context_field.hide()
            self.context_section.setEnabled(False)
            self._context_initialized = False
            return

        lines = []
        for key, value in metadata.items():
            if value:
                lines.append(
                    f"<b>{html.escape(str(key).title())}:</b> {html.escape(str(value))}"
                )
        metadata_html = (
            "<br/>".join(lines) if lines else "<i>No metadata available.</i>"
        )
        self.metadata_label.setText(metadata_html)
        if snippet:
            self.context_field.setPlainText(snippet)
            self.context_field.show()
            self.context_placeholder.hide()
        else:
            self.context_field.hide()
            self.context_placeholder.show()
        was_expanded = self.context_section.is_expanded()
        self.context_section.setEnabled(True)
        if not self._context_initialized:
            self.context_section.set_expanded(False)
        else:
            self.context_section.set_expanded(was_expanded)
        self._context_initialized = True

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._notification_label.isVisible() and self._notification_text:
            self._update_notification_elision()

    def _update_notification_elision(self) -> None:
        if not self._notification_text:
            return
        available_width = max(16, self._notification_label.width() - 12)
        metrics = self._notification_label.fontMetrics()
        elided = metrics.elidedText(
            self._notification_text, QtCore.Qt.ElideRight, available_width
        )
        self._notification_label.setText(elided)


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
