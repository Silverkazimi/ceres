"""Fixture server that logs every upstream lookup (host, method, url, bytes, ms) to $UPSTREAM_LOG.
Run from the repo root: python <this> --host 127.0.0.1 --port N --no-reload-watch --player sample01"""
import os, sys, time, json, threading
sys.path.insert(0, os.getcwd())
from ceres import fixture_upstream as fu
_log = open(os.environ["UPSTREAM_LOG"], "a", buffering=1)
_lock = threading.Lock()
_orig = fu.FixtureSet.lookup
def lookup(self, method, host, url, data=None):
    t = time.perf_counter(); out = _orig(self, method, host, url, data)
    try: size = len(out[2]) if isinstance(out[2], (bytes, bytearray)) else -1
    except Exception: size = -1
    with _lock:
        _log.write(json.dumps({"t": time.time(), "host": host, "m": method, "url": url, "status": out[0] if isinstance(out[0], int) else None, "bytes": size, "ms": round((time.perf_counter()-t)*1000, 2)}) + "\n")
    return out
fu.FixtureSet.lookup = lookup
if os.environ.get("NO_PACING") == "1":
    from praetoria import land_analysis as la
    la.CONCURRENT_DISPATCH_INTERVAL_SECONDS = 0.0
    _init = la.LandCapture.__init__
    def _init0(self, *a, **k):
        _init(self, *a, **k); self.min_delay = 0.0
    la.LandCapture.__init__ = _init0
from tests.ceres import offline_server
offline_server.main()
