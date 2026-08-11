#!/usr/bin/env python3
"""PreCompact: snapshot the git state a compaction is about to summarize away.

The saving half of the carry-over pair; `session-start-carryover.py` restores.

WHY THIS EXISTS. Compaction summarizes the conversation, and a summary is a
lossy, model-authored artifact — it keeps what read as important mid-sentence,
not necessarily which worktree the session is committing into. On a clone with
the parallel-session rules in force that is the one fact whose loss is
expensive: an agent that forgets it owns `.claude/worktrees/<slug>` writes its
next edit against the primary tree, which is precisely the collision the whole
rule set exists to prevent.

WHAT IT DOES NOT DO. It does not block compaction. Exit 2 on this event
BLOCKS the compact — the opposite of the goal here, since the compaction is
what the operator asked for. Every path exits 0, matching the rule the other
three hooks in this directory state explicitly.

It also does not try to preserve reasoning. That is `CARRYOVER_NOTES`' job,
written by the agent by hand: which phase of a plan is in flight, what was
decided, what is next. This hook captures only what is DERIVABLE, so the notes
never have to restate git state that would be stale by the time it was read.
The two are separate files with separate authors for exactly that reason.

Solo mode does not disable it — solo mode is about whether the worktree rules
are being enforced, and a solo session's context is no less worth carrying.

Pure-stdlib. Every path exits 0.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared_tree import (carryover_path, git_out, git_value,  # noqa: E402
                          primary_git_dir, sweep_carryover)

#: Bumped when the snapshot's shape changes, so a reader never field-matches
#: an older shape against a newer one and reports nonsense.
SNAPSHOT_VERSION = 1

#: A truncated listing is still accurate; an unbounded one could paste a
#: generated tree into the restored context, which is the one place where
#: volume directly costs the thing being preserved.
MAX_PATHS = 15
MAX_COMMITS = 8


def quiet():
    sys.exit(0)


def dirty_paths(tree):
    status = git_out("-C", tree, "status", "--porcelain")
    if not status:
        return []
    out = []
    for line in status.splitlines():
        if len(line) <= 3:
            continue
        path = line[3:].strip().split(" -> ")[-1].strip().strip('"')
        out.append(path)
    return out[:MAX_PATHS]


def recent_commits(tree, upstream="origin/main"):
    """Commits on this branch that main does not have — the work in flight.

    Falls back to a plain log when the branch has no such range (detached,
    or `origin/main` unavailable), because reporting nothing here would read
    as "no work in progress", which is the wrong default.
    """
    log = git_out("-C", tree, "log", "--oneline", f"{upstream}..HEAD")
    if log is None:
        log = git_out("-C", tree, "log", "--oneline", f"-{MAX_COMMITS}")
    return (log or "").splitlines()[:MAX_COMMITS]


def worktree_lines(tree):
    listing = git_out("-C", tree, "worktree", "list")
    return (listing or "").splitlines()[:MAX_PATHS]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        quiet()
    if not isinstance(data, dict):
        quiet()

    session_cwd = data.get("cwd")
    session_cwd = session_cwd if isinstance(session_cwd, str) else "."
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or session_cwd

    git_dir = primary_git_dir(project_dir)
    if not git_dir or not os.path.isdir(git_dir):
        quiet()                        # not a repo — nothing to carry

    # The tree this SESSION is working in, which is the fact worth saving.
    # `primary_git_dir` deliberately resolves back to the shared git dir, so
    # it cannot answer this — ask the cwd directly.
    tree = git_value("-C", session_cwd, "rev-parse", "--show-toplevel") or ""
    branch = git_value("-C", session_cwd, "branch", "--show-current") or ""

    snapshot = {
        "version": SNAPSHOT_VERSION,
        "saved_at": time.time(),
        "trigger": data.get("trigger"),
        "message_count": data.get("message_count"),
        "cwd": session_cwd,
        "worktree": tree,
        "branch": branch,
        "head": git_value("-C", session_cwd, "rev-parse", "--short", "HEAD") or "",
        "dirty": dirty_paths(tree) if tree else [],
        "commits": recent_commits(tree) if tree else [],
        "worktrees": worktree_lines(tree) if tree else [],
    }

    try:
        path = carryover_path(git_dir, data.get("session_id"))
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(snapshot, f)
        os.replace(tmp, path)          # atomic: a torn read looks corrupt
        sweep_carryover(git_dir)
    except Exception:
        pass                           # never fail the compaction

    quiet()


if __name__ == "__main__":
    main()
