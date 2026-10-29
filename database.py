import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                pdf_path TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                log_path TEXT NOT NULL,
                zip_path TEXT,
                device TEXT NOT NULL DEFAULT 'cpu',
                tier TEXT NOT NULL DEFAULT 'standard',
                pages TEXT NOT NULL DEFAULT 'all',
                status TEXT NOT NULL DEFAULT 'pending',
                progress_msg TEXT DEFAULT 'Queued',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
        conn.commit()

def add_task(filename: str, pdf_path: str, output_dir: str, log_path: str, device: str = "cpu", tier: str = "standard", pages: str = "all") -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (filename, pdf_path, output_dir, log_path, device, tier, pages, status, progress_msg, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'Queued for processing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (filename, pdf_path, output_dir, log_path, device, tier, pages))
        conn.commit()
        return cursor.lastrowid

def get_all_tasks() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks ORDER BY id DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_next_pending_task() -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM tasks 
            WHERE status = 'pending' 
            ORDER BY id ASC 
            LIMIT 1
        """)
        row = cursor.fetchone()
        return dict(row) if row else None

def update_task_status(task_id: int, status: str, progress_msg: Optional[str] = None, zip_path: Optional[str] = None, error_message: Optional[str] = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status in ("completed", "failed") else None
        
        updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = [status]

        if progress_msg is not None:
            updates.append("progress_msg = ?")
            params.append(progress_msg)
        
        if zip_path is not None:
            updates.append("zip_path = ?")
            params.append(zip_path)

        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)

        params.append(task_id)
        query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()

def delete_task(task_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        task_data = dict(row)
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return task_data
