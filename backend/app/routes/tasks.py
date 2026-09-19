"""
HTTP surface for the AI agent: submit a task, poll it, download its files.

Agent runs take minutes, so POST /tasks returns immediately with an id and
the run continues in the background; the UI polls GET /tasks/{id}. Runs are
serialized (one at a time) because the local Ollama models can't serve
several long agent loops at once on a single machine.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from pydantic import BaseModel

from backend.app import live, quality
from backend.app.agent import direct
from backend.app.agent.run_task import run_agent_task
from backend.app.auth.deps import current_user
from backend.app.core.config import BACKEND_DIR
from backend.app.db.mongo import db
from backend.app.outputs.export import FORMATS as EXPORT_FORMATS
from backend.app.outputs.export import ExportError, answer_of, export_answer
from backend.app.outputs.intent import detect as detect_intent
from backend.app.outputs.pipeline import add_corrosion_extras, run_content_task

router = APIRouter(prefix="/tasks", tags=["tasks"])

UPLOAD_DIR = Path.home() / ".krypto_uploads"
OUTPUT_ROOT = (Path.home() / ".krypto_output").resolve()
SAMPLE_REPORT = BACKEND_DIR / "test_data" / "inspection_report_unit4.png"
ALLOWED_SUFFIXES = {
    ".pdf", ".docx", ".txt", ".md", ".csv",
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024       # per file
MAX_TOTAL_UPLOAD_BYTES = 250 * 1024 * 1024  # per task
MAX_FILES = 5
MAX_GOAL_CHARS = 4000
UPLOAD_CHUNK = 1024 * 1024
# A run's step trace keeps at most this much text per step, so a huge document
# can't push the run's database record (16 MB limit) or the API response past a sane size.
MAX_STORED_RESPONSE = 20_000

_run_lock = asyncio.Lock()
_background: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cap_steps(steps: list) -> list:
    """Shorten oversized step responses (the full text was still used during the run)."""
    capped = []
    for step in steps:
        response = step.get("response")
        if isinstance(response, str) and len(response) > MAX_STORED_RESPONSE:
            step = {
                **step,
                "response": response[:MAX_STORED_RESPONSE]
                + f"\n… [{len(response) - MAX_STORED_RESPONSE:,} more characters not stored]",
            }
        capped.append(step)
    return capped


def _looks_like(suffix: str, head: bytes) -> bool:
    """Cheap sanity check so a renamed file fails fast with a clear message."""
    if suffix == ".pdf":
        return b"%PDF" in head[:1024]
    if suffix == ".docx":
        return head[:2] == b"PK"
    return True


async def _save_upload(upload: UploadFile, suffix: str, remaining_total: int) -> tuple[Path, int]:
    """Stream an upload to disk in chunks (never the whole file in memory) and enforce the size limits."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    size = 0
    try:
        with open(saved, "wb") as out:
            while chunk := await upload.read(UPLOAD_CHUNK):
                if size == 0 and not _looks_like(suffix, chunk):
                    raise HTTPException(
                        status_code=422,
                        detail=f"“{Path(upload.filename or '').name}” is not a valid {suffix[1:].upper()} file.",
                    )
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413, detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB each)"
                    )
                if size > remaining_total:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Files too large together (max {MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024)} MB in total)",
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail=f"“{Path(upload.filename or '').name}” is empty.")
    except BaseException:
        saved.unlink(missing_ok=True)
        raise
    return saved, size


def _attention_needed(row: Dict[str, Any]) -> bool:
    status = str(row.get("status", "")).lower()
    if status:
        return status.startswith(("replace", "high"))
    rate, life = row.get("corrosion_rate"), row.get("remaining_life")
    return (rate is not None and rate > 0.25) or (life is not None and life < 10)


