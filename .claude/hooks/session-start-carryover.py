#!/usr/bin/env python3
"""SessionStart: put the carried-over context back after a reset.

The restoring half of the pair; `precompact-carryover.py` saves.

WHEN IT FIRES. `SessionStart` runs on every session, with a `source` naming
why: `startup | resume | clear | compact | fork`. This hook acts only on
`clear` and `compact` — the two that DISCARD context. `resume` already
restores the real transcript, and re-injecting a summary beside it would be
noise arguing with the record. `startup` is a genuinely new session and
`fork` inherits its parent's context.

`hookSpecificOutput.additionalContext` is the only supported way to force
content into a post-reset context; a hook cannot invoke `/compact` or
`/clear`, and no hook event can trigger one. This is a companion to
`autoCompactWindow` in settings.json, which is what actually does the
resetting — not a replacement for it.

WHAT IT EMITS, in priority order:

  1. `CARRYOVER_NOTES` — the agent's own hand-written notes. First because
     it is the only part a machine cannot reconstruct, and if the emission
     had to be truncated this is the part worth keeping.
  2. The PreCompact snapshot — branch, worktree, dirty paths, commits in
     flight. Derivable, so it is cheap to regenerate and safe to drop.

THE SESSION-ID PROBLEM, stated because the fallback below looks arbitrary
without it: `/clear` may start a NEW session id, which orphans a snapshot
keyed by the old one. So the session-keyed lookup is tried first and a
bounded newest-snapshot scan backs it up. The bound matters — re-injected
text reads as authoritative by construction, so restoring a two-day-old
snapshot would be worse than restoring nothing.

Pure-stdlib. Every path exits 0 — a status hook must never block a session.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared_tree import (carryover_path, latest_carryover,  # noqa: E402
                          notes_path, primary_git_dir)

#: The two sources that discard context. Everything else is left alone.
RESTORE_ON = {"clear", "compact"}

#: Caps. The restored context competes for the very window the reset just
#: freed, so this stays a briefing, not an archive.
MAX_NOTES_CHARS = 8000
MAX_LINES = 15


def quiet():
    sys.exit(0)


def emit(context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    sys.exit(0)


def read_notes(git_dir):
    p = notes_path(git_dir)
    if not p or not os.path.exists(p):
        return ""
    try:
        with open(p) as f:
            text = f.read()
    except Exception:
        return ""
    text = text.strip()
    if len(text) > MAX_NOTES_CHARS:
        text = (text[:MAX_NOTES_CHARS]
                + f"\n… (truncated at {MAX_NOTES_CHARS} chars; full notes at {p})")
    return text


def load_snapshot(git_dir, session_id):
    """This session's snapshot, or the newest recent one. ('' when neither.)"""
    for path in (carryover_path(git_dir, session_id),
                 latest_carryover(git_dir)):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version"):
            return data
    return None


def _age(saved_at):
    try:
        mins = max(0, int((time.time() - float(saved_at)) / 60))
    except Exception:
        return "unknown age"
    return f"{mins} min ago" if mins < 120 else f"{mins // 60} h ago"


def _block(title, lines):
    if not lines:
        return ""
    shown = lines[:MAX_LINES]
    more = len(lines) - len(shown)
    out = f"\n{title}\n" + "\n".join(f"    {l}" for l in shown)
    if more > 0:
        out += f"\n    … and {more} more"
    return out


def render(notes, snap):
    parts = ["CARRIED-OVER CONTEXT (restored after a context reset)"]

    if notes:
        parts.append(
            "Session notes — written by hand during the session, so this is "
            "the part no snapshot could reconstruct:\n\n" + notes)

    if snap:
        head = (f"Git state at reset ({_age(snap.get('saved_at'))}"
                f"{', ' + snap['trigger'] if snap.get('trigger') else ''}):")
        lines = []
        if snap.get("worktree"):
            lines.append(f"working tree: {snap['worktree']}")
        if snap.get("branch"):
            lines.append(f"branch:       {snap['branch']} @ {snap.get('head', '')}")
        body = head + "\n" + "\n".join(f"    {l}" for l in lines)
        body += _block("commits not yet on origin/main:", snap.get("commits") or [])
        body += _block("uncommitted paths:", snap.get("dirty") or [])
        body += _block("worktrees at reset:", snap.get("worktrees") or [])
        parts.append(body)
        parts.append(
            "Re-assert this before acting on it — `git -C <worktree> branch "
            "--show-current` and `git status`. The snapshot is a point-in-time "
            "record, and a concurrent session may have moved things since.")

    return "\n\n".join(parts)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        quiet()
    if not isinstance(data, dict):
        quiet()

    if data.get("source") not in RESTORE_ON:
        quiet()

    session_cwd = data.get("cwd")
    session_cwd = session_cwd if isinstance(session_cwd, str) else "."
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or session_cwd

    git_dir = primary_git_dir(project_dir)
    if not git_dir or not os.path.isdir(git_dir):
        quiet()

    notes = read_notes(git_dir)
    snap = load_snapshot(git_dir, data.get("session_id"))
    if not notes and not snap:
        quiet()                        # nothing carried — say nothing

    emit(render(notes, snap))


if __name__ == "__main__":
    main()
