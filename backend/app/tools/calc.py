"""
Corrosion rate / remaining-life calculator.

Pure Python + pint (unit-aware) math. Deliberately NOT routed through an
LLM — this is exactly the kind of calculation where a model can silently
get the arithmetic wrong, so it's computed directly and the LLM only ever
narrates the result.
"""

from __future__ import annotations

from typing import TypedDict

import pint

ureg = pint.UnitRegistry()


class CorrosionResult(TypedDict):
    corrosion_rate_mm_per_year: float
    remaining_life_years: float
    steps: dict


def calculate_corrosion(
    previous_thickness: float,
    current_thickness: float,
    years: float,
    required_thickness: float,
    unit: str = "mm",
) -> CorrosionResult:
    """
    Args:
        previous_thickness: thickness at the earlier inspection.
        current_thickness: thickness at the most recent inspection.
        years: time elapsed between the two inspections.
        required_thickness: minimum thickness allowed before failure/replacement.
        unit: unit the thickness values are given in (default "mm").

    Returns:
        CorrosionResult with the corrosion rate, remaining life in years,
        and a step-by-step breakdown for auditability.
    """
    if years <= 0:
        raise ValueError("years must be greater than 0")

    prev = ureg.Quantity(previous_thickness, unit)
    curr = ureg.Quantity(current_thickness, unit)
    required = ureg.Quantity(required_thickness, unit)
    span = ureg.Quantity(years, "year")

    metal_lost = prev - curr
    corrosion_rate = metal_lost / span  # e.g. mm/year

    remaining_metal = curr - required
    if corrosion_rate.magnitude <= 0:
        remaining_life = float("inf")
    else:
        remaining_life = (remaining_metal / corrosion_rate).to("year").magnitude

    steps = {
        "1_metal_lost": f"{previous_thickness}{unit} - {current_thickness}{unit} = {metal_lost.magnitude}{unit}",
        "2_corrosion_rate": f"{metal_lost.magnitude}{unit} / {years} years = {corrosion_rate.magnitude:.6f} {unit}/year",
        "3_remaining_metal": f"{current_thickness}{unit} - {required_thickness}{unit} = {remaining_metal.magnitude}{unit}",
        "4_remaining_life": (
            f"{remaining_metal.magnitude}{unit} / {corrosion_rate.magnitude:.6f} {unit}/year "
            f"= {remaining_life:.4f} years"
            if remaining_life != float("inf")
            else "corrosion rate is 0 or negative -> remaining life is effectively infinite"
        ),
    }

    return CorrosionResult(
        corrosion_rate_mm_per_year=round(corrosion_rate.to(f"{unit}/year").magnitude, 6),
        remaining_life_years=(
            round(remaining_life, 4) if remaining_life != float("inf") else float("inf")
        ),
        steps=steps,
    )
