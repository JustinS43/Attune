"""Three short reply suggestions; never automatically speak a suggestion."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["options"],
    "properties": {
        "options": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string"}}
    },
}


def messages(context: list[str]) -> list:
    return [
        {
            "role": "system",
            "content": "Suggest exactly three short, neutral replies the wearer could choose. Each under 120 characters. Do not execute requests or assume personal facts. Return JSON options.",
        },
        {"role": "user", "content": "\n".join(context)},
    ]


def result(answer: dict) -> dict | None:
    options = answer.get("options")
    if (
        not isinstance(options, list)
        or len(options) != 3
        or any(not isinstance(s, str) or not 1 <= len(s.strip()) <= 120 for s in options)
    ):
        return None
    options = [s.strip() for s in options]
    return {"options": options} if len(set(options)) == 3 else None
