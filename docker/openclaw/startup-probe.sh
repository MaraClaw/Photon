#!/bin/sh

probe_helper=$probe_dir/startup-probe.py
probe_pid=
probe_file=
probe_sequence=0
probe_cancelled=0
probe_interrupted=0
probe_control=${TMPDIR:-/tmp}/openclaw-probe.$$.control
probe_control_owned=0
probe_channel_ready=0

probe_cancel() {
    if [ "$probe_cancelled" -eq 0 ]; then
        probe_cancelled=$1
        if [ "$probe_channel_ready" -eq 1 ]; then
            printf '%s\n' "$probe_cancelled" >&4
        fi
    fi
    probe_interrupted=1
}

probe_cleanup() {
    if [ -n "$probe_file" ]; then
        rm -f -- "$probe_file"
        probe_file=
    fi
}

probe_exit() {
    probe_cleanup
    if [ "$probe_channel_ready" -eq 1 ]; then
        exec 4>&-
    fi
    if [ "$probe_control_owned" -eq 1 ]; then
        rm -f -- "$probe_control"
    fi
    if [ "$probe_cancelled" -ne 0 ]; then
        exit "$((128 + probe_cancelled))"
    fi
}

trap 'probe_cancel 1' HUP
trap 'probe_cancel 2' INT
trap 'probe_cancel 15' TERM
trap 'probe_exit' 0

probe_check_cancelled() {
    if [ "$probe_cancelled" -ne 0 ]; then
        exit "$((128 + probe_cancelled))"
    fi
}

probe_wait() {
    probe_pid=$!
    while :; do
        probe_interrupted=0
        if wait "$probe_pid"; then
            probe_status=0
        else
            probe_status=$?
        fi
        [ "$probe_interrupted" -ne 0 ] || break
    done
    probe_pid=
    probe_check_cancelled
    case $probe_status in
        2|124|129|130|143) exit "$probe_status" ;;
    esac
    return "$probe_status"
}

probe_run() {
    probe_check_cancelled
    python3 -B "$probe_helper" --run "$@" 3<"$probe_control" >/dev/null &
    probe_wait
}

probe_capture() {
    probe_check_cancelled
    probe_sequence=$((probe_sequence + 1))
    probe_file=${TMPDIR:-/tmp}/openclaw-probe.$$.$probe_sequence
    if ! (umask 077; set -C; : > "$probe_file") 2>/dev/null; then
        probe_file=
        printf '%s\n' 'Cannot create private startup probe capture' >&2
        exit 2
    fi
    python3 -B "$probe_helper" --capture "$@" 3<"$probe_control" >"$probe_file" &
    if probe_wait; then
        probe_status=0
    else
        probe_status=$?
    fi
    probe_output=
    while IFS= read -r probe_line || [ -n "$probe_line" ]; do
        probe_output=$probe_output$probe_line'
'
    done <"$probe_file"
    while :; do
        case $probe_output in
            *'
') probe_output=${probe_output%?} ;;
            *) break ;;
        esac
    done
    probe_cleanup
    probe_check_cancelled
    return "$probe_status"
}

probe_finish() {
    probe_cleanup
    probe_check_cancelled
    rm -f -- "$probe_control"
    probe_control_owned=0
    trap - HUP INT TERM
    probe_check_cancelled
    trap - 0
    exec 4>&-
}

probe_check_cancelled
if ! (umask 077; set -C; : > "$probe_control") 2>/dev/null; then
    printf '%s\n' 'Cannot create private startup probe control' >&2
    exit 2
fi
probe_control_owned=1
exec 4>"$probe_control"
probe_channel_ready=1
probe_check_cancelled
python3 -B "$probe_helper" --check-policy 3<"$probe_control" >/dev/null &
if probe_wait; then
    :
else
    exit "$probe_status"
fi
