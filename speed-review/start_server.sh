#!/bin/bash
# usage: start_server.sh PORT  -- starts the metered offline fixture server in the background (repo root as cwd)
S=${SPEED_SCRATCH:-/tmp/ceres-speed}; mkdir -p $S; D=$(dirname "$(readlink -f "$0")")
PORT=${1:-27411}
H=$S/home-$PORT; rm -rf $H; mkdir -p $H/logs
cd "$(git -C "$D" rev-parse --show-toplevel)"
env HOME=$H TMPDIR=$H CERES_TEST_LOG_DIR=$H/logs CERES_FIXTURE_UPSTREAM=$PWD/tests/fixtures/upstream/board-basic \
  CERES_FIXTURE_OVERLAYS=browser-combine PRAETORIA_CACHE_DIR=$H/cache CERES_NO_STORED_DATA_CLEANUP=1 \
  CERES_CAPTURE_IN_PROCESS=1 CERES_CLIENT_LIMITS=off PYTHONDONTWRITEBYTECODE=1 UPSTREAM_LOG=$H/upstream.jsonl NO_PACING=${NO_PACING:-0} \
  python $D/metered_server.py --host 127.0.0.1 --port $PORT --no-reload-watch --player sample01 > $H/server.log 2>&1 &
echo $! > $H/pid
for i in $(seq 1 60); do curl -fs http://127.0.0.1:$PORT/api/health >/dev/null && break; sleep 0.5; done
echo "up on $PORT home=$H"
