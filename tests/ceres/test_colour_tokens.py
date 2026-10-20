from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
ALLOWLIST_PATH = Path(__file__).parent / "fixtures" / "ceres-colour-allowlist.json"

COLOUR_FUNCTION = re.compile(
    r"(?i)(?<![-\w])(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\s*\("
)
HEX_COLOUR = re.compile(r"(?i)(?<![\w#])#[0-9a-f]{3}(?:[0-9a-f]{1}|[0-9a-f]{3}|[0-9a-f]{5})?(?![0-9a-f\w])")
CSS_NAME = re.compile(r"(?i)(?<![-\w])(?:" + "|".join((
    "aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood "
    "cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray "
    "darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen "
    "darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue "
    "firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew "
    "hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan "
    "lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray "
    "lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid "
    "mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream "
    "mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen "
    "paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown "
    "royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow "
    "springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen"
).split()) + r")(?![-\w])")
NAMED_COLOUR_PROPERTIES = re.compile(
    r"(?i)(?:color|background|border|outline|shadow|fill|stroke|decoration|column-rule|caret|accent|scrollbar|stop|flood|lighting|filter)"
)
CSS_TEMPLATE_RULE = re.compile(
    r"(?is)^\s*(?:[.#:\[*]|@(?:media|supports|layer|container|keyframes)\b|\"\n r\"(?:html|body|main|header|footer|section|article|div|span|a|button|input|select|textarea|label|form|\"\n r\"table|thead|tbody|tr|th|td|svg|path)\b)[^{};]*\{"
)
STYLE_ATTRIBUTE = re.compile(r"(?is)\bstyle\s*=\s*([\"'])(.*?)\1")
STYLE_ASSIGNMENT = re.compile(r"(?im)\.style\.(?P<property>[A-Za-z][A-Za-z0-9]*)\s*=\s*(?P<value>[^;\n]+)")
STYLE_BRACKET_ASSIGNMENT = re.compile(
    r"(?im)\.style\s*\[\s*([\"'])(?P<property>[A-Za-z][A-Za-z0-9-]*)\1\s*\]\s*=\s*(?P<value>[^;\n]+)"
)
SET_PROPERTY = re.compile(
    r"(?is)\.style\.setProperty\(\s*([\"'])(?P<property>--?[A-Za-z][A-Za-z0-9-]*|[A-Za-z][A-Za-z0-9-]*)\1\s*,\s*\"\n r\"(?P<quote>[\"'`])(?P<value>.*?)(?<!\\)(?P=quote)"
)
CSS_TEXT_ASSIGNMENT = re.compile(
    r"(?is)\.style\.cssText\s*=\s*(?P<quote>[\"'`])(?P<value>.*?)(?<!\\)(?P=quote)"
)
SET_STYLE_ATTRIBUTE = re.compile(
    r"(?is)\.setAttribute\(\s*([\"'])style\1\s*,\s*(?P<quote>[\"'`])(?P<value>.*?)(?<!\\)(?P=quote)"
)
JS_STRING = re.compile(r"(?s)(?P<quote>[\"'`])(?P<value>(?:\\.|(?!\1).)*?)(?<!\\)(?P=quote)")


@dataclass(frozen=True)
class RawColour:
    file: str
    line: int
    match_key: str
    value: str


def _allowlist() -> dict:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def _blank_comments(text: str) -> str:
    def blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group())

    return re.sub(r"/\*.*?\*/", blank, text, flags=re.DOTALL)


def _blank_js_comments(text: str) -> str:
    result = list(text)
    cursor = 0
    quote = ""
    while cursor < len(text):
        char = text[cursor]
        if quote:
            if char == quote and text[cursor - 1] != "\\":
                quote = ""
            cursor += 1
            continue
        if char in "\"'`":
            quote = char
            cursor += 1
            continue
        if text.startswith("//", cursor):
            end = text.find("\n", cursor)
            end = len(text) if end < 0 else end
            result[cursor:end] = " " * (end - cursor)
            cursor = end
            continue
        if text.startswith("/*", cursor):
            end = text.find("*/", cursor + 2)
            end = len(text) if end < 0 else end + 2
            for index in range(cursor, end):
                if result[index] != "\n":
                    result[index] = " "
            cursor = end
            continue
        cursor += 1
    return "".join(result)


