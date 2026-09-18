#!/bin/sh
set -eu

target=$1
staged=$2
candidate=$3
backup=$4
pids=$5
expected_size=$6
expected_digest=$7
job_dir=$(dirname -- "$candidate")
ready=0
replaced=0
previous_exited=0
unset APPIMAGE APPDIR OWD
export PYINSTALLER_RESET_ENVIRONMENT=1
exec 2>>"$job_dir/error.log"

finish() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        printf '%s\n' 'AppImage update failed.' >&2
        if [ "$replaced" -eq 1 ]; then
            cp -p -- "$backup" "$staged" && mv -f -- "$staged" "$target"
        fi
        if [ "$ready" -eq 1 ] && [ "$previous_exited" -eq 1 ] && [ -x "$target" ]; then
            "$target" >/dev/null 2>&1 &
        fi
    fi
    rm -f -- "$staged"
    exit "$result"
}
trap finish EXIT
trap 'exit 1' TERM INT

[ "$(wc -c < "$staged")" -eq "$expected_size" ]
digest=$(sha256sum < "$staged")
[ "${digest%% *}" = "$expected_digest" ]
[ -f "$target" ]
cp -p -- "$target" "$backup"
printf '%s\n' ready > "$job_dir/ready"
ready=1

for pid in $pids; do
    attempts=0
    while kill -0 "$pid" 2>/dev/null; do
        attempts=$((attempts + 1))
        [ "$attempts" -le 600 ] || { printf '%s\n' 'The previous application is still running.' >&2; exit 1; }
        sleep 0.1
    done
done
previous_exited=1

mv -f -- "$staged" "$target"
replaced=1
rm -f -- "$candidate"
cd -- "$(dirname -- "$target")"
"$target" >/dev/null 2>&1 &
