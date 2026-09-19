"""
Manual smoke test for the corrosion calculator.

Run with:
    ./venv/bin/python -m backend.app.tools.test_calc
"""

from backend.app.tools.calc import calculate_corrosion


def main():
    result = calculate_corrosion(
        previous_thickness=10.0,
        current_thickness=8.5,
        years=5,
        required_thickness=6.0,
    )

    print("Inputs: previous=10mm, current=8.5mm, years=5, required=6mm")
    print()
    print("Step-by-step breakdown:")
    for key in sorted(result["steps"]):
        print(f"  {key}: {result['steps'][key]}")
    print()
    print(f"Corrosion rate: {result['corrosion_rate_mm_per_year']} mm/year")
    print(f"Remaining life: {result['remaining_life_years']} years")


if __name__ == "__main__":
    main()
