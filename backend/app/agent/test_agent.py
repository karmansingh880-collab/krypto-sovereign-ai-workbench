"""
Manual smoke test for the Krypto agent loop.

Run with:
    ./venv/bin/python -m backend.app.agent.test_agent

Watch the terminal for [PLAN] / [ACT] / [OBSERVE] / [REFLECT] lines showing
the loop as it happens.
"""

from backend.app.agent.graph import run_agent

SAMPLE_TEXT = (
    "The Ganges river basin supports over 400 million people and is one of "
    "the most densely populated river basins in the world. It faces severe "
    "pollution from industrial waste, untreated sewage, and agricultural "
    "runoff, prompting large-scale government cleanup initiatives such as "
    "the Namami Gange programme."
)

GOAL = f"Summarize this in one sentence: {SAMPLE_TEXT}"


def main():
    print("=" * 70)
    print("Starting Krypto agent test run")
    print(f"Goal: {GOAL}")
    print("=" * 70)

    final_state = run_agent(GOAL)

    print("=" * 70)
    print("Run finished.")
    print(f"Total iterations used: {final_state['iterations']}")
    print(f"Steps completed: {final_state['current_step']}/{len(final_state['plan'])}")
    print(f"Needs approval: {final_state['needs_approval']}")
    print("Final outputs:")
    for i, output in enumerate(final_state["outputs"], 1):
        print(f"  {i}. {output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