def _quoted_end(text: str, opening: int, quote: str) -> int | None:
    cursor = opening + 1
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
        elif text[cursor] == quote:
            return cursor
        else:
            cursor += 1
    return None


def _template_end(text: str, opening: int) -> int | None:
    cursor = opening + 1
    expression_depth = 0
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if expression_depth == 0:
            if char == "`":
                return cursor
            if text.startswith("${", cursor):
                expression_depth = 1
                cursor += 2
                continue
        elif char in "\"'":
            end = _quoted_end(text, cursor, char)
            if end is None:
                return None
            cursor = end + 1
            continue
        elif char == "`":
            end = _template_end(text, cursor)
            if end is None:
                return None
            cursor = end + 1
            continue
        elif char == "{":
            expression_depth += 1
        elif char == "}":
            expression_depth -= 1
        cursor += 1
    return None


def _template_literals(text: str):
    cursor = 0
    while cursor < len(text):
        char = text[cursor]
        if char in "\"'":
            end = _quoted_end(text, cursor, char)
            if end is None:
                return
            cursor = end + 1
            continue
        if char == "`":
            end = _template_end(text, cursor)
            if end is None:
                return
            yield text[cursor + 1:end], cursor + 1
            cursor = end + 1
            continue
        cursor += 1


def _interpolation_end(text: str, opening: int) -> int | None:
    cursor = opening + 2
    depth = 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char in "\"'":
            end = _quoted_end(text, cursor, char)
            if end is None:
                return None
            cursor = end + 1
            continue
        if char == "`":
            end = _template_end(text, cursor)
            if end is None:
                return None
            cursor = end + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def _matching_brace(text: str, opening: int) -> int:
    depth = 1
    quote = ""
    cursor = opening + 1
    while cursor < len(text):
        char = text[cursor]
        if quote:
            if char == quote and text[cursor - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif text.startswith("${", cursor):
            end = _interpolation_end(text, cursor)
            if end is None:
                raise AssertionError(f"unclosed template interpolation at offset {cursor}")
            cursor = end
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    raise AssertionError(f"unclosed CSS block at offset {opening}")


def _has_top_level_block(text: str) -> bool:
    depth = 0
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif text.startswith("${", index):
            end = _interpolation_end(text, index)
            if end is None:
                return False
            index = end
        elif char == "{" and depth == 0:
            return True
        index += 1
    return False


def _css_rule_bodies(text: str, base_offset: int = 0):
    cursor = 0
    while cursor < len(text):
        opening = text.find("{", cursor)
        if opening < 0:
            return
        closing = _matching_brace(text, opening)
        body = text[opening + 1:closing]
        body_offset = base_offset + opening + 1
        if _has_top_level_block(body):
            yield from _css_rule_bodies(body, body_offset)
        else:
            yield body, body_offset
        cursor = closing + 1


def _declarations(text: str, base_offset: int = 0):
    start = 0
    depth = 0
    quote = ""
    for index, char in enumerate(text + ";"):
        if quote:
            if char == quote and text[index - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            declaration = text[start:index]
            colon = declaration.find(":")
            if colon >= 0:
                leading = len(declaration) - len(declaration.lstrip())
                property_name = declaration[:colon].strip()
                value = declaration[colon + 1:].strip()
                if re.fullmatch(r"--?[A-Za-z_][A-Za-z0-9_-]*|[A-Za-z][A-Za-z0-9_-]*", property_name):
                    yield property_name, value, base_offset + start + leading
            start = index + 1


def _searchable_value(value: str) -> str:
    value = re.sub(r"(?is)url\(.*?\)", " ", value)
    return re.sub(r"(?s)([\"']).*?(?<!\\)\1", " ", value)


def _raw_values(property_name: str, value: str):
    searchable = _searchable_value(value)
    matches = [(match.start(), match.group()) for match in HEX_COLOUR.finditer(searchable)]
    for match in COLOUR_FUNCTION.finditer(searchable):
        depth = 1
        cursor = match.end()
        while cursor < len(searchable) and depth:
            if searchable[cursor] == "(":
                depth += 1
            elif searchable[cursor] == ")":
                depth -= 1
            cursor += 1
        matches.append((match.start(), searchable[match.start():cursor].strip()))
    if NAMED_COLOUR_PROPERTIES.search(property_name):
        matches += [(match.start(), match.group()) for match in CSS_NAME.finditer(searchable)]
    for _, raw in sorted(matches):
        yield raw.lower()


def _normalise_declaration(property_name: str, value: str) -> str:
    return f"{property_name.strip()}: {' '.join(value.split())}"


def _scan_declarations(path: Path, source: str, declaration_text: str, base_offset: int) -> list[RawColour]:
    hits = []
    for property_name, value, offset in _declarations(declaration_text, base_offset):
        if property_name.startswith("--"):
            continue
        match_key = _normalise_declaration(property_name, value)
        line = source.count("\n", 0, offset) + 1
        hits.extend(RawColour(path.as_posix(), line, match_key, raw) for raw in _raw_values(property_name, value))
    return hits


def _scan_css(path: Path, source: str, text: str, base_offset: int = 0) -> list[RawColour]:
    cleaned = _blank_comments(text)
    hits = []
    for body, offset in _css_rule_bodies(cleaned, base_offset):
        hits.extend(_scan_declarations(path, source, body, offset))
    return hits


def _scan_html(path: Path, source: str) -> list[RawColour]:
    hits = []
    cleaned = re.sub(
        r"(?s)<!--.*?-->",
        lambda match: re.sub(r"[^\n]", " ", match.group()),
        source,
    )
    for block in re.finditer(r"(?is)<style\b[^>]*>(.*?)</style\s*>", cleaned):
        hits.extend(_scan_css(path, source, block.group(1), block.start(1)))
    for match in STYLE_ATTRIBUTE.finditer(cleaned):
        hits.extend(_scan_declarations(path, source, match.group(2), match.start(2)))
    return hits


def _camel_to_css(name: str) -> str:
    return re.sub(r"[A-Z]", lambda match: "-" + match.group().lower(), name)


def _scan_js(path: Path, source: str) -> list[RawColour]:
    cleaned = _blank_js_comments(source)
    hits = _scan_html(path, cleaned)
    for pattern in (STYLE_ASSIGNMENT, STYLE_BRACKET_ASSIGNMENT):
        for match in pattern.finditer(cleaned):
            property_name = _camel_to_css(match.group("property"))
            if property_name == "css-text":
                continue
            for literal in JS_STRING.finditer(match.group("value")):
                value = literal.group("value")
                offset = match.start("value") + literal.start("value")
                line = source.count("\n", 0, offset) + 1
                key = _normalise_declaration(property_name, value)
                hits.extend(RawColour(path.as_posix(), line, key, raw) for raw in _raw_values(property_name, value))
    for match in SET_PROPERTY.finditer(cleaned):
        property_name = _camel_to_css(match.group("property"))
        if property_name.startswith("--"):
            continue
        value = match.group("value")
        line = source.count("\n", 0, match.start("value")) + 1
        key = _normalise_declaration(property_name, value)
        hits.extend(RawColour(path.as_posix(), line, key, raw) for raw in _raw_values(property_name, value))
    for pattern in (CSS_TEXT_ASSIGNMENT, SET_STYLE_ATTRIBUTE):
        for match in pattern.finditer(cleaned):
            hits.extend(_scan_declarations(path, source, match.group("value"), match.start("value")))
    for value, offset in _template_literals(cleaned):
        if CSS_TEMPLATE_RULE.search(value):
            hits.extend(_scan_css(path, source, value, offset))
        else:
            hits.extend(_scan_declarations(path, source, value, offset))
    return sorted(hits, key=lambda item: (item.line, item.match_key, item.value))


def _observed_raw_colours() -> list[RawColour]:
    observed = []
    for path in sorted(STATIC.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or "assets" in path.relative_to(STATIC).parts:
            continue
        if path.suffix not in {".css", ".html", ".js"}:
            continue
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".css":
            observed.extend(_scan_css(relative, source, source))
        elif path.suffix == ".html":
            observed.extend(_scan_html(relative, source))
        else:
            observed.extend(_scan_js(relative, source))
    return sorted(observed, key=lambda item: (item.file, item.line, item.match_key, item.value))


def test_css_scanner_covers_literals_without_selector_or_comment_noise():
    source = """
* color: red */
#abcdef { --local-token: #123; color: #abc; border: 1px solid rgba(1, 2, 3, .4); }
.mix { background: color-mix(in srgb, var(--bg), transparent); outline-color: color-mix(in srgb, var(--bg), white); }
.safe { background-image: url('/icon.svg#abcdef'); content: 'red'; }
"""
    hits = _scan_css(Path("ceres/static/example.css"), source, source)
    assert [(hit.value, hit.line) for hit in hits] == [
        ("#abc", 3),
        ("rgba(1, 2, 3, .4)", 3),
        ("white", 4),
    ]


def test_html_scanner_is_limited_to_style_contexts():
    source = """
<!-- <i style="color:red"> -->
<a id="text-filter" href="#abcdef">red prose</a>
<i style="--local:#fff; color:rebeccapurple; box-shadow:0 0 1px #1234"></i>
"""
    hits = _scan_html(Path("ceres/static/example.html"), source)
    assert [(hit.value, hit.line) for hit in hits] == [
        ("rebeccapurple", 4),
        ("#1234", 4),
    ]


def test_js_scanner_covers_style_apis_and_css_templates_only():
    source = """
node.style.color = '#bad'
const prose = 'red and #abcdef';
node.style.color = enabled ? '#fff' : 'rgb(0 0 0 / .5)';
node.style.setProperty('border-color', 'hsl(10 20% 30%)');
node.style.setProperty('--palette-entry', '#123456');
node.style['outlineColor'] = 'oklch(40% .2 10)';
node.setAttribute('style', 'fill:#abcd');
const css = `.sample { background: blue; }`;
const declarations = `color:#123; inline-size:${width}px;`;
"""
    hits = _scan_js(Path("ceres/static/example.js"), source)
    assert [(hit.value, hit.line) for hit in hits] == [
        ("#fff", 4),
        ("rgb(0 0 0 / .5)", 4),
        ("hsl(10 20% 30%)", 5),
        ("oklch(40% .2 10)", 7),
        ("#abcd", 8),
        ("blue", 9),
        ("#123", 10),
    ]


def test_colour_allowlist_schema_is_explicit():
    manifest = _allowlist()
    assert set(manifest) == {"schema_version", "rule", "reviewed_on", "legacy_allowlist"}
    assert manifest["schema_version"] == 1
    assert manifest["rule"] == ""
    assert manifest["reviewed_on"] == "2026-09-15"
    for entry in manifest["legacy_allowlist"]:
        assert set(entry) == {"file", "match_key", "value", "reason", "expiry_phase", "expiry_date"}
        assert entry["file"].startswith("ceres/static/")
        assert entry["match_key"] and entry["value"]
        assert entry["reason"] == "pre- legacy"
        assert entry["expiry_phase"] == 1
        assert date.fromisoformat(entry["expiry_date"]) >= date.today(), f"expired legacy colour: {entry!r}"


def test_raw_colours_match_reviewed_allowlist():
    observed = _observed_raw_colours()
    allowed = _allowlist()["legacy_allowlist"]
    observed_keys = Counter((item.file, item.match_key, item.value) for item in observed)
    allowed_keys = Counter((item["file"], item["match_key"], item["value"]) for item in allowed)
    new = observed_keys - allowed_keys
    stale = allowed_keys - observed_keys
    lines_by_key = {}
    for item in observed:
        lines_by_key.setdefault((item.file, item.match_key, item.value), []).append(item.line)
    new_lines = []
    for key, count in sorted(new.items()):
        file, _, value = key
        new_lines.extend(f"{file}:{line}:{value}" for line in lines_by_key[key][:count])
    stale_lines = [f"{file}:{match_key}:{value}" for (file, match_key, value), count in sorted(stale.items()) for _ in range(count)]
    message = []
    if new_lines:
        message.append("new raw colours; use a global var(--token):\n" + "\n".join(new_lines))
    if stale_lines:
        message.append("stale reviewed entries; remove them from the shrinking allowlist:\n" + "\n".join(stale_lines))
    assert not message, "\n\n".join(message)