def _extract_assessment(steps: list) -> Optional[Dict[str, Any]]:
    """Pull the corrosion table out of a finished run's steps (the approval
    note's data if there is one, else the calculation result)."""
    table, t_min = None, None
    for step in reversed(steps):
        if step.get("tool_used") == "generate_approval_note":
            data = (step.get("tool_args") or {}).get("data") or {}
            if isinstance(data.get("corrosion_table"), list) and not step.get("error"):
                table, t_min = data["corrosion_table"], data.get("required_thickness")
                break
    if table is None:
        for step in reversed(steps):
            if step.get("tool_used") == "calculate_corrosion" and not step.get("error"):
                try:
                    payload = json.loads(step.get("response") or "")
                except ValueError:
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("corrosion_table"), list):
                    table, t_min = payload["corrosion_table"], payload.get("required_thickness")
                    break
    if not table:
        return None
    return {
        "required_thickness": t_min,
        "table": table,
        "attention": [r.get("location") for r in table if _attention_needed(r)],
    }


def _remove_files(doc: Dict[str, Any]) -> None:
    """Delete the generated notes and uploaded copies belonging to a task.
    Only ever touches files inside our own output/upload folders."""
    candidates = list(doc.get("outputs", [])) + list(doc.get("files", []))
    for raw in candidates:
        path = Path(raw).resolve()
        for root in (OUTPUT_ROOT, UPLOAD_DIR.resolve()):
            if root in path.parents and path.is_file():
                path.unlink(missing_ok=True)


