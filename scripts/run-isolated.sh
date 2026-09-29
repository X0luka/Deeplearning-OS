#!/bin/bash
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
test -f "$task_root/.local/preparation/capture-complete.json" || test -f "$task_root/.local/development.json"
test "$#" -gt 0
mkdir -p "$task_root/.local/runtime-home" "$task_root/.local/tmp"
# Private mount/PID/network namespaces; no global mounts or WSL configuration changes.
exec unshare --user --map-root-user --mount --net --pid --fork --mount-proc \
    /bin/bash --noprofile --norc -c '
set -euo pipefail
task_root=$1
shift
mount --make-rprivate /
mount -t tmpfs -o size=256m tmpfs /tmp
mkdir /tmp/dlos-bind
mount --bind "$task_root" /tmp/dlos-bind
mount -t tmpfs -o size=16m tmpfs /home
mkdir -p "$task_root"
mount --bind /tmp/dlos-bind "$task_root"
mount -t tmpfs -o size=1m tmpfs /mnt
mount -t tmpfs -o size=1m tmpfs /run
mount -t tmpfs -o size=1m tmpfs /root
mount -t tmpfs -o size=32m tmpfs /dev/shm
mount --rbind / /
mount -o remount,bind,ro /
ip link set lo up
cd "$task_root"
# Chromium reserves large virtual ranges. Bound sampled RSS instead of address space.
ulimit -t 300
exec env -i PATH="$task_root/.local/tools/node/bin:/usr/bin:/bin" LANG=C.UTF-8 \
    HOME="$task_root/.local/runtime-home" TMPDIR="$task_root/.local/tmp" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
    PLAYWRIGHT_BROWSERS_PATH="$task_root/.local/browsers" \
    NODE_OPTIONS=--max-old-space-size=256 \
    DLOS_ISOLATED_TEST=1 python3 "$task_root/scripts/resource_guard.py" "$@"
' isolated "$task_root" "$@"
