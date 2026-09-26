"""Constrained local text-language identification."""

from __future__ import annotations


class LanguageID:
    """Double-check final text, retaining the ASR guess when evidence is absent."""

    def __init__(self, languages: list[str]):
        from lingua import IsoCode639_1, Language, LanguageDetectorBuilder

        if not isinstance(languages, list) or any(
            not isinstance(code, str) or len(code) != 2 for code in languages
        ):
            raise ValueError("languages must be a list of ISO-639-1 codes")
        self.languages = list(dict.fromkeys(code.lower() for code in languages))
        self.detector = None
        try:
            selected = [
                Language.from_iso_code_639_1(getattr(IsoCode639_1, code.upper()))
                for code in self.languages
            ]
        except (AttributeError, ValueError) as exc:
            raise ValueError("unsupported language code") from exc
        if len(selected) == 1:
            return
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
        fallback = fallback.split("-")[0].lower()
        if self.languages and fallback not in self.languages:
            fallback = "und"
        if not text.strip():
            return fallback
        result = self.detector.detect_language_of(text)
        code = result.iso_code_639_1.name.lower() if result else fallback
        return code if not self.languages or code in self.languages else fallback
