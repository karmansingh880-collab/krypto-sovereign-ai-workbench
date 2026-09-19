"""
Live progress of a run: which step the AI is on, what comes next, and the answer as it is
being written. The browser watches this over a WebSocket (routes/ws.py).

Code that does the work (the agent, the file pipeline) reports through the small functions at
the bottom -- `stage(...)`, `plan(...)`, `token(...)`. They do nothing when no run is being
watched (tests, scripts), so calling them never changes how the work itself behaves.

A run's state lives in memory only while it runs (plus a few minutes after, for late viewers);
its stage timeline is saved with the task when it finishes.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Deque, Dict, Iterator, List, Optional, Tuple

MAX_STAGES = 80
MAX_TEXT_CHARS = 60_000
KEEP_AFTER_FINISH_SECONDS = 600
QUEUE_LIMIT = 800

Stage = Dict[str, Any]


class Subscriber:
    """One connected browser: an event queue bound to that connection's event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self.overflowed = False  # too slow to keep up: it will be sent a fresh snapshot instead


class LiveRun:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self._lock = threading.Lock()
        self._stages: List[Stage] = []
        self._subscribers: List[Subscriber] = []
        self.text = ""
        self.status = "queued"          # queued | running | success | failed
        self.message = ""
        self.started = time.time()
        self.finished_at: Optional[float] = None
        self.extras: Dict[str, Any] = {}  # e.g. the passages used to answer; saved with the task

    # ---------------------------------------------------------------- state
    def _find(self, key: str) -> Optional[Stage]:
        return next((s for s in self._stages if s["id"] == key), None)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "task_id": self.task_id,
                "status": self.status,
                "message": self.message,
                "elapsed": round((self.finished_at or time.time()) - self.started, 1),
                "stages": [dict(s) for s in self._stages],
                "text": self.text,
            }

    def timeline(self) -> List[Dict[str, Any]]:
        """The finished stages, small enough to store with the task."""
        with self._lock:
            return [
                {k: s.get(k) for k in ("id", "title", "status", "detail", "duration", "error")}
                for s in self._stages
                if s["status"] not in ("pending", "skipped")
            ][:MAX_STAGES]

    # ---------------------------------------------------------- subscribers
    def subscribe(self, loop: asyncio.AbstractEventLoop) -> Subscriber:
        sub = Subscriber(loop)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    def _emit(self, event: Dict[str, Any]) -> None:
        """Hand an event to every connected browser; safe to call from any thread."""
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub.loop.call_soon_threadsafe(self._deliver, sub, event)
            except RuntimeError:
                pass  # that connection's loop is already closed

    @staticmethod
    def _deliver(sub: Subscriber, event: Dict[str, Any]) -> None:
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            sub.overflowed = True

    # ------------------------------------------------------------- reporting
    def plan(self, items: List[Tuple[str, str]]) -> None:
        """Declare the steps expected, in order, so the panel can show what comes next."""
        with self._lock:
            for key, title in items[:MAX_STAGES]:
                if self._find(key) is None:
                    self._stages.append({"id": key, "title": title, "status": "pending", "detail": "",
                                         "current": None, "total": None, "started": None, "duration": None,
                                         "error": None})
        self._emit({"type": "plan", "stages": self.snapshot()["stages"]})

    def start(self, key: str, title: Optional[str] = None, detail: str = "") -> None:
        with self._lock:
            stage = self._find(key)
            if stage is None:
                if len(self._stages) >= MAX_STAGES:
                    return
                stage = {"id": key, "title": title or key, "status": "pending", "detail": "", "current": None,
                         "total": None, "started": None, "duration": None, "error": None}
                self._stages.append(stage)
            elif title:
                stage["title"] = title
            stage.update(status="running", detail=detail, started=time.time(), duration=None, error=None)
            snapshot = dict(stage)
        self._emit({"type": "stage", "stage": snapshot})

    def update(self, key: str, detail: Optional[str] = None, current: Optional[int] = None,
               total: Optional[int] = None) -> None:
        with self._lock:
            stage = self._find(key)
            if stage is None:
                return
            if detail is not None:
                stage["detail"] = detail
            if current is not None:
                stage["current"] = current
            if total is not None:
                stage["total"] = total
            snapshot = dict(stage)
        self._emit({"type": "stage", "stage": snapshot})

    def done(self, key: str, detail: Optional[str] = None) -> None:
        with self._lock:
            stage = self._find(key)
            if stage is None:
                return
            stage["status"] = "done"
            stage["duration"] = round(time.time() - (stage["started"] or time.time()), 1)
            if detail is not None:
                stage["detail"] = detail
            snapshot = dict(stage)
        self._emit({"type": "stage", "stage": snapshot})

    def fail(self, key: str, message: str) -> None:
        with self._lock:
            stage = self._find(key)
            if stage is None:
                return
            stage["status"] = "error"
            stage["error"] = message[:300]
            stage["duration"] = round(time.time() - (stage["started"] or time.time()), 1)
            snapshot = dict(stage)
        self._emit({"type": "stage", "stage": snapshot})

    def note(self, detail: str) -> None:
        """Change the detail line of the step currently running (e.g. a time estimate)."""
        with self._lock:
            running = [s for s in self._stages if s["status"] == "running"]
            if not running:
                return
            stage = running[-1]
            stage["detail"] = detail
            snapshot = dict(stage)
        self._emit({"type": "stage", "stage": snapshot})

    def token(self, piece: str) -> None:
        with self._lock:
            if len(self.text) >= MAX_TEXT_CHARS:
                return
            self.text += piece
        self._emit({"type": "token", "text": piece})

    def reset_text(self) -> None:
        with self._lock:
            self.text = ""
        self._emit({"type": "text_reset"})

    def set_status(self, status: str, message: str = "") -> None:
        with self._lock:
            self.status = status
            self.message = message
        self._emit({"type": "status", "status": status, "message": message})

    def finish(self, status: str, message: str = "") -> None:
        """The run is over: anything still marked running is closed off, then viewers are told."""
        now = time.time()
        with self._lock:
            for stage in self._stages:
                if stage["status"] == "running":
                    stage["status"] = "done" if status == "success" else "error"
                    stage["duration"] = round(now - (stage["started"] or now), 1)
                elif stage["status"] == "pending" and status == "success":
                    stage["status"] = "skipped"   # planned but not needed for this run
            self.status = status
            self.message = message
            self.finished_at = now
        self._emit({"type": "snapshot", "run": self.snapshot()})
        self._emit({"type": "status", "status": status, "message": message})


