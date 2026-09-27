"""Words in a script the caption languages don't use are dropped (podcast crosstalk, laughs)."""

from attune.audio.asr import allowed_scripts, in_scripts


def test_english_spanish_keep_latin_words_only():
    scripts = allowed_scripts(["en", "es"])
    for word in ["moving", "¿Dónde", "niño", "don't", "café", "3", "—"]:
        assert in_scripts(word, scripts), word
    for word in ["اللي", "Москва", "你好", "ありがとう"]:
        assert not in_scripts(word, scripts), word


def test_other_scripts_when_configured_and_none_for_auto():
    assert in_scripts("اللي", allowed_scripts(["ar", "en"]))
    assert in_scripts("ありがとう", allowed_scripts(["ja"]))
    assert allowed_scripts([]) is None and in_scripts("اللي", None)
