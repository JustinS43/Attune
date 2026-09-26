"""Attune engine: AR captions for deaf and hard-of-hearing people.

Run it with `python -m attune` (see attune/main.py and docs/setup.md).
"""

import os

# Must be set before anything imports cv2: OpenCV reads it once, at import. With Media
# Foundation hardware transforms on, a USB webcam (Logitech C922) takes ~17 s to open and
# then hangs on the first property change once CUDA is loaded. vision/camera.py sets it
# too, but other modules import cv2 before that file runs.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

__version__ = "0.1.0"