class Hub:
    """All runs currently (or very recently) being watched, by task id."""

    def __init__(self) -> None:
        self._runs: Dict[str, LiveRun] = {}
        self._lock = threading.Lock()

    def create(self, task_id: str) -> LiveRun:
        run = LiveRun(task_id)
        with self._lock:
            self._runs[task_id] = run
        return run

    def get(self, task_id: str) -> Optional[LiveRun]:
        with self._lock:
            return self._runs.get(task_id)

    def forget(self, task_id: str) -> None:
        with self._lock:
            self._runs.pop(task_id, None)

    def forget_later(self, task_id: str, loop: asyncio.AbstractEventLoop,
                     delay: float = KEEP_AFTER_FINISH_SECONDS) -> None:
        loop.call_later(delay, self.forget, task_id)


hub = Hub()

# ---------------------------------------------------------------------------
# What the working code calls. All of these are no-ops when nobody is watching.
# ---------------------------------------------------------------------------

_current: "contextvars.ContextVar[Optional[LiveRun]]" = contextvars.ContextVar("krypto_live_run", default=None)
_conversation: "contextvars.ContextVar[str]" = contextvars.ContextVar("krypto_conversation", default="")


def bind(run: Optional[LiveRun]) -> "contextvars.Token[Optional[LiveRun]]":
    return _current.set(run)


def unbind(token: "contextvars.Token[Optional[LiveRun]]") -> None:
    _current.reset(token)


def current() -> Optional[LiveRun]:
    return _current.get()


def watching() -> bool:
    return _current.get() is not None


def plan(items: List[Tuple[str, str]]) -> None:
    run = _current.get()
    if run:
        run.plan(items)


def start(key: str, title: Optional[str] = None, detail: str = "") -> None:
    run = _current.get()
    if run:
        run.start(key, title, detail)


def update(key: str, detail: Optional[str] = None, current: Optional[int] = None, total: Optional[int] = None) -> None:
    run = _current.get()
    if run:
        run.update(key, detail, current, total)


def done(key: str, detail: Optional[str] = None) -> None:
    run = _current.get()
    if run:
        run.done(key, detail)


def fail(key: str, message: str) -> None:
    run = _current.get()
    if run:
        run.fail(key, message)


def token(piece: str) -> None:
    run = _current.get()
    if run:
        run.token(piece)


def note(detail: str) -> None:
    run = _current.get()
    if run:
        run.note(detail)


def reset_text() -> None:
    run = _current.get()
    if run:
        run.reset_text()


def set_extra(name: str, value: Any) -> None:
    run = _current.get()
    if run:
        run.extras[name] = value


@contextmanager
def stage(key: str, title: str, detail: str = "") -> Iterator[None]:
    """`with live.stage("read", "Reading report.pdf"): ...` -- marks the step running, then done or failed."""
    start(key, title, detail)
    try:
        yield
    except BaseException as exc:
        fail(key, str(exc) or exc.__class__.__name__)
        raise
    else:
        done(key)


_labels: "contextvars.ContextVar[Dict[str, str]]" = contextvars.ContextVar("krypto_file_labels", default={})


def set_file_labels(mapping: Dict[str, str]) -> "contextvars.Token[Dict[str, str]]":
    """Original file names for the uploaded copies (which are stored under random names)."""
    return _labels.set(dict(mapping))


def file_label(path: str) -> str:
    return _labels.get().get(path) or os.path.basename(path)


def set_conversation(text: str) -> "contextvars.Token[str]":
    """Earlier question and answer of a follow-up, for the model to use as context."""
    return _conversation.set(text)


def conversation() -> str:
    return _conversation.get()