def _iso(dt: datetime) -> str:
    """Mongo hands back naive UTC datetimes; mark them UTC so browsers don't read them as local time."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _json_safe(value: Any) -> Any:
    """Replace inf/NaN (e.g. remaining life when nothing is corroding) with None; they are not valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _kind_of(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return "docx"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
        return "image"
    return "file"


def _serialize(doc: Dict[str, Any], with_steps: bool = True) -> Dict[str, Any]:
    steps = doc.get("steps", [])
    out = {
        "id": str(doc["_id"]),
        "goal": doc["goal"],
        "files": doc.get("input_names") or [Path(p).name for p in doc.get("files", [])],
        "formats": doc.get("formats", []),
        "assessment": _json_safe(doc.get("assessment") or (_extract_assessment(steps) if steps else None)),
        "status": doc["status"],
        "final_message": doc.get("final_message"),
        "outputs": [
            {"index": i, "name": Path(p).name, "kind": _kind_of(p)} for i, p in enumerate(doc.get("outputs", []))
        ],
        "created_at": _iso(doc["created_at"]),
        "finished_at": _iso(doc["finished_at"]) if doc.get("finished_at") else None,
        "parent_id": str(doc["parent_id"]) if doc.get("parent_id") else None,
    }
    if with_steps:
        out["steps"] = _json_safe(steps)
        out["timeline"] = doc.get("timeline", [])
        out["extras"] = _json_safe(doc.get("extras", {}))
    return out


def _object_id(task_id: str) -> ObjectId:
    if not ObjectId.is_valid(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return ObjectId(task_id)


def _ollama_problem() -> Optional[str]:
    """None if the Ollama server answers, else a short reason."""
    try:
        import ollama

        ollama.list()
        return None
    except Exception as exc:
        return str(exc)[:200]


def _conversation_text(parent: Dict[str, Any]) -> str:
    """What a follow-up question should know about the run it continues."""
    assessment = parent.get("assessment")
    if assessment:
        flagged = ", ".join(assessment.get("attention") or []) or "none"
        answer = (
            f"{len(assessment.get('table', []))} locations were assessed; those needing attention: {flagged}. "
            f"Minimum allowable thickness {assessment.get('required_thickness')} mm."
        )
    else:
        text = answer_of(parent.get("steps", []))
        if "```" in text:
            # An answer with code: keep its line breaks (code is unreadable without them) and allow more of it,
            # so a follow-up like "now make it handle a list" can build on the code.
            answer = text.strip()[:1800]
        else:
            answer = " ".join(text.split())[:600]
    return f"Question: {parent.get('goal', '')[:300]}\nAnswer: {answer}"


async def _execute(task_id: ObjectId, goal: str, file_paths: list[str],
                   formats: list[str], names: list[str], conversation: str = "") -> None:
    run = live.hub.get(str(task_id)) or live.hub.create(str(task_id))
    live.bind(run)  # this asyncio task's own context: the worker threads it starts report to this run
    live.set_file_labels(dict(zip(file_paths, names)))
    if conversation:
        live.set_conversation(conversation)

    async with _run_lock:
        run.set_status("running")
        await db.tasks.update_one({"_id": task_id}, {"$set": {"status": "running"}})
        problem = await asyncio.to_thread(_ollama_problem)
        if problem:
            message = f"Ollama is not reachable ({problem}). Start Ollama, then run the task again."
            await db.tasks.update_one({"_id": task_id}, {"$set": {
                "status": "failed",
                "final_message": message,
                "finished_at": _now(),
            }})
            run.finish("failed", message)
            live.hub.forget_later(str(task_id), asyncio.get_running_loop())
            return
        try:
            intent = detect_intent(goal, formats, has_file=bool(file_paths))
            if intent.wants_files and not intent.corrosion:
                # An explicit request for a Word/PDF file or an image.
                result = await run_content_task(goal, file_paths, intent, names)
            else:
                result = await run_agent_task(goal, file_paths)
                if intent.corrosion:
                    result = await add_corrosion_extras(result, intent)
            assessment = _extract_assessment(result["steps"])
            update = {
                "status": result["status"],
                "steps": _cap_steps(result["steps"]),
                "outputs": result["outputs"],
                "final_message": result["final_message"],
                "assessment": assessment,
                "extras": await _answer_extras(goal, result["steps"], assessment, run),
            }
        except Exception as exc:  # run_agent_task already guards, this is the last line of defence
            update = {"status": "failed", "final_message": f"Task crashed: {exc}"}
        update["finished_at"] = _now()
        run.finish(update["status"], update.get("final_message") or "")
        update["timeline"] = run.timeline()
        await db.tasks.update_one({"_id": task_id}, {"$set": update})
        live.hub.forget_later(str(task_id), asyncio.get_running_loop())


async def _answer_extras(goal: str, steps: list, assessment: Optional[Dict[str, Any]],
                         run: "live.LiveRun") -> Dict[str, Any]:
    """Which passages the answer used, and any facts in it that the document does not contain."""
    extras: Dict[str, Any] = {}
    sources = run.extras.get("sources")
    if sources:
        extras["sources"] = sources
    if run.extras.get("code_check"):
        extras["code_check"] = run.extras["code_check"]
    answer = answer_of(steps)
    if answer and not assessment:
        document = "\n".join(
            str(s.get("response") or "") for s in steps
            if s.get("tool_used") == "extract_text" and not s.get("error")
        )
        missing = await asyncio.to_thread(quality.unverified_facts, answer, document, goal)
        if missing:
            extras["unverified"] = missing
    return extras


# Words that show a request is about the attached report/document (or refers to "this"): then the sample stays attached.
_REFERS_TO_DOCUMENT = re.compile(
    r"\b(report|inspection|thickness|corrosion|pipes?|piping|wall|sop|remaining life|approval|readings?|locations?|"
    r"document|file|image|picture|scanned?|table|attached|above|figure|chart|graph|pdf|page|summari[sz]e|this|these)\b", re.I)


def sample_is_irrelevant(goal: str) -> bool:
    """True when a request plainly has nothing to do with the bundled sample report.

    The sample is the composer's default source, so someone who just types "simple code for python" or
    "what is the capital of Australia?" would otherwise get an answer built from an inspection report.
    Code requests never need it; a plain question that does not refer to a document does not either.
    When in doubt the sample stays attached (the original behaviour).
    """
    from backend.app.router.router import demo_model

    if not demo_model() or "approval note" in goal.lower():
        return False
    kind = direct.kind_of(goal)
    if kind.startswith("code"):
        return not re.search(r"\b(report|inspection|thickness|corrosion|sop|attached|this (file|document|report))\b", goal, re.I)
    return not _REFERS_TO_DOCUMENT.search(goal)


@router.post("", status_code=202)
async def create_task(
    goal: str = Form(...),
    file: Optional[UploadFile] = File(None),
    files: Optional[List[UploadFile]] = File(None),
    use_sample: bool = Form(False),
    formats: str = Form(""),
    user: Dict[str, Any] = Depends(current_user),
):
    goal = goal.strip()
    chosen_formats = [f for f in (x.strip().lower() for x in formats.split(",")) if f in ("docx", "pdf", "image")]
    if not goal:
        raise HTTPException(status_code=422, detail="goal must not be empty")
    if len(goal) > MAX_GOAL_CHARS:
        raise HTTPException(status_code=422, detail=f"The instruction is too long (max {MAX_GOAL_CHARS} characters).")

    file_paths: list[str] = []
    input_names: list[str] = []
    if use_sample and sample_is_irrelevant(goal):
        use_sample = False  # the composer's default source: not what this request is about
    if use_sample:
        file_paths.append(str(SAMPLE_REPORT))
        input_names.append(f"{SAMPLE_REPORT.name} (sample)")
    else:
        uploads = [u for u in ([file] if file is not None else []) + list(files or []) if u.filename]
        if len(uploads) > MAX_FILES:
            raise HTTPException(status_code=422, detail=f"Attach at most {MAX_FILES} files per task.")
        for upload in uploads:  # check every type first, so nothing is written for a bad request
            suffix = Path(upload.filename).suffix.lower()
            if suffix == ".doc":
                raise HTTPException(
                    status_code=422,
                    detail="Old .doc files are not supported. Save the document as .docx and upload that.",
                )
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_SUFFIXES)}",
                )
        total = 0
        try:
            for upload in uploads:
                saved, size = await _save_upload(
                    upload, Path(upload.filename).suffix.lower(), MAX_TOTAL_UPLOAD_BYTES - total
                )
                total += size
                file_paths.append(str(saved))
                input_names.append(Path(upload.filename).name)
        except BaseException:
            for path in file_paths:  # a later file was rejected: don't leave the earlier ones behind
                Path(path).unlink(missing_ok=True)
            raise

    return await _start_task(user, goal, file_paths, input_names, chosen_formats)


