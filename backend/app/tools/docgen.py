"""
Document generation tool: fill the approval-note Word template with real
inspection data and write out a finished .docx file.

Uses docxtpl (Jinja2-in-docx) against the template at
backend/templates/approval_note.docx -- never builds the .docx from
scratch at request time, so the layout/branding stays under version
control and editable in Word.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict

from docxtpl import DocxTemplate

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "approval_note.docx"

# Where generated approval notes are written. Each call gets a unique
# filename so concurrent requests (or repeated runs) never overwrite one
# another's output.
OUTPUT_DIR = os.path.expanduser("~/.krypto_output/approval_notes")


def generate_approval_note(data: dict) -> str:
    """
    Fill the approval-note template with `data` and write a real .docx file.

    Args:
        data: dict with keys
            title: str
            date: str
            prepared_by: str
            findings_summary: str
            corrosion_table: list of dicts, each with
                location, previous_thickness, current_thickness,
                corrosion_rate, remaining_life, status
            sop_citations: list of dicts, each with doc_name, page, text
            recommendation: str
            required_thickness: float -- the SOP's minimum allowable
                thickness, used to validate each row's status below

    Returns:
        Absolute path to the generated .docx file.

    Raises:
        ValueError: if any corrosion_table row is missing a real calculated
            corrosion_rate/remaining_life, or if a row's status contradicts
            the real numbers (e.g. marked "Replace" when current_thickness
            is actually above required_thickness). Both are hard rejects --
            an approval note must never be generated from invented or
            self-contradictory numbers. The caller (the agent's retry loop)
            is expected to catch this and try again with correct data.
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Approval note template not found at {TEMPLATE_PATH}")

    required_keys = (
        "title",
        "date",
        "prepared_by",
        "findings_summary",
        "corrosion_table",
        "sop_citations",
        "recommendation",
        "required_thickness",
    )
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise ValueError(f"generate_approval_note: missing required keys: {missing}")

    corrosion_table = data["corrosion_table"]
    required_thickness = data["required_thickness"]

    null_rows = [
        row.get("location", "?")
        for row in corrosion_table
        if row.get("corrosion_rate") is None or row.get("remaining_life") is None
    ]
    if null_rows:
        raise ValueError(
            "generate_approval_note REJECTED: corrosion_table has missing "
            f"corrosion_rate/remaining_life for location(s) {null_rows}. Every "
            "row must carry the real values already computed by "
            "calculate_corrosion -- never leave these null and never invent "
            "them. Look for the SYSTEM-COMPUTED corrosion_table in the "
            "results from previous steps and copy those exact numbers."
        )

    contradicted_rows = []
    for row in corrosion_table:
        status = str(row.get("status", "")).lower()
        current_thickness = row.get("current_thickness")
        if "replace" in status and current_thickness is not None and current_thickness > required_thickness:
            contradicted_rows.append(
                f"{row.get('location', '?')} (current_thickness={current_thickness}mm is "
                f"above required_thickness={required_thickness}mm but marked {row.get('status')!r})"
            )
    if contradicted_rows:
        raise ValueError(
            "generate_approval_note REJECTED: recommendation status contradicts "
            f"the real numbers for: {'; '.join(contradicted_rows)}. A location "
            "can only be marked 'Replace' when its current_thickness is at or "
            "below required_thickness. Fix the status for these locations to "
            "match the real numbers (e.g. 'Safe, continue monitoring')."
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    tpl = DocxTemplate(str(TEMPLATE_PATH))
    tpl.render(data)

    out_name = f"approval_note_{uuid.uuid4().hex[:12]}.docx"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    tpl.save(out_path)

    return out_path
