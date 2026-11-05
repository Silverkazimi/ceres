""": a static module that calls a broadcast-gate / shared-confirmer export must import or define it."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "ceres" / "static"
SOURCES = ("broadcast-gate.js", "shared-confirmer.js")
EXPORT_RE = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M)


def _strip(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return re.sub(r"(?<![:'\"`])//[^\n]*", "", src)


def _imported(src):
    names = set()
    for m in re.finditer(r"import\s*\{([^}]*)\}\s*from", src):
        for part in m.group(1).split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    for m in re.finditer(r"\(\s*\{([^}]*)\}\s*\)\s*=>", src):  # import('./x.js').then(({ a }) => ...)
        names.update(p.strip().split(":")[-1].strip() for p in m.group(1).split(",") if p.strip())
    return names


def _defined(src, name):
    n = re.escape(name)
    return bool(re.search(rf"(?:function\*?\s+{n}\b|(?:const|let|var|class)\s+{n}\b)", src))


def test_modules_import_every_gate_and_confirmer_function_they_call():
    exports = {f: set(EXPORT_RE.findall((STATIC / f).read_text())) for f in SOURCES}
    problems = []
    for path in sorted(STATIC.glob("*.js")):
        if path.name in SOURCES:
            continue
        src = _strip(path.read_text())
        have = _imported(src)
        for fname, names in exports.items():
            for name in sorted(names):
                if not re.search(rf"(?<![\w$.]){re.escape(name)}\s*\(", src):
                    continue
                if name in have or _defined(src, name):
                    continue
                problems.append(f"{path.name} calls {name} (from {fname}) without importing it")
    assert not problems, "\n".join(problems)