async def _start_task(user: Dict[str, Any], goal: str, file_paths: list[str], input_names: list[str],
                      formats: list[str], parent_id: Optional[ObjectId] = None,
                      conversation: str = "") -> Dict[str, Any]:
    """Record a new run and start it in the background (it waits its turn: runs are one at a time)."""
    doc: Dict[str, Any] = {
        "user_id": user["_id"],
        "goal": goal,
        "files": file_paths,
        "input_names": input_names,
        "formats": formats,
        "status": "queued",
        "steps": [],
        "outputs": [],
        "final_message": None,
        "created_at": _now(),
        "finished_at": None,
    }
    if parent_id:
        doc["parent_id"] = parent_id
    inserted = await db.tasks.insert_one(doc)
    live.hub.create(str(inserted.inserted_id))

    task = asyncio.create_task(
        _execute(inserted.inserted_id, goal, file_paths, formats, input_names, conversation)
    )
    _background.add(task)
    task.add_done_callback(_background.discard)

    return {"id": str(inserted.inserted_id), "status": "queued"}


@router.get("")
async def list_tasks(limit: int = 20, user: Dict[str, Any] = Depends(current_user)):
    limit = max(1, min(limit, 100))
    cursor = db.tasks.find({"user_id": user["_id"]}, {"steps": 0}).sort("created_at", -1).limit(limit)
    docs = [doc async for doc in cursor]
    for doc in docs:
        # Runs saved before the summary existed: compute it once from their steps and store it.
        if "assessment" not in doc and doc["status"] not in ("queued", "running"):
            full = await db.tasks.find_one({"_id": doc["_id"]}, {"steps": 1})
            doc["assessment"] = _extract_assessment((full or {}).get("steps", []))
            await db.tasks.update_one({"_id": doc["_id"]}, {"$set": {"assessment": doc["assessment"]}})
    return [_serialize(doc, with_steps=False) for doc in docs]


@router.delete("")
async def clear_finished_tasks(user: Dict[str, Any] = Depends(current_user)):
    """Delete every finished (success/failed) task of this user; queued/running ones stay."""
    docs = [
        d async for d in db.tasks.find(
            {"user_id": user["_id"], "status": {"$nin": ["queued", "running"]}}
        )
    ]
    for doc in docs:
        _remove_files(doc)
    result = await db.tasks.delete_many({"_id": {"$in": [d["_id"] for d in docs]}})
    return {"deleted": result.deleted_count}


@router.delete("/{task_id}")
async def delete_task(task_id: str, user: Dict[str, Any] = Depends(current_user)):
    doc = await db.tasks.find_one({"_id": _object_id(task_id), "user_id": user["_id"]})
    if doc is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if doc["status"] in ("queued", "running"):
        raise HTTPException(status_code=409, detail="Task is still active")
    _remove_files(doc)
    await db.tasks.delete_one({"_id": doc["_id"]})
    return {"deleted": task_id}


