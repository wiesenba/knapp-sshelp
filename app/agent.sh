# Optional managed agent for interactive Bash shells. Never auto-loads a key.
_sshelp_agent_reachable() {
    ssh-add -l >/dev/null 2>&1
    local status=$?
    [[ $status == 0 || $status == 1 ]]
}
sshelp_agent() {
    command -v ssh-add >/dev/null 2>&1 || return 0
    if [[ -n ${SSH_AUTH_SOCK:-} ]] && _sshelp_agent_reachable; then
        return 0  # Preserve working forwarded or editor-provided agents.
    fi
    command -v flock >/dev/null 2>&1 || return 0
    local directory="${XDG_STATE_HOME:-$HOME/.local/state}/sshelp/agent"
    [[ ! -L $directory ]] || return 1
    (umask 077; mkdir -p -- "$directory") || return 1
    chmod 700 -- "$directory" || return 1
    local socket="$directory/socket"
    [[ ! -L $socket && ! -L $directory/lock ]] || return 1
    (
        umask 077
        flock -x 9 || exit 1
        export SSH_AUTH_SOCK="$socket"
        if ! _sshelp_agent_reachable; then
            [[ ! -e $socket || -S $socket ]] || exit 1
            rm -f -- "$socket"
            ssh-agent -a "$socket" >/dev/null || exit 1
        fi
    ) 9>"$directory/lock" || return 1
    export SSH_AUTH_SOCK="$socket"
    unset SSH_AGENT_PID
}
sshelp_agent