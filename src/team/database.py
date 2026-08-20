"""Database module - SQLite database operations for team management"""
import sqlite3
import json
import time
from pathlib import Path
from typing import Optional, Any


class Database:
    """SQLite database wrapper for team data persistence"""

    def __init__(self, team_name: str, db_path: Optional[str] = None):
        """
        @brief Initialize database connection and create tables

        @param team_name Name of the team (used for database path)
        @param db_path Optional custom database path
        """
        if db_path is None:
            home = Path.home()
            db_dir = home / ".nexus" / "teams" / team_name
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "database.sqlite")

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._cursor = self._conn.cursor()
        self._init_tables()

    def _init_tables(self) -> None:
        """@brief Create events and worktrees tables if they don't exist"""
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                task_id INTEGER,
                worktree_name TEXT,
                metadata TEXT,
                created_at REAL NOT NULL
            )
        """)

        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS worktrees (
                name TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                branch TEXT,
                task_id INTEGER,
                status TEXT DEFAULT 'active',
                created_at REAL NOT NULL,
                removed_at REAL
            )
        """)
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        @brief Execute a SQL statement

        @param sql SQL statement to execute
        @param params Parameters for the SQL statement
        @return Cursor object
        """
        return self._cursor.execute(sql, params)

    def commit(self) -> None:
        """@brief Commit the current transaction"""
        self._conn.commit()

    def execute_and_commit(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        @brief Execute a SQL statement and commit

        @param sql SQL statement to execute
        @param params Parameters for the SQL statement
        @return Cursor object
        """
        cursor = self._cursor.execute(sql, params)
        self._conn.commit()
        return cursor

    def insert_event(
        self,
        event_type: str,
        task_id: Optional[int] = None,
        worktree_name: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """
        @brief Insert a new event record

        @param event_type Type of the event
        @param task_id Associated task ID
        @param worktree_name Associated worktree name
        @param metadata Additional metadata as dict
        @return ID of the inserted row
        """
        metadata_json = json.dumps(metadata) if metadata else None
        cursor = self._cursor.execute(
            """
            INSERT INTO events (event_type, task_id, worktree_name, metadata, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_type, task_id, worktree_name, metadata_json, time.time()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_events(
        self,
        event_type: Optional[str] = None,
        task_id: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        @brief Retrieve events with optional filters

        @param event_type Filter by event type
        @param task_id Filter by task ID
        @param limit Maximum number of events to return
        @return List of event records as dicts
        """
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []

        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if task_id is not None:
            sql += " AND task_id = ?"
            params.append(task_id)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._cursor.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def insert_worktree(
        self,
        name: str,
        path: str,
        branch: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> bool:
        """
        @brief Insert a new worktree record

        @param name Worktree name (primary key)
        @param path Worktree path
        @param branch Git branch name
        @param task_id Associated task ID
        @return True if inserted, False if already exists
        """
        try:
            self._cursor.execute(
                """
                INSERT INTO worktrees (name, path, branch, task_id, status, created_at)
                VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (name, path, branch, task_id, time.time()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_worktree_status(self, name: str, status: str) -> bool:
        """
        @brief Update worktree status

        @param name Worktree name
        @param status New status value
        @return True if updated, False if not found
        """
        removed_at = time.time() if status == "removed" else None
        cursor = self._cursor.execute(
            """
            UPDATE worktrees SET status = ?, removed_at = ?
            WHERE name = ?
            """,
            (status, removed_at, name),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_worktree(self, name: str) -> Optional[dict]:
        """
        @brief Get a worktree by name

        @param name Worktree name
        @return Worktree record as dict or None
        """
        row = self._cursor.execute(
            "SELECT * FROM worktrees WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    def get_worktrees(
        self,
        status: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> list[dict]:
        """
        @brief Retrieve worktrees with optional filters

        @param status Filter by status
        @param task_id Filter by task ID
        @return List of worktree records as dicts
        """
        sql = "SELECT * FROM worktrees WHERE 1=1"
        params: list[Any] = []

        if status:
            sql += " AND status = ?"
            params.append(status)
        if task_id is not None:
            sql += " AND task_id = ?"
            params.append(task_id)

        sql += " ORDER BY created_at DESC"

        rows = self._cursor.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """@brief Close the database connection"""
        self._conn.close()

    def __enter__(self) -> "Database":
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit"""
        self._conn.close()
