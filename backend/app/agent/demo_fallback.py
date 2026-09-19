"""
Demo-mode guarantee for the approval note.

Small local models (e.g. llama3.2:3b) cannot reliably emit the large nested
JSON that generate_approval_note needs, and one truncated reply means no
deliverable. In demo mode (KRYPTO_DEMO_MODEL set) the graph therefore builds
the note itself from the data already computed in code -- the OCR readings,
the forced calculate_corrosion table, and the SOP chunks retrieved from the
knowledge base -- and calls the real generate_approval_note tool (including
its validation). Nothing here is invented: every number comes from those
observations. With the locked models this module is never used.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

from backend.app import live
from backend.app.agent.state import AgentState, Observation
from backend.app.router.router import demo_model
from backend.app.tools.docgen import generate_approval_note

HIGH_CORROSION_RATE = 0.25      # SOP-CORR-014 section 4 (mm/year)
MODERATE_CORROSION_RATE = 0.1   # SOP-CORR-014 section 4 (mm/year)
MIN_REMAINING_LIFE_YEARS = 10   # SOP-CORR-014 section 3


def _first_match(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _status_for(row: Dict[str, Any], t_min: float) -> str:
    if row["current_thickness"] <= t_min:
        return f"Replace: at or below t-min ({t_min} mm)"
    if row["corrosion_rate"] > HIGH_CORROSION_RATE or row["remaining_life"] < MIN_REMAINING_LIFE_YEARS:
        return "High corrosion / short remaining life: engineer sign-off and follow-up UT within 6 months"
    if row["corrosion_rate"] >= MODERATE_CORROSION_RATE:
        return "Moderate corrosion: monitor closely"
    return "Low corrosion: standard monitoring"


def _collect(state: AgentState) -> Dict[str, Any]:
    ocr_text = ""
    forced_calc: Optional[Observation] = None
    sop_chunks: List[Dict[str, Any]] = []
    seen_text = set()

    for obs in state["observations"]:
        if obs.get("error"):
            continue
        tool = obs.get("tool_used")
        if tool == "extract_text" and isinstance(obs.get("result"), str) and not ocr_text:
            ocr_text = obs["result"]
        elif tool == "calculate_corrosion" and obs.get("forced"):
            forced_calc = obs
        elif tool == "search_knowledge" and isinstance(obs.get("result"), list):
            for chunk in obs["result"]:
                if chunk.get("text") and chunk["text"] not in seen_text:
                    seen_text.add(chunk["text"])
                    sop_chunks.append(chunk)

    return {"ocr_text": ocr_text, "forced_calc": forced_calc, "sop_chunks": sop_chunks}


def build_note_data(state: AgentState) -> Optional[Dict[str, Any]]:
    collected = _collect(state)
    forced_calc = collected["forced_calc"]
    if forced_calc is None:
        return None

    t_min = forced_calc["result"]["required_thickness"]
    table = [
        {**row, "status": _status_for(row, t_min)}
        for row in forced_calc["result"]["corrosion_table"]
    ]

    ocr_text = collected["ocr_text"]
    lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]
    report_title = next((ln for ln in lines if "REPORT" in ln.upper()), "Piping Wall Thickness Inspection Report")
    inspection_date = _first_match(r"Inspection Date:\s*([\d-]+)", ocr_text)
    inspector = _first_match(r"Inspector:\s*(.+)", ocr_text)

    action_rows = [r for r in table if r["status"].startswith(("Replace", "High"))]

    summary = (
        f"{len(table)} locations were assessed against SOP-CORR-014 (minimum allowable wall "
        f"thickness {t_min} mm). Corrosion rate and remaining life were computed from the two "
        f"inspection readings for each location. "
    )
    if action_rows:
        summary += "Attention required: " + "; ".join(
            f"{r['location']} ({r['corrosion_rate']} mm/yr, {r['remaining_life']} years remaining)"
            for r in action_rows
        ) + "."
    else:
        summary += "No location requires immediate action."

    if action_rows:
        recommendation = (
            "Do not approve continued service for "
            + ", ".join(r["location"] for r in action_rows)
            + " without unit inspection engineer sign-off (SOP-CORR-014 section 5); schedule follow-up "
            "ultrasonic thickness testing within 6 months. Remaining locations may continue on their "
            "standard monitoring interval."
        )
    else:
        recommendation = "All locations may continue in service on the standard inspection interval."

    return {
        "title": f"Approval Note: {report_title.title()}",
        "date": date.today().isoformat(),
        "prepared_by": "Krypto AI Agent"
        + (f" (report inspector: {inspector}; inspection date: {inspection_date})" if inspector else ""),
        "findings_summary": summary,
        "required_thickness": t_min,
        "corrosion_table": table,
        "sop_citations": [
            {"doc_name": c["doc_name"], "page": c["page"], "text": c["text"][:400]}
            for c in collected["sop_chunks"][:3]
        ],
        "recommendation": recommendation,
    }


def ensure_approval_note(state: AgentState) -> bool:
    """
    Demo mode only. If the goal asks for an approval note and none has been
    produced yet, generate it from verified data. Returns True if a note now
    exists. Attempted at most once per run.
    """
    if not demo_model() or "approval note" not in state["goal"].lower():
        return False

    if any(o.get("tool_used") == "generate_approval_note" and not o.get("error") for o in state["observations"]):
        return True
    if any(o.get("forced") and o.get("tool_used") == "generate_approval_note" for o in state["observations"]):
        return False  # already tried and failed -- don't loop

    data = build_note_data(state)
    if data is None:
        return False

    observation: Observation = {
        "step": "[SYSTEM] generate approval note from verified computed data",
        "model_used": "none",
        "model_tag": "none",
        "reason": "demo-mode assist -- small model cannot reliably emit the full note JSON",
        "tool_used": "generate_approval_note",
        "tool_args": {"data": data},
        "response": "",
        "result": None,
        "error": None,
        "forced": True,
    }
    live.start("note", "Drafting the approval note", "Filling in the Word template")
    try:
        path = generate_approval_note(data)
        observation["result"] = path
        observation["response"] = path
        print(f"[FORCE-NOTE] approval note written from computed data -> {path}")
        live.done("note", "approval note (.docx) created")
    except Exception as exc:
        observation["error"] = str(exc)
        print(f"[FORCE-NOTE] failed: {exc}")
        live.fail("note", str(exc))

    state["observations"].append(observation)
    if observation["error"] is None:
        state["outputs"].append(observation["response"])
        return True
    return False
