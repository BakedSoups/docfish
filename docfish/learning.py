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


def evidence_status(results: list[dict], minimum_score: float = 0.2) -> dict:
    best = max((float(item.get("score", 0)) for item in results), default=0.0)
    sufficient = bool(results) and best >= minimum_score
    return {
        "sufficient": sufficient,
        "best_score": round(best, 4),
        "message": "" if sufficient else "The selected source does not contain enough relevant evidence to answer this question.",
    }


def learning_prompt(mode: str, question: str, answer: str, citations: list[dict]) -> str:
    evidence = "\n".join(
        f"[{index}] {item.get('citation') or item.get('path')}: {item.get('text', '')[:1200]}"
        for index, item in enumerate(citations[:6], 1)
    )
    if mode == "quiz":
        task = "Create two short questions: one recall question and one application question. Put answers in a collapsed answer key at the end."
    else:
        task = "Explain the answer more simply using a small mental model and one minimal example. Preserve all citation numbers."
    return f"{task}\n\nOriginal question:\n{question}\n\nAnswer:\n{answer}\n\nEvidence:\n{evidence}"


def note_markdown(note: dict) -> str:
    citations = "\n".join(
        f"- [{index}] {item.get('citation') or item.get('path', 'Source')}"
        for index, item in enumerate(note.get("citations", []), 1)
    ) or "- None"
    return (
        f"# Learning note {note['id']}\n\n## Question\n\n{note['question']}\n\n"
        f"## Answer\n\n{note['answer']}\n\n## Sources\n\n{citations}\n"
    )
