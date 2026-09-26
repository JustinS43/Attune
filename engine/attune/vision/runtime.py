"""ONNX Runtime session setup shared by the face finder and face namer.

Uses the CUDA provider when it's available and falls back to the CPU, so the
code also runs on laptops without an NVIDIA GPU (slower).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)
_dlls_loaded = False


def _preload_cuda_dlls() -> None:
    """Load the CUDA and cuDNN DLLs installed by the nvidia-* pip wheels (Windows)."""
    global _dlls_loaded
    if _dlls_loaded:
        return
    _dlls_loaded = True
    import onnxruntime as ort

    preload = getattr(ort, "preload_dlls", None)
    if preload is not None:
        try:
            preload()
        except Exception as exc:  # noqa: BLE001 - the CPU fallback still works
            log.warning("Could not preload CUDA libraries: %s", exc)


def make_session(model_path: str, use_gpu: bool = True):
    import onnxruntime as ort

    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found: {model_path} (see models/README.md)")
    providers = ["CPUExecutionProvider"]
    if use_gpu and "CUDAExecutionProvider" in ort.get_available_providers():
        _preload_cuda_dlls()
        providers = [
            ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}),
            "CPUExecutionProvider",
        ]
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    session = ort.InferenceSession(model_path, sess_options=opts, providers=providers)
    if use_gpu and "CUDAExecutionProvider" not in session.get_providers():
        log.warning("%s is running on the CPU", os.path.basename(model_path))
    return session
