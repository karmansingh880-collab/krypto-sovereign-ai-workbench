"""
Manual smoke test for the model router.

Run with:
    ./venv/bin/python -m backend.app.router.test_router
"""

from backend.app.router.router import route_task

TEST_CASES = [
    ("Write a python script that sorts a list of integers", None),
    ("Summarize this quarterly report in three sentences", None),
    ("What does this scanned handwriting say?", "image"),
]


def main():
    for task_description, file_type in TEST_CASES:
        decision = route_task(task_description, file_type=file_type)
        print(f"Task: {task_description!r} (file_type={file_type!r})")
        print(f"  -> model: {decision.model_key} ({decision.model_tag})")
        print(f"  -> reason: {decision.reason}")
        print()


if __name__ == "__main__":
    main()
