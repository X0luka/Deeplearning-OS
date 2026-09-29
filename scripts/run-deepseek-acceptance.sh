#!/bin/bash
set -euo pipefail
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
test -f "$task_root/.local/preparation/capture-complete.json"
test "$#" -eq 1
case "$1" in 1|2|3) ;; *) exit 2 ;; esac
set -- "$task_root/components/AgentOS-K/.venv/bin/python" "$task_root/scripts/deepseek_acceptance.py" --case "$1" --authorized-paid-test --diagnosed-proxy-path
mkdir -p "$task_root/.local/runtime-home" "$task_root/.local/tmp"
# User-authorized three fixed DeepSeek samples; private mount/PID namespaces.
# Outbound official API permitted for this runner only; no host listener. Receipt prevents replay.
# The general run-isolated.sh remains unchanged. Resource guard ends this session at 600s.
exec unshare --user --map-root-user --mount --pid --fork --mount-proc \
    /bin/bash --noprofile --norc -c '
set -euo pipefail
task_root=$1
shift
mount --make-rprivate /
mount -t tmpfs -o size=256m tmpfs /tmp
mkdir /tmp/dlos-bind
mount --bind "$task_root" /tmp/dlos-bind
resolver_target=$(readlink -f /etc/resolv.conf)
cp /etc/resolv.conf /tmp/dlos-resolv.conf
mount -t tmpfs -o size=16m tmpfs /home
mkdir -p "$task_root"
mount --bind /tmp/dlos-bind "$task_root"
mount -t tmpfs -o size=1m tmpfs /mnt
# Preserve DNS only inside this private mount; WSL resolver is a /mnt symlink.
if [[ "$resolver_target" == /mnt/* ]]; then
    mkdir -p -- "$(dirname -- "$resolver_target")"
    cp /tmp/dlos-resolv.conf "$resolver_target"
fi
mount -t tmpfs -o size=1m tmpfs /run
mount -t tmpfs -o size=1m tmpfs /root
mount -t tmpfs -o size=32m tmpfs /dev/shm
mount --rbind / /
mount -o remount,bind,ro /
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
