import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUTTONS = (ROOT / "ceres" / "static" / "buttons.css").read_text(encoding="utf-8")


def _specificity(selector: str) -> tuple[int, int, int]:
    functional = re.compile(r":(is|not|has|where)\(([^()]*)\)")
    extra = [0, 0, 0]

    while match := functional.search(selector):
        name, arguments = match.groups()
        if name != "where":
            choices = [_specificity(part.strip()) for part in arguments.split(",")]
            maximum = max(choices)
            extra = [left + right for left, right in zip(extra, maximum)]
        selector = selector[:match.start()] + " " + selector[match.end():]

    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+", selector))
    classes += len(re.findall(r"\[[^\]]+\]", selector))
    classes += len(re.findall(r"(?<!:):(?!:)[\w-]+", selector))
    bare = re.sub(r"\[[^\]]+\]|[.#][\w-]+|:{1,2}[\w-]+", " ", selector)
    elements = len(re.findall(r"(?<![\w-])[a-zA-Z][\w-]*(?=[\s.#:\[]|$)", bare))
    return ids + extra[0], classes + extra[1], elements + extra[2]


def _rules(css: str):
    return [
        (selector.strip(), declarations)
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    ]


def _selectors(selector_list: str) -> list[str]:
    selectors = []
    start = depth = 0
    for index, character in enumerate(selector_list):
        if character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            selectors.append(selector_list[start:index].strip())
            start = index + 1
    selectors.append(selector_list[start:].strip())
    return selectors


def test_destructive_secondary_hover_wins_with_danger_border() -> None:
    generic = []
    destructive = []

    for order, (selector_list, declarations) in enumerate(_rules(BUTTONS)):
        for selector in _selectors(selector_list):
            if (
                ".ceres-btn--secondary" in selector
                and ":hover" in selector
                and re.search(r"\bborder-color\s*:\s*var\(--accent\)", declarations)
            ):
                generic.append((selector, _specificity(selector), order))
            if (
                'data-control-case~="destructive"' in selector
                and ":hover" in selector
                and re.search(r"\bborder-color\s*:\s*var\(--danger\)", declarations)
            ):
                destructive.append((selector, _specificity(selector), order))

    assert generic, "generic secondary hover border rule was not found"
    assert destructive, "destructive hover rule must set a danger border"

    generic_winner = max(generic, key=lambda rule: (rule[1], rule[2]))
    destructive_winner = max(destructive, key=lambda rule: (rule[1], rule[2]))
    assert destructive_winner[1:] > generic_winner[1:], (
        "destructive hover must win by specificity or source order: \"\n f\"{destructive_winner[0]!r} {destructive_winner[1:]} <= \"\n f\"{generic_winner[0]!r} {generic_winner[1:]}"
    )
