"""V-12: instant colour labels come only from the fixed list and name common shirt colours right."""

import cv2
import numpy as np
import pytest
from attune.vision.appearance import (
    COLORS,
    classify_hsv,
    dominant_color,
    upper_body_box,
)

# Typical garment colours (RGB) and the word we expect.
SWATCHES = {
    "black": (20, 20, 22),
    "white": (235, 235, 230),
    "grey": (128, 128, 128),
    "navy": (25, 35, 90),
    "blue": (40, 90, 200),
    "green": (40, 140, 60),
    "red": (200, 30, 30),
    "orange": (240, 130, 30),
    "yellow": (240, 220, 40),
    "purple": (120, 50, 160),
    "pink": (240, 150, 190),
    "brown": (110, 70, 35),
    "beige": (220, 200, 160),
}


@pytest.mark.parametrize("name,rgb", SWATCHES.items())
def test_swatch_colours(name, rgb):
    img = np.zeros((200, 200, 3), np.uint8)
    img[:] = rgb[::-1]  # BGR
    noise = np.random.default_rng(1).integers(-12, 13, img.shape)
    img = np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)
    assert dominant_color(img, (0, 0, 200, 200)) == name


def test_only_fixed_words_can_come_out():
    rng = np.random.default_rng(2)
    hsv = np.stack(
        [
            rng.integers(0, 180, 5000),
            rng.integers(0, 256, 5000),
            rng.integers(0, 256, 5000),
        ],
        -1,
    )
    idx = classify_hsv(hsv.reshape(50, 100, 3).astype(np.uint8))
    assert set(np.unique(idx)) <= set(range(len(COLORS)))


def test_upper_body_region_is_below_the_face_and_clipped():
    x1, y1, x2, _ = upper_body_box(np.array([900, 300, 1000, 420]), (1080, 1920, 3))
    assert y1 > 420 and x1 < 900 and x2 > 1000
    assert upper_body_box(np.array([900, 1040, 1000, 1079]), (1080, 1920, 3)) is None


def test_shirt_under_a_face_wins_over_background():
    img = np.full((1080, 1920, 3), (200, 200, 200), np.uint8)  # light grey wall
    face = np.array([900, 300, 1000, 420])
    x1, y1, x2, y2 = upper_body_box(face, img.shape)
    cv2.rectangle(img, (x1 + 15, y1), (x2 - 15, y2), SWATCHES["blue"][::-1], -1)
    assert dominant_color(img, (x1, y1, x2, y2)) == "blue"
