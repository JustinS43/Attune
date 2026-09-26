"""Language selection without downloading or initializing recognition models."""

import sys
from types import SimpleNamespace

import pytest
from attune.audio.language_id import LanguageID


def test_language_detection_never_escapes_selected_languages():
    language = LanguageID.__new__(LanguageID)
    language.languages = ["en", "es"]
    language.detector = SimpleNamespace(detect_language_of=lambda text: None)
    assert language.detect("...", "fr") == "und"
    assert language.detect("", "fr") == "und"
    assert language.detect("...", "es-ES") == "es"
    language.detector.detect_language_of = lambda text: SimpleNamespace(
        iso_code_639_1=SimpleNamespace(name="ES")
    )
    assert language.detect("Hola", "en") == "es"


def test_language_selection_validates_and_deduplicates_without_loading_all(monkeypatch):
    def unexpected_builder(*args):
        pytest.fail("a single language must not initialize all language models")

    module = SimpleNamespace(
        IsoCode639_1=SimpleNamespace(EN="en", ES="es"),
        Language=SimpleNamespace(from_iso_code_639_1=lambda code: code),
        LanguageDetectorBuilder=SimpleNamespace(from_all_languages=unexpected_builder),
    )
    monkeypatch.setitem(sys.modules, "lingua", module)
    language = LanguageID(["EN", "en"])
    assert language.languages == ["en"]
    assert language.detect("Hello", "es") == "en"
    for invalid in (["xx"], [None], "en", ["english"]):
        with pytest.raises(ValueError):
            LanguageID(invalid)
