"""
Locally generated images: charts from data, an infographic card from key points,
and the corrosion chart for inspection results.

These are drawn with matplotlib -- accurate, offline, and fast. They are not
AI-painted pictures (that needs a diffusion model, far too heavy for this machine).
"""

from __future__ import annotations

import re
import textwrap
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")  # headless: no window, safe inside the server
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

# pyplot keeps global state and is not thread-safe; runs execute in worker threads.
_lock = threading.Lock()

BLUE, GREEN, AMBER, RED, GREY = "#1b5fd1", "#2e9e5b", "#d99a1c", "#c93a32", "#5f6d7c"
_PALETTE = ["#1b5fd1", "#2e9e5b", "#d99a1c", "#c93a32", "#7a4fd0", "#17a2b8", "#e2733b", "#5f6d7c"]

# 'label: 12', 'label = 12', 'label | 12' first, then 'label, 12', then a SPACED dash
# ('label - 12'); an unspaced hyphen belongs to the label itself (e.g. "P-103").
_NUM = r"(-?\d[\d,]*\.?\d*)"
_UNIT = r"(?:\s*\S.{0,30})?$"
_DATA_LINE_PATTERNS = [
    re.compile(r"^(.{1,40}?)\s*[:=|]\s*" + _NUM + _UNIT),
    re.compile(r"^(.{1,40}?)\s*,\s*" + _NUM + _UNIT),
    re.compile(r"^(.{1,40}?)\s+[-–—]\s+" + _NUM + _UNIT),
]


def parse_chart_data(text: str) -> Optional[Tuple[str, str, List[str], List[float]]]:
    """Read 'TITLE:', 'TYPE:' and 'label: number' lines written by the model."""
    title, kind = "", "bar"
    labels: List[str] = []
    values: List[float] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if not line or line.upper() == "NONE":
            continue
        head = re.match(r"(?i)^(title|type)\s*[:=]\s*(.+)$", line)
        if head:
            if head.group(1).lower() == "title":
                title = head.group(2).strip(" *\"'")
            else:
                word = head.group(2).lower()
                kind = "pie" if "pie" in word else "line" if "line" in word else "bar"
            continue
        match = None
        for pattern in _DATA_LINE_PATTERNS:
            match = pattern.match(line)
            if match:
                break
        if match:
            labels.append(match.group(1).strip(" *\"'"))
            values.append(float(match.group(2).replace(",", "")))
    if len(values) < 2:
        return None

    # A small model sometimes copies the format word instead of a real name ("label: 5").
    placeholders = {"label", "labels", "item", "items", "category", "name", "x", "data", "value", "point", "example"}
    if sum(1 for l in labels if l.lower() in placeholders) * 2 >= len(labels):
        return None

    counts: Dict[str, int] = {}
    unique: List[str] = []
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
        unique.append(label if counts[label] == 1 else f"{label} ({counts[label]})")
    return title or "Chart", kind, unique[:20], values[:20]


def _finish(fig, path: Path) -> str:
    fig.savefig(str(path), dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(path)


def chart_from_data(title: str, kind: str, labels: List[str], values: List[float], path: Path) -> str:
    with _lock:
        fig, ax = plt.subplots(figsize=(8, 4.8))
        if kind == "pie":
            positives = [(l, v) for l, v in zip(labels, values) if v > 0] or list(zip(labels, values))
            ax.pie([v for _, v in positives], labels=[l for l, _ in positives], autopct="%1.1f%%",
                   startangle=90, colors=_PALETTE, wedgeprops={"edgecolor": "white"})
            ax.axis("equal")
        elif kind == "line":
            ax.plot(labels, values, marker="o", color=BLUE, linewidth=2)
            ax.grid(alpha=0.3)
        else:
            bars = ax.bar(labels, values, color=[_PALETTE[i % len(_PALETTE)] for i in range(len(values))])
            ax.bar_label(bars, fmt="%g", padding=3, fontsize=9)
            ax.grid(axis="y", alpha=0.3)
            ax.set_axisbelow(True)
        if kind != "pie":
            if max(len(str(l)) for l in labels) > 6 or len(labels) > 6:
                plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
        ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
        return _finish(fig, path)


def grouped_bar_chart(title: str, labels: List[str], series: Dict[str, List[float]], path: Path,
                      y_label: str = "") -> str:
    """Several series side by side per label (e.g. previous vs current thickness)."""
    with _lock:
        fig, ax = plt.subplots(figsize=(8, 4.8))
        count = len(series)
        width = 0.8 / count
        for i, (name, values) in enumerate(series.items()):
            positions = [x + (i - (count - 1) / 2) * width for x in range(len(labels))]
            bars = ax.bar(positions, values, width, label=name, color=_PALETTE[i % len(_PALETTE)])
            ax.bar_label(bars, fmt="%g", padding=2, fontsize=8)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
        ax.legend(frameon=False)
        ax.grid(axis="y", alpha=0.3)
        ax.set_axisbelow(True)
        if y_label:
            ax.set_ylabel(y_label)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
        return _finish(fig, path)


def infographic(title: str, points: List[str], path: Path) -> str:
    """A one-page card: title band and numbered key points."""
    points = [p for p in points if p.strip()][:6] or ["No key points were available."]
    with _lock:
        height = 1.6 + 1.05 * len(points)
        fig, ax = plt.subplots(figsize=(8, height))
        ax.set_xlim(0, 10)
        ax.set_ylim(0, height)
        ax.axis("off")
        ax.add_patch(FancyBboxPatch((0.2, height - 1.25), 9.6, 1.05, boxstyle="round,pad=0.02,rounding_size=0.15",
                                    facecolor=BLUE, edgecolor="none"))
        ax.text(5, height - 0.72, "\n".join(textwrap.wrap(title, 46)[:2]), ha="center", va="center",
                color="white", fontsize=17, fontweight="bold")
        for i, point in enumerate(points):
            y = height - 1.45 - 1.05 * (i + 1) + 0.25
            ax.add_patch(FancyBboxPatch((0.2, y), 9.6, 0.85, boxstyle="round,pad=0.02,rounding_size=0.12",
                                        facecolor="#f2f6fc", edgecolor="#c9d6ea"))
            ax.add_patch(plt.Circle((0.85, y + 0.425), 0.28, color=_PALETTE[i % len(_PALETTE)]))
            ax.text(0.85, y + 0.425, str(i + 1), ha="center", va="center", color="white", fontsize=12, fontweight="bold")
            # shorten at a word boundary ("…") so a long point never stops mid-sentence
            shown = textwrap.shorten(point, width=118, placeholder="…")
            ax.text(1.4, y + 0.425, "\n".join(textwrap.wrap(shown, 62)[:2]), ha="left", va="center",
                    fontsize=11, color="#18212b")
        return _finish(fig, path)


def corrosion_chart(table: List[Dict[str, Any]], required_thickness: Optional[float], path: Path) -> str:
    """Corrosion rate and remaining life per location, coloured by the SOP-CORR-014 limits."""
    locations = [str(r.get("location", "?")) for r in table]
    rates = [float(r.get("corrosion_rate") or 0) for r in table]
    lives = [float(r["remaining_life"]) if r.get("remaining_life") is not None else 0.0 for r in table]

    def rate_colour(rate: float) -> str:
        return RED if rate > 0.25 else AMBER if rate >= 0.1 else GREEN

    with _lock:
        fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4.4))
        bars = left.bar(locations, rates, color=[rate_colour(r) for r in rates])
        left.bar_label(bars, fmt="%.2f", padding=3, fontsize=9)
        left.axhline(0.25, color=RED, linestyle="--", linewidth=1)
        left.axhline(0.10, color=AMBER, linestyle="--", linewidth=1)
        left.text(len(locations) - 0.5, 0.255, "high > 0.25", color=RED, fontsize=8, ha="right", va="bottom")
        left.text(len(locations) - 0.5, 0.105, "moderate ≥ 0.10", color=AMBER, fontsize=8, ha="right", va="bottom")
        left.set_title("Corrosion rate (mm/year)", fontweight="bold")
        left.set_ylim(0, max(max(rates, default=0) * 1.25, 0.3))

        life_colours = [RED if life < 10 else GREEN for life in lives]
        bars = right.bar(locations, lives, color=life_colours)
        right.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
        right.axhline(10, color=RED, linestyle="--", linewidth=1)
        right.text(-0.45, 10.5, "10-year minimum", color=RED, fontsize=8, ha="left", va="bottom")
        right.set_title("Remaining life (years)", fontweight="bold")
        right.set_ylim(0, max(max(lives, default=0) * 1.15, 12))

        for ax in (left, right):
            ax.grid(axis="y", alpha=0.3)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
        suffix = f"  (minimum allowable thickness {required_thickness:g} mm)" if required_thickness else ""
        fig.suptitle("Corrosion assessment" + suffix, fontsize=13, fontweight="bold")
        fig.tight_layout()
        return _finish(fig, path)
