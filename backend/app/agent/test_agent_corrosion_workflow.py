"""
End-to-end test: multi-location inspection report -> forced corrosion
calculations -> validated approval note.

This exercises the structural fix for calculate_corrosion being skippable:
force_corrosion_node (graph.py) calls it directly in code for every parsed
location the moment OCR readings + the SOP's required_thickness are both
in state, and docgen.generate_approval_note() hard-rejects (triggering a
retry) any corrosion_table with null values or a status that contradicts
the real numbers.

Run with:
    ./venv/bin/python -m backend.app.agent.test_agent_corrosion_workflow
"""

import asyncio
import json

from backend.app.agent.run_task import run_agent_task

GOAL = (
    "Read the inspection report, extract wall-thickness readings, "
    "calculate corrosion rate and remaining life for each location, cite "
    "the relevant SOP, and generate an approval note."
)
REPORT_PATH = "backend/test_data/inspection_report_unit4.png"


def main():
    print("=" * 70)
    print("Starting corrosion workflow test (forced calc + validated docgen)")
    print(f"Goal: {GOAL}")
    print(f"File: {REPORT_PATH}")
    print("=" * 70)

    result = asyncio.run(run_agent_task(GOAL, [REPORT_PATH]))

    print("=" * 70)
    print(f"status: {result['status']}")
    print(f"final_message: {result['final_message']}")
    print(f"outputs (files produced): {result['outputs']}")
    print()
    print("Full step trace:")
    for i, step in enumerate(result["steps"], 1):
        tool = step["tool_used"] or "(none)"
        err = f" ERROR: {step['error']}" if step["error"] else ""
        print(f"  {i}. [{tool}] {step['step']}{err}")
        if step["tool_used"] == "calculate_corrosion":
            print(f"     ARGS: {step['tool_args']}")
            print(f"     RESPONSE: {step['response']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
