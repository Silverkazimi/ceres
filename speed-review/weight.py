"""Static weight of the board page: raw, gzip, comment-stripped (esbuild --minify-whitespace off, legal comments none), minified.
usage (repo root): python -I weight.py ESBUILD_BIN board_reqs.json"""
import sys, json, gzip, subprocess, pathlib, collections
esb, reqs = sys.argv[1], json.load(open(sys.argv[2]))
root = pathlib.Path("ceres/static")
files = []
for r in reqs:
    if r.get("type") in ("script", "stylesheet") and "/static/" in r["url"]:
        files.append(r["url"].split("/static/", 1)[1].split("?")[0])
def gz(b): return len(gzip.compress(b, 6))
tot = collections.Counter()
rows = []
for f in files:
    src = (root / f).read_bytes()
    loader = "css" if f.endswith(".css") else "js"
    strip = subprocess.run([esb, f"--loader={loader}", "--legal-comments=none"], input=src, capture_output=True).stdout
    mini = subprocess.run([esb, f"--loader={loader}", "--minify", "--legal-comments=none"], input=src, capture_output=True).stdout
    row = (f, len(src), gz(src), len(strip), gz(strip), len(mini), gz(mini))
    rows.append(row)
    for i, k in enumerate(["raw", "raw_gz", "nocomment", "nocomment_gz", "min", "min_gz"]):
        tot[(loader, k)] += row[i + 1]
rows.sort(key=lambda r: -r[1])
print(f"{'file':32s} {'raw':>8s} {'gz':>7s} {'nocmt':>8s} {'nocmt_gz':>8s} {'min':>8s} {'min_gz':>7s}")
for r in rows[:25]: print(f"{r[0]:32s} {r[1]:8d} {r[2]:7d} {r[3]:8d} {r[4]:8d} {r[5]:8d} {r[6]:7d}")
print("files:", len(files))
for loader in ("js", "css"):
    print(loader, {k: tot[(loader, k)] for k in ["raw", "raw_gz", "nocomment", "nocomment_gz", "min", "min_gz"]})
