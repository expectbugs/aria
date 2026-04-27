#!/bin/bash
# Spawn interactive Claude Code with ARIA's system prompt.
# Full TUI — /effort, /model, /config, /cost all work.
#
# This is the raw ARIA brain without daemon wrappers (no context
# injection, no ACTION execution, no delivery). Useful for debugging
# model/effort settings and testing ARIA's personality directly.
#
# Usage: ./aria_interactive.sh

cd /home/user/aria
SYSTEM_PROMPT=$(./venv/bin/python -c "from system_prompt import build_primary_prompt; print(build_primary_prompt())")
CLAUDE_CODE_EFFORT_LEVEL=max \
CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
/usr/bin/claude \
    --model opus \
    --system-prompt "$SYSTEM_PROMPT"
