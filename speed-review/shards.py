"""Replicates tests/conftest.py::_shard_collection on a collected id list and prints per-shard predicted seconds.
usage: python -I shards.py collect.txt N durations.json"""
import sys, json, math, collections
ids = [l.strip() for l in open(sys.argv[1]) if "::" in l]; n = int(sys.argv[2]); dur = json.load(open(sys.argv[3]))
by = collections.OrderedDict()
for i in ids: by.setdefault(i.split("::")[0], []).append(i)
w = {p: float(dur.get(p, 8 * len(t))) for p, t in by.items()}
missing = [p for p in by if p not in dur]
target = sum(w.values()) / n
chunks = []
for p, t in by.items():
    pieces = max(1, min(len(t), math.ceil(w[p] / target)))
    for k in range(pieces): chunks.append((w[p] * len(t[k::pieces]) / len(t), p, k))
loads = [0.0] * n; files = [[] for _ in range(n)]
for cw, p, k in sorted(chunks, key=lambda c: (-c[0], c[1], c[2])):
    s = loads.index(min(loads)); loads[s] += cw; files[s].append(p)
print(f"files {len(by)} tests {len(ids)} total predicted {sum(w.values()):.0f}s, target/shard {target:.0f}s; files without a measured duration: {len(missing)} ({sum(w[p] for p in missing):.0f}s at 8s/test)")
print("per-shard predicted s:", [round(x) for x in loads], " max/mean", round(max(loads) / (sum(loads) / n), 2))
top = sorted(w.items(), key=lambda kv: -kv[1])[:8]
print("heaviest files:", [(p.split('/')[-1], round(s), len(by[p])) for p, s in top])
