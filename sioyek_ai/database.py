"""SQLite helpers for persisting AI sessions linked to sioyek highlights."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Dict

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


@dataclass
class SessionSummary:
    id: int
    highlight_id: Optional[int]
    document_path: str
    created_at: str
    updated_at: str
    selection_text: str
    question: str
    answer_preview: str
    context_snippet: str
    metadata: Dict[str, str]


@dataclass
class Message:
    role: str
    content: str
    created_at: str


@dataclass
class HighlightRecord:
    id: int
    document_path: str
    desc: str
    highlight_type: str
    begin_x: float
    begin_y: float
    end_x: float
    end_y: float
    is_ai: bool


class DatabaseManager:
    """Manage read/write access to sioyek's sqlite databases."""

    def __init__(self, local_db: str, shared_db: str) -> None:
        self.local_path = Path(local_db).expanduser()
        self.shared_path = Path(shared_db).expanduser()

        self.local_conn = sqlite3.connect(str(self.local_path))
        self.shared_conn = sqlite3.connect(str(self.shared_path))
        self.shared_conn.row_factory = sqlite3.Row
        self.shared_conn.execute("PRAGMA foreign_keys = ON")
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.shared_conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_sessions (
                id INTEGER PRIMARY KEY,
                highlight_id INTEGER,
                document_path TEXT NOT NULL,
                selection_text TEXT,
                question TEXT,
                answer_preview TEXT,
                context_snippet TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(highlight_id) REFERENCES highlights(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES ai_sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_ai_sessions_document_path
                ON ai_sessions(document_path);
            CREATE INDEX IF NOT EXISTS idx_ai_messages_session_id
                ON ai_messages(session_id);
            """
        )
        self.shared_conn.commit()
        self._ensure_highlight_ai_column()
        self._ensure_session_context_columns()

    def _ensure_highlight_ai_column(self) -> None:
        cursor = self.shared_conn.execute("PRAGMA table_info(highlights)")
        columns = [row[1] for row in cursor.fetchall()]
        if "is_ai" not in columns:
            self.shared_conn.execute(
                "ALTER TABLE highlights ADD COLUMN is_ai INTEGER DEFAULT 0"
            )
            self.shared_conn.commit()

    def _ensure_session_context_columns(self) -> None:
        cursor = self.shared_conn.execute("PRAGMA table_info(ai_sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        alterations = []
        if "context_snippet" not in columns:
            alterations.append(
                "ALTER TABLE ai_sessions ADD COLUMN context_snippet TEXT"
            )
        if "metadata_json" not in columns:
            alterations.append(
                "ALTER TABLE ai_sessions ADD COLUMN metadata_json TEXT"
            )
        for sql in alterations:
            self.shared_conn.execute(sql)
        if alterations:
            self.shared_conn.commit()

    # ------------------------------------------------------------------
    # Document / highlight helpers
    # ------------------------------------------------------------------
    def get_document_hash(self, file_path: str) -> str:
        norm = os.path.normpath(file_path)
        cursor = self.local_conn.execute(
            "SELECT hash FROM document_hash WHERE path = ?", (norm,)
        )
        row = cursor.fetchone()
        if row:
            return row[0]

        # Fall back to computing the hash if sioyek hasn't seen this file yet.
        file_hash = self._hash_file(norm)
        self.local_conn.execute(
            "INSERT INTO document_hash(path, hash) VALUES (?, ?)", (norm, file_hash)
        )
        self.local_conn.commit()
        return file_hash

    def _hash_file(self, file_path: str) -> str:
        digest = hashlib.md5()
        with open(file_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def insert_highlight(
        self,
        document_path: str,
        selection_text: str,
        highlight_type: str,
        begin_x: float,
        begin_y: float,
        end_x: float,
        end_y: float,
    ) -> int:
        existing = self.find_highlight(
            document_path, begin_x, begin_y, end_x, end_y
        )
        if existing is not None:
            self.shared_conn.execute(
                "UPDATE highlights SET desc = ?, type = ?, is_ai = 1 WHERE id = ?",
                (selection_text, highlight_type, existing),
            )
            self.shared_conn.commit()
            return existing

        cursor = self.shared_conn.execute(
            """
            INSERT INTO highlights(document_path, desc, type, begin_x, begin_y, end_x, end_y, is_ai)
            VALUES(?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                document_path,
                selection_text,
                highlight_type,
                begin_x,
                begin_y,
                end_x,
                end_y,
            ),
        )
        self.shared_conn.commit()
        return int(cursor.lastrowid)

    def find_highlight(
        self,
        document_path: str,
        begin_x: float,
        begin_y: float,
        end_x: float,
        end_y: float,
        tolerance: float = 1e-2,
    ) -> Optional[int]:
        cursor = self.shared_conn.execute(
            """
            SELECT id FROM highlights
            WHERE document_path = ?
              AND ABS(begin_x - ?) < ?
              AND ABS(begin_y - ?) < ?
              AND ABS(end_x - ?) < ?
              AND ABS(end_y - ?) < ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                document_path,
                begin_x,
                tolerance,
                begin_y,
                tolerance,
                end_x,
                tolerance,
                end_y,
                tolerance,
            ),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None

    def delete_highlight(self, highlight_id: int) -> None:
        self.shared_conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
        self.shared_conn.commit()

    # ------------------------------------------------------------------
    # Session + message helpers
    # ------------------------------------------------------------------
    def create_session(
        self,
        highlight_id: Optional[int],
        document_path: str,
        selection_text: str,
        question_text: str,
        context_snippet: str = "",
        metadata: Optional[Dict[str, str]] = None,
    ) -> SessionSummary:
        timestamp = _utc_now()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        cursor = self.shared_conn.execute(
            """
            INSERT INTO ai_sessions( highlight_id, document_path, selection_text,
                                     question, answer_preview, context_snippet,
                                     metadata_json, created_at, updated_at )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                highlight_id,
                document_path,
                selection_text,
                question_text,
                "",
                context_snippet or None,
                metadata_json,
                timestamp,
                timestamp,
            ),
        )
        self.shared_conn.commit()
        return self.get_session_summary(int(cursor.lastrowid))

    def get_session_summary(self, session_id: int) -> SessionSummary:
        cursor = self.shared_conn.execute(
            """
            SELECT id, highlight_id, document_path, selection_text, question,
                   answer_preview, context_snippet, metadata_json,
                   created_at, updated_at
            FROM ai_sessions WHERE id = ?
            """,
            (session_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Unknown session id: {session_id}")
        return SessionSummary(
            id=row["id"],
            highlight_id=row["highlight_id"],
            document_path=row["document_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            selection_text=row["selection_text"] or "",
            question=row["question"] or "",
                answer_preview=row["answer_preview"] or "",
                context_snippet=row["context_snippet"] or "",
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            )

    def list_sessions_for_document(self, document_path_hash: str) -> List[SessionSummary]:
        cursor = self.shared_conn.execute(
            """
            SELECT id, highlight_id, document_path, selection_text, question, answer_preview,
                   context_snippet, metadata_json,
                   created_at, updated_at
            FROM ai_sessions
            WHERE document_path = ?
            ORDER BY updated_at DESC
            """,
            (document_path_hash,),
        )
        rows = cursor.fetchall()
        return [
            SessionSummary(
                id=row["id"],
                highlight_id=row["highlight_id"],
                document_path=row["document_path"],
                selection_text=row["selection_text"] or "",
                question=row["question"] or "",
                answer_preview=row["answer_preview"] or "",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                context_snippet=row["context_snippet"] or "",
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            )
            for row in rows
        ]

    def insert_message(self, session_id: int, role: str, content: str) -> Message:
        timestamp = _utc_now()
        self.shared_conn.execute(
            """
            INSERT INTO ai_messages(session_id, role, content, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (session_id, role, content, timestamp),
        )
        self.shared_conn.commit()
        return Message(role=role, content=content, created_at=timestamp)

    def update_session_preview(self, session_id: int, preview: str) -> None:
        timestamp = _utc_now()
        self.shared_conn.execute(
            "UPDATE ai_sessions SET answer_preview = ?, updated_at = ? WHERE id = ?",
            (preview, timestamp, session_id),
        )
        self.shared_conn.commit()

    def get_session_by_highlight(self, highlight_id: int) -> Optional[SessionSummary]:
        cursor = self.shared_conn.execute(
            """
            SELECT id, highlight_id, document_path, selection_text, question,
                   answer_preview, created_at, updated_at
            FROM ai_sessions
            WHERE highlight_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (highlight_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SessionSummary(
            id=row["id"],
            highlight_id=row["highlight_id"],
            document_path=row["document_path"],
            selection_text=row["selection_text"] or "",
            question=row["question"] or "",
            answer_preview=row["answer_preview"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            context_snippet=row["context_snippet"] or "",
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    def get_session_question(self, session_id: int) -> str:
        cursor = self.shared_conn.execute(
            "SELECT question FROM ai_sessions WHERE id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else ""

    def get_messages(self, session_id: int) -> List[Message]:
        cursor = self.shared_conn.execute(
            """
            SELECT role, content, created_at
            FROM ai_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        rows = cursor.fetchall()
        return [Message(role=row["role"], content=row["content"], created_at=row["created_at"]) for row in rows]

    def list_highlights_for_document(self, document_path: str) -> List[HighlightRecord]:
        cursor = self.shared_conn.execute(
            """
            SELECT id, document_path, desc, type, begin_x, begin_y, end_x, end_y, is_ai
            FROM highlights
            WHERE document_path = ?
            """,
            (document_path,),
        )
        rows = cursor.fetchall()
        print(
            f"[db] fetched {len(rows)} highlights for document {document_path}",
            flush=True,
        )
        return [
            HighlightRecord(
                id=row["id"],
                document_path=row["document_path"],
                desc=row["desc"] or "",
                highlight_type=row["type"],
                begin_x=row["begin_x"],
                begin_y=row["begin_y"],
                end_x=row["end_x"],
                end_y=row["end_y"],
                is_ai=bool(row["is_ai"]) if "is_ai" in row.keys() else False,
            )
            for row in rows
        ]

    def find_highlight_near(
        self,
        document_path: str,
        x: float,
        y: float,
        tolerance: float = 48.0,
        highlight_type: Optional[str] = None,
        require_ai: bool = False,
    ) -> Optional[HighlightRecord]:
        candidates = self.list_highlights_for_document(document_path)
        best: Optional[HighlightRecord] = None
        best_score = float("inf")
        for record in candidates:
            if highlight_type and record.highlight_type != highlight_type:
                continue
            if require_ai and not record.is_ai:
                continue

            min_x = min(record.begin_x, record.end_x)
            max_x = max(record.begin_x, record.end_x)
            min_y = min(record.begin_y, record.end_y)
            max_y = max(record.begin_y, record.end_y)

            in_y_range = min_y - tolerance <= y <= max_y + tolerance
            if not in_y_range:
                continue

            if min_x - tolerance <= x <= max_x + tolerance:
                horizontal_penalty = 0.0
            else:
                horizontal_penalty = min(abs(x - min_x), abs(x - max_x))

            if min_y <= y <= max_y:
                vertical_penalty = 0.0
            else:
                vertical_penalty = min(abs(y - min_y), abs(y - max_y))

            score = vertical_penalty * 2 + horizontal_penalty
            if score < best_score:
                best_score = score
                best = record

        if best:
            info = {
                "id": best.id,
                "type": best.highlight_type,
                "is_ai": best.is_ai,
                "desc": best.desc[:80],
                "begin": (best.begin_x, best.begin_y),
                "end": (best.end_x, best.end_y),
                "score": best_score,
            }
            print("[db] highlight match", info, flush=True)
        else:
            print(
                "[db] no highlight match",
                {
                    "document_path": document_path,
                    "x": x,
                    "y": y,
                    "tolerance": tolerance,
                    "highlight_type": highlight_type,
                    "require_ai": require_ai,
                },
                flush=True,
            )
        return best

    def delete_session(self, session_id: int) -> None:
        self.shared_conn.execute("DELETE FROM ai_sessions WHERE id = ?", (session_id,))
        self.shared_conn.commit()

    def close(self) -> None:
        self.local_conn.close()
        self.shared_conn.close()
