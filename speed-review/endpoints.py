"""Time GET routes on a running fixture server. usage: python -I endpoints.py BASE"""
import sys, time, json, urllib.request, statistics, gzip
B = sys.argv[1]
def get(path, gz=False):
    req = urllib.request.Request(B + path, headers={"Accept-Encoding": "gzip"} if gz else {})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read(); code = r.status; enc = r.headers.get("content-encoding"); cc = r.headers.get("cache-control"); et = r.headers.get("etag")
    except urllib.error.HTTPError as e:
        body = e.read(); code = e.code; enc = None; cc = None; et = None
    return (time.perf_counter() - t) * 1000, code, body, enc, cc, et
board = json.loads(get("/api/board?player=sample01")[2])
plots = board.get("plots", [])
deed = plots[0]["deed_uid"] if plots else "x"
print("plots on board:", len(plots), "deed", deed)
paths = ["/", "/cards", "/api/health", "/api/board?player=sample01", "/api/board/status?player=sample01", f"/api/plots/{deed}?player=sample01",
         "/api/idle-cards?player=sample01", "/api/cooling?player=sample01", "/api/capture/status?player=sample01", "/api/capture/estimate?player=sample01",
         "/api/dec?player=sample01", "/api/wallet?player=sample01", "/api/wallet/rewards?player=sample01", "/api/sps-rate",
         "/api/harvest/routing/state?player=sample01", "/api/harvest/state?player=sample01", f"/api/plots/{deed}/candidates?player=sample01",
         f"/api/plots/{deed}/harvest?player=sample01", f"/api/plots/{deed}/taxes?player=sample01", "/api/cards?player=sample01",
         "/api/cards/combinables?player=sample01", "/api/cards/purchases?player=sample01", "/api/market?player=sample01", "/api/dev/watch"]
print(f"{'path':60s} code  median_ms  max_ms  raw_bytes  gzip_bytes cache-control")
for p in paths:
    runs = [get(p) for _ in range(5)]
    ms = [r[0] for r in runs]
    g = get(p, gz=True)
    print(f"{p[:60]:60s} {runs[0][1]}  {statistics.median(ms):8.1f}  {max(ms):7.1f}  {len(runs[0][2]):9d}  {len(g[2]):9d}  {runs[0][4]}")