@router.get("/{task_id}")
async def get_task(task_id: str, user: Dict[str, Any] = Depends(current_user)):
    doc = await db.tasks.find_one({"_id": _object_id(task_id), "user_id": user["_id"]})
    if doc is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _serialize(doc)


class FollowUpBody(BaseModel):
    goal: str
    formats: List[str] = []


class ExportBody(BaseModel):
    format: str


@router.post("/{task_id}/followup", status_code=202)
async def follow_up(task_id: str, body: FollowUpBody, user: Dict[str, Any] = Depends(current_user)):
    """Ask more about the same files: starts a new run that knows the earlier question and answer."""
    parent = await db.tasks.find_one({"_id": _object_id(task_id), "user_id": user["_id"]})
    if parent is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if parent["status"] != "success":
        raise HTTPException(status_code=409, detail="Wait until that run has finished successfully.")
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(status_code=422, detail="Type your follow-up question.")
    if len(goal) > MAX_GOAL_CHARS:
        raise HTTPException(status_code=422, detail=f"The question is too long (max {MAX_GOAL_CHARS} characters).")
    formats = [f for f in (x.strip().lower() for x in body.formats) if f in EXPORT_FORMATS]

    # Copy the input files: the follow-up must keep working even if the original run is deleted.
    file_paths: list[str] = []
    try:
        for original in parent.get("files", []):
            source = Path(original)
            if not source.is_file():
                raise HTTPException(status_code=409, detail="The files of that run are no longer available.")
            if source.resolve() == SAMPLE_REPORT.resolve():
                file_paths.append(str(source))  # the bundled sample is shared and never deleted
                continue
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            copy = UPLOAD_DIR / f"{uuid.uuid4().hex}{source.suffix.lower()}"
            await asyncio.to_thread(shutil.copyfile, source, copy)
            file_paths.append(str(copy))
    except BaseException:
        for path in file_paths:
            if Path(path).resolve() != SAMPLE_REPORT.resolve():
                Path(path).unlink(missing_ok=True)
        raise

    return await _start_task(
        user, goal, file_paths, list(parent.get("input_names", [])), formats,
        parent_id=parent["_id"], conversation=_conversation_text(parent),
    )


@router.post("/{task_id}/export")
async def export_task(task_id: str, body: ExportBody, user: Dict[str, Any] = Depends(current_user)):
    """Save a finished run's answer as a Word file, PDF or image (instant: no model is used)."""
    doc = await db.tasks.find_one({"_id": _object_id(task_id), "user_id": user["_id"]})
    if doc is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if doc["status"] != "success":
        raise HTTPException(status_code=409, detail="Only a finished, successful run can be exported.")
    fmt = body.format.strip().lower()
    fmt = "docx" if fmt == "word" else fmt
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail="Choose Word, PDF or image.")

    outputs = list(doc.get("outputs", []))
    known = (doc.get("exports") or {}).get(fmt)
    if known and known in outputs and Path(known).is_file():
        path, created = known, False
    else:
        try:
            path = await asyncio.to_thread(
                export_answer, doc.get("steps", []), doc["goal"], doc.get("assessment"), fmt, outputs
            )
        except ExportError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        created = path not in outputs
        if created:
            outputs.append(path)
            await db.tasks.update_one(
                {"_id": doc["_id"]}, {"$set": {"outputs": outputs, f"exports.{fmt}": path}}
            )
    index = outputs.index(path)
    return {"output": {"index": index, "name": Path(path).name, "kind": _kind_of(path)}, "created": created}


@router.get("/{task_id}/files/{index}")
async def download_output(task_id: str, index: int, user: Dict[str, Any] = Depends(current_user)):
    doc = await db.tasks.find_one({"_id": _object_id(task_id), "user_id": user["_id"]})
    if doc is None:
        raise HTTPException(status_code=404, detail="Task not found")
    outputs = doc.get("outputs", [])
    if not 0 <= index < len(outputs):
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(outputs[index]).resolve()
    if OUTPUT_ROOT not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name)
