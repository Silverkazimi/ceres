""" / : every static module must import or define each cross-module function it calls, and every name it
imports from a sibling module must really be exported there.

The VM tests run all modules in one shared global context, so a missing import can never fail them (aletheia round 6 F2);
only the browser would show it. This guard reads ceres/static/*.js statically and changes no behaviour."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "ceres" / "static"
EXPORT_RE = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
EXPORT_LIST_RE = re.compile(r"^export\s*\{([^}]*)\}(?!\s*from)", re.M)
IMPORT_RE = re.compile(r"^\s*import\s*\{([^}]*)\}\s*from\s*['\"]\./([\w.\-]+)['\"]\s*;?", re.M)
RE_EXPORT_NAMES_RE = re.compile(r"^\s*export\s*\{([^}]*)\}\s*from\s*['\"]", re.M)
DYNAMIC_IMPORT_RE = re.compile(r"\{([^{}]*)\}\s*=\s*await\s+import\(")   # const { a } = await import('./x.js')
REEXPORT_RE = re.compile(r"^\s*export\s*(?:\{[^}]*\}|\*)\s*from\s*['\"][^'\"]+['\"]\s*;?", re.M)


def _strip(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return re.sub(r"(?<![:'\"`])//[^\n]*", "", src)


def _names(block):
    for part in block.split(","):
        part = part.strip()
        if part:
            yield [p.strip() for p in part.split(" as ")]


def _exports(src):
    names = set(EXPORT_RE.findall(src))
    for block in EXPORT_LIST_RE.findall(src):
        names.update(pair[-1] for pair in _names(block))
    for block in RE_EXPORT_NAMES_RE.findall(src):      # export { a, b } from './x.js'
        names.update(pair[-1] for pair in _names(block))
    return names


def _imported(src):
    names = set()
    for m in IMPORT_RE.finditer(src):
        names.update(pair[-1] for pair in _names(m.group(1)))
    for m in re.finditer(r"\(\s*\{([^}]*)\}\s*\)\s*=>", src):  # import('./x.js').then(({ a }) => ...)
        names.update(p.strip().split(":")[-1].strip() for p in m.group(1).split(",") if p.strip())
    for m in DYNAMIC_IMPORT_RE.finditer(src):
        names.update(p.strip().split(":")[-1].strip() for p in m.group(1).split(",") if p.strip())
    return names


def _defined(src, name):
    """Declared in this file: function / const / let / var / class, or a parameter of a function or arrow function."""
    n = re.escape(name)
    return bool(re.search(rf"(?:function\*?\s+{n}\b|(?:const|let|var|class)\s+{n}\b"
                          rf"|function\s*[\w$]*\s*\([^()]*\b{n}\b[^()]*\)|\([^()]*\b{n}\b[^()]*\)\s*=>|\b{n}\s*=>)", src))


def _without_strings(src):
    """Quoted text is not code: 'reviewHtml ()' in a note is not a call."""
    return re.sub(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"", "''", src)


def _body(src):
    """Source without import / re-export statements, so a name listed in an import line is never read as a use."""
    return REEXPORT_RE.sub("", IMPORT_RE.sub("", src))


CALL_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")


def test_every_static_module_imports_or_defines_the_cross_module_functions_it_calls():
    sources = {p.name: _strip(p.read_text(encoding="utf-8")) for p in sorted(STATIC.glob("*.js"))}
    owners = {}
    for name, src in sources.items():
        for exported in _exports(src):
            owners.setdefault(exported, []).append(name)
    problems = []
    for fname, src in sources.items():
        have, body = _imported(src), _body(src)
        for name in sorted(set(CALL_RE.findall(_without_strings(body)))):
            others = [o for o in owners.get(name, ()) if o != fname]
            if not others or name in have or _defined(body, name):
                continue
            problems.append(f"{fname} calls {name}() (exported by {', '.join(others)}) without importing or defining it")
    assert not problems, "Missing imports (the browser would throw a ReferenceError):\n" + "\n".join(problems)


def test_every_name_a_static_module_imports_is_exported_by_that_module():
    sources = {p.name: _strip(p.read_text(encoding="utf-8")) for p in sorted(STATIC.glob("*.js"))}
    exports = {name: _exports(src) for name, src in sources.items()}
    problems = []
    for fname, src in sources.items():
        for m in IMPORT_RE.finditer(src):
            target = m.group(2)
            if target not in exports:
                continue    # not a ceres/static module
            for pair in _names(m.group(1)):
                if pair[0] not in exports[target]:
                    problems.append(f"{fname} imports {pair[0]} from ./{target}, which does not export it")
    assert not problems, "Imports of names that do not exist:\n" + "\n".join(problems)
