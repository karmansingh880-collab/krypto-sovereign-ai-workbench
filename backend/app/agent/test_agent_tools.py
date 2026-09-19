"""
Integration test for Step 1: agent loop actually calling real tools.

Generates a sample "scanned inspection report" image, then runs the agent
on a goal that requires reading it (extract_text) and doing a real
corrosion calculation (calculate_corrosion) from the numbers found in it.

Run with:
    ./venv/bin/python -m backend.app.agent.test_agent_tools
"""

import tempfile
from pathlib import Path

from backend.app.agent.graph import run_agent


def make_inspection_report_image(path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    lines = [
        "MRPL INSPECTION REPORT",
        "Asset: Pipeline Section 7B",
        "Previous thickness: 10.0 mm",
        "Current thickness: 8.5 mm",
        "Years since last inspection: 5",
        "Required minimum thickness: 6.0 mm",
    ]

    font = None
    for font_path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if Path(font_path).exists():
            font = ImageFont.truetype(font_path, 28)
            break
    if font is None:
        font = ImageFont.load_default()

    image = Image.new("RGB", (700, 260), color="white")
    draw = ImageDraw.Draw(image)
    y = 20
    for line in lines:
        draw.text((20, y), line, fill="black", font=font)
        y += 40
    image.save(path)


def main():
    tmp_dir = tempfile.mkdtemp()
    image_path = str(Path(tmp_dir) / "inspection_report.png")

    print(f"Generating sample scanned inspection report: {image_path}")
    make_inspection_report_image(image_path)

    goal = (
        f"Read the scanned inspection report at {image_path}, extract the "
        "thickness findings from it, and calculate the corrosion rate and "
        "remaining life using those readings."
    )

    print("=" * 70)
    print("Starting Krypto agent test run (Step 1: real tool wiring)")
    print(f"Goal: {goal}")
    print("=" * 70)

    final_state = run_agent(goal, files=[image_path])

    print("=" * 70)
    print("Run finished.")
    print(f"Total iterations used: {final_state['iterations']}")
    print(f"Steps completed: {final_state['current_step']}/{len(final_state['plan'])}")
    print()
    print("Tool usage trace (proves real, non-mocked tool calls):")
    for i, obs in enumerate(final_state["observations"], 1):
        tool = obs.get("tool_used") or "(none -- plain model reply)"
        status = "ERROR: " + obs["error"] if obs.get("error") else "ok"
        print(f"  {i}. step={obs['step']!r}")
        print(f"     model={obs['model_tag']} tool={tool} status={status}")
        if obs.get("result") is not None:
            print(f"     real tool result: {obs['result']!r}")
    print()
    print("Final outputs:")
    for i, output in enumerate(final_state["outputs"], 1):
        print(f"  {i}. {output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
