"""Speak-for-me package (Section 3 - Hardware & Services, TODO H-07..H-09).

ElevenLabs streaming first; Kokoro (sherpa-onnx, offline) if no audio within
``speech_out.fallback_after_s``. Only the typed text ever leaves the laptop.
"""

from .service import SpeechOutService

__all__ = ["SpeechOutService"]
