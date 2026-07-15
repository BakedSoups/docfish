"""Question crafting and grounding helpers."""

import re


FIELDS = ("goal", "context", "constraints", "question", "format")


def structured_question(data: dict) -> str:
    labels = {
        "goal": "Goal", "context": "Known context", "constraints": "Constraints",
        "question": "Exact question", "format": "Desired response format",
    }
    parts = [f"{labels[key]}: {str(data.get(key, '')).strip()}" for key in FIELDS if str(data.get(key, "")).strip()]
    examples = [str(value).strip() for value in data.get("examples", []) if str(value).strip()][:3]
    if examples:
        parts.append("Examples:\n" + "\n\n".join(f"Example {index}:\n{value}" for index, value in enumerate(examples, 1)))
    return "\n\n".join(parts)


def crafting_prompt(data: dict, mode: str) -> str:
    draft = structured_question(data)
    if mode == "missing":
        instruction = (
            "Identify only the missing context that materially prevents a precise answer. "
            "Return a short checklist. Do not answer the programming question."
        )
    else:
        instruction = (
            "Rewrite this as a precise one-to-three-shot programming question. Keep context short, "
            "preserve the learner's intent, include no invented facts, and return only the proposed question."
        )
    return f"{instruction}\n\nDRAFT\n{draft or '(empty draft)'}"


def validate_citations(answer: str, source_count: int) -> dict:
    cited = {int(value) for value in re.findall(r"\[(\d+)]", answer)}
    invalid = sorted(value for value in cited if value < 1 or value > source_count)
    valid = sorted(value for value in cited if 1 <= value <= source_count)
    return {
        "grounded": bool(valid) and not invalid,
        "citations": valid,
        "invalid": invalid,
        "warning": "" if valid and not invalid else "The answer did not provide valid citations for the retrieved evidence.",
    }
