"""
Integration test for Step 2: agent loop writing code and running it for
real inside a Docker sandbox (via run_code_sandbox).

Run with:
    ./venv/bin/python -m backend.app.agent.test_agent_sandbox
"""

from backend.app.agent.graph import run_agent

GOAL = (
    "Write a Python function to calculate pipe flow rate given diameter and "
    "velocity, then test it with sample values."
)


def main():
    print("=" * 70)
    print("Starting Krypto agent test run (Step 2: real Docker sandbox)")
    print(f"Goal: {GOAL}")
    print("=" * 70)

    final_state = run_agent(GOAL)

    print("=" * 70)
    print("Run finished.")
    print(f"Total iterations used: {final_state['iterations']}")
    print(f"Retries used on final step: {final_state['retries']}")
    print(f"Steps completed: {final_state['current_step']}/{len(final_state['plan'])}")
    print()
    print("Full tool usage trace:")
    for i, obs in enumerate(final_state["observations"], 1):
        tool = obs.get("tool_used") or "(none -- plain model reply)"
        status = "ERROR: " + obs["error"] if obs.get("error") else "ok"
        print(f"  {i}. step={obs['step']!r}")
        print(f"     model={obs['model_tag']} tool={tool} status={status}")
        if tool == "run_code_sandbox" and obs.get("result"):
            r = obs["result"]
            print(f"     container_id={r.get('container_id')}")
            print(f"     passed={r.get('passed')}")
            print(f"     stdout={r.get('stdout')!r}")
            print(f"     stderr={r.get('stderr')!r}")
        elif obs.get("tool_args"):
            print(f"     args={obs['tool_args']}")
    print()
    print("Final outputs:")
    for i, output in enumerate(final_state["outputs"], 1):
        print(f"  {i}. {output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
