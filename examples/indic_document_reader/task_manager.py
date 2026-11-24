"""
Background Task Manager — Enables asynchronous execution of heavy OCR, Translation,
and TTS tasks so users can navigate across tabs without blocking or losing progress.
"""

import concurrent.futures
import threading
import time
import uuid
from typing import Callable, Any, Dict, List, Optional
import streamlit as st

# Global thread pool executor
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=6)
_TASKS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def submit_background_task(
    name: str,
    page_route: str,
    target_fn: Callable[..., Any],
    *args,
    **kwargs,
) -> str:
    """
    Submit a task to run asynchronously in the background.
    Returns a unique task_id.
    """
    task_id = str(uuid.uuid4())[:8]
    with _LOCK:
        _TASKS[task_id] = {
            "id": task_id,
            "name": name,
            "page_route": page_route,
            "status": "running",  # 'running', 'completed', 'failed'
            "result": None,
            "error": None,
            "created_at": time.time(),
            "completed_at": None,
        }

    def _worker():
        try:
            res = target_fn(*args, **kwargs)
            with _LOCK:
                if task_id in _TASKS:
                    _TASKS[task_id]["status"] = "completed"
                    _TASKS[task_id]["result"] = res
                    _TASKS[task_id]["completed_at"] = time.time()
        except Exception as e:
            with _LOCK:
                if task_id in _TASKS:
                    _TASKS[task_id]["status"] = "failed"
                    _TASKS[task_id]["error"] = str(e)
                    _TASKS[task_id]["completed_at"] = time.time()

    _EXECUTOR.submit(_worker)
    return task_id


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Get the current state and result of a task."""
    with _LOCK:
        return _TASKS.get(task_id)


def get_active_tasks() -> List[Dict[str, Any]]:
    """List all currently executing tasks."""
    with _LOCK:
        return [t for t in _TASKS.values() if t["status"] == "running"]


def get_recent_completed_tasks() -> List[Dict[str, Any]]:
    """List tasks completed within the last 5 minutes."""
    cutoff = time.time() - 300
    with _LOCK:
        return [
            t for t in _TASKS.values()
            if t["status"] == "completed" and (t.get("completed_at") or 0) > cutoff
        ]


def render_background_tasks_banner():
    """
    Renders a live floating/sidebar notification banner if tasks are running
    or recently completed, allowing the user to navigate freely.
    """
    active = get_active_tasks()
    completed = get_recent_completed_tasks()

    if active:
        for t in active:
            st.info(
                f"[PROCESSING] Background task '{t['name']}' is running. "
                f"You can switch tabs and work freely — results will be available upon return."
            )

    if completed:
        for t in completed:
            col_msg, col_link = st.columns([3, 1])
            with col_msg:
                st.success(f"[COMPLETED] Task '{t['name']}' finished execution.")
            with col_link:
                if t.get("page_route"):
                    st.page_link(t["page_route"], label="View Results →")

