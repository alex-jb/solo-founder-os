#!/usr/bin/env bash
# run_weekly_retro.sh — Sunday morning sfos-retro wrapper.
#
# Fired by ~/Library/LaunchAgents/com.alexji.sfos-weekly-retro.plist
# at Sunday 09:00 local time. Runs sfos-retro across all 8 (and
# counting) agents in the SFOS stack, writes markdown report to
# ~/.solo-founder-os/retro-<UTC date>.md, and notifies via osascript
# + solo-founder-os fan_out (if NTFY/Telegram/Slack configured).

set -u

LOG="$HOME/.solo-founder-os/launchd_retro_$(date -u +%Y-%m-%d).log"
mkdir -p "$(dirname "$LOG")"

{
    echo "==> $(date -u +%FT%TZ) weekly sfos-retro start"

    # sfos-retro is the v0.17 console script; falls back to module
    # invocation if the script isn't on PATH (e.g. when SFOS is
    # installed editable into a venv that PATH doesn't see).
    if command -v sfos-retro >/dev/null 2>&1; then
        out=$(sfos-retro --since 7 2>&1)
    else
        out=$(PYTHONPATH=/Users/alexji/Desktop/solo-founder-os \
              python3 -m solo_founder_os.cross_agent_report --since 7 2>&1)
    fi
    echo "$out"

    # Last line of `sfos-retro` stdout is the one-line summary like:
    # "5 agents active · 35 reflections · 0 skills · 0 bandits"
    summary=$(echo "$out" | tail -1)
    [ -z "$summary" ] && summary="📊 weekly sfos-retro generated"

    osascript -e "display notification \"${summary//\"/\\\"}\" with title \"SFOS weekly retro\" sound name \"Glass\"" 2>/dev/null || true

    if python3 -c "import solo_founder_os" 2>/dev/null; then
        python3 - <<EOF || true
from solo_founder_os.notifier import fan_out
fan_out(["ntfy", "telegram", "slack"], """${summary//\"/\\\"}""",
         title="SFOS weekly retro")
EOF
    fi

    echo "==> $(date -u +%FT%TZ) done"
} >> "$LOG" 2>&1
