"""Sphere colours."""
from __future__ import annotations

import colorsys

RGB = tuple[int, int, int]

BLACK: RGB = (0, 0, 0)
WHITE: RGB = (255, 255, 255)
DIM_WHITE: RGB = (40, 40, 40)
RED: RGB = (255, 0, 0)
DEAD_RED: RGB = (60, 0, 0)
GREEN: RGB = (0, 255, 0)
BLUE: RGB = (0, 0, 255)
YELLOW: RGB = (255, 200, 0)
ORANGE: RGB = (255, 90, 0)
PURPLE: RGB = (140, 0, 255)
PINK: RGB = (255, 60, 150)
TURQUOISE: RGB = (0, 220, 180)
MAGENTA: RGB = (255, 0, 255)

# Distinct per-player colours for free-for-all.
PLAYER_COLORS: list[RGB] = [ORANGE, BLUE, GREEN, PINK, PURPLE, TURQUOISE, YELLOW, MAGENTA]
TEAM_COLORS: list[RGB] = [RED, BLUE, GREEN, YELLOW]

BATTERY_COLORS: dict[str, RGB] = {
    "charging": DIM_WHITE,
    "charged": WHITE,
    "100%": GREEN,
    "80%": TURQUOISE,
    "60%": BLUE,
    "40%": YELLOW,
    "20%": RED,
    "empty": RED,
}


def scale(color: RGB, factor: float) -> RGB:
    f = max(0.0, min(1.0, factor))
    return tuple(int(c * f) for c in color)  # type: ignore[return-value]


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)
