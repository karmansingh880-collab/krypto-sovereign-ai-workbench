"""
Targeted test closing the gap on the 2 tools not yet exercised through a
real agent run: describe_image and search_knowledge (extract_text,
calculate_corrosion, and run_code_sandbox were already proven via
test_agent_tools.py and test_agent_sandbox.py).

Run with:
    ./venv/bin/python -m backend.app.agent.test_agent_remaining_tools
"""

import tempfile
from pathlib import Path

from backend.app.agent.graph import run_agent
from backend.app.agent.test_agent_tools import make_inspection_report_image
from backend.app.rag.ingest import ingest_document

KB_TEXT = """
Krypto corrosion inspection standard: any pipeline section with a
calculated remaining life under 2 years must be flagged for immediate
replacement, per MRPL internal safety guideline INS-04.
"""


def main():
    tmp_dir = tempfile.mkdtemp()
    image_path = str(Path(tmp_dir) / "inspection_report.png")
    make_inspection_report_image(image_path)

    kb_path = str(Path(tmp_dir) / "kb_doc.txt")
    with open(kb_path, "w") as f:
        f.write(KB_TEXT)
    print(f"Ingesting knowledge base doc: {kb_path}")
    ingest_document(kb_path)

    goal = (
        f"Look at the image at {image_path} and describe what asset it refers "
        "to. Separately, search the internal knowledge base for the safety "
        "guideline about when a pipeline section must be flagged for "
        "replacement, and report what you find."
    )

    print("=" * 70)
    print("Starting agent run to exercise describe_image + search_knowledge")
    print(f"Goal: {goal}")
    print("=" * 70)

    final_state = run_agent(goal, files=[image_path])

    print("=" * 70)
    print("Run finished.")
    print(f"Steps completed: {final_state['current_step']}/{len(final_state['plan'])}")
    print()
    print("Tool usage trace:")
    for i, obs in enumerate(final_state["observations"], 1):
        tool = obs.get("tool_used") or "(none -- plain model reply)"
        status = "ERROR: " + obs["error"] if obs.get("error") else "ok"
        print(f"  {i}. step={obs['step']!r}")
        print(f"     model={obs['model_tag']} tool={tool} status={status}")
        if obs.get("tool_used") in ("describe_image", "search_knowledge"):
            print(f"     args={obs.get('tool_args')}")
            print(f"     real result={obs.get('result')!r}")
    print("=" * 70)


if __name__ == "__main__":
    main()
