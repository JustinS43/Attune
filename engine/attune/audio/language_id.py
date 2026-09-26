"""Constrained local text-language identification."""

from __future__ import annotations


class LanguageID:
    """Double-check final text, retaining the ASR guess when evidence is absent."""

    def __init__(self, languages: list[str]):
        from lingua import IsoCode639_1, Language, LanguageDetectorBuilder

        self.languages = languages
        selected = [
            Language.from_iso_code_639_1(getattr(IsoCode639_1, code.upper())) for code in languages
        ]
        builder = (
            LanguageDetectorBuilder.from_languages(*selected)
            if len(selected) >= 2
            else LanguageDetectorBuilder.from_all_languages()
        )
        self.detector = builder.build()

    def detect(self, text: str, fallback: str = "und") -> str:
        """Return an ISO-639-1 tag, never outside a configured language set."""
        if len(self.languages) == 1:
            return self.languages[0]
        if not text.strip():
            return fallback
        result = self.detector.detect_language_of(text)
        code = result.iso_code_639_1.name.lower() if result else fallback
        return code if not self.languages or code in self.languages else fallback
