#!/usr/bin/env python3
"""PostToolUse detector: report writes that landed in the PRIMARY working tree.

The companion to `guard-shared-worktree.py`, covering the half that guard
structurally cannot.

WHY A SECOND HOOK. The PreToolUse guard denies file-tool edits by path and git
mutations by parsing the command. For Bash it only ever inspects commands whose
command WORD is `git` — so every other way a shell command can write a file
passes untouched:

    echo x > config.py            sed -i s/a/b/ config.py       tee config.py
    cp / mv / rm / truncate / dd  python -c 'open(...,"w")'      patch, tar -x
    a heredoc feeding any interpreter, or any script the command invokes

Closing that by parsing is not achievable: deciding whether an arbitrary shell
string writes to a path — through interpreters, subshells, and cwd-relative
paths — is undecidable in general, and every approximation is both leaky (misses
a vector) and noisy (denies legitimate writes to worktrees and /tmp). The guard's
own docstring already concedes this class.

So this hook does not predict. It OBSERVES: after each Bash call it re-reads the
primary tree's state and compares it to a baseline. That is decidable, costs one
`git status` (~10-30ms), and catches every write vector including ones nobody
enumerated — because it watches the effect, not the syntax.

It also catches, for free, the collision the whole rule set exists for: ANOTHER
session switching the shared primary tree's branch out from under this one.

WHAT IT IS NOT. It detects; it does not prevent. The write has already happened
when this fires — the value is that it surfaces immediately, before it is
committed or built upon, instead of being discovered later as mystery dirt. And
with genuinely concurrent sessions it cannot tell YOUR write from another
session's, so it reports what changed and says so rather than accusing.

Scope: PostToolUse on Bash, the demonstrated hole. Broadening the matcher (e.g.
to MCP write tools, which the PreToolUse guard also cannot see) is a one-line
change in settings.json; it is not done here because Bash is the gap actually
observed, and a `git status` after every Read would be cost with no cause.

Baseline lives beside the solo marker in the PRIMARY git dir, keyed by session,
and is REFRESHED after each report so one drift is reported once rather than on
every subsequent command.

Solo mode (CIM_SOLO / .git/cim-solo) disables it: deliberate solo work dirties
the primary tree by design, and reporting that would be noise.

Pure-stdlib. Every path exits 0 — a detector must never fail a tool call.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

#: Stale baselines from finished sessions are swept on write. A week is long
#: enough that a paused session resuming still finds its own file.
BASELINE_TTL_SECONDS = 7 * 24 * 60 * 60
BASELINE_PREFIX = "cim-tree-baseline-"

#: A truncated status listing is still an accurate alarm; an unbounded one
#: could paste a 10k-line generated tree into the transcript.
MAX_REPORTED_PATHS = 20


def git(*args):
    """Read-only git. Returns RAW stdout, or None when the command failed.

    Raw, deliberately: `status --porcelain` is a COLUMNAR format whose first
    two characters are the index/worktree status and whose third is a space
    (` M config.py`). Stripping the result would eat that leading space and
    shift every path parse one character left. The caller must also
    distinguish "no output" from "could not ask" — an empty porcelain listing
    legitimately means a clean tree, which is the single most common case here.
    """
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=5)
    except Exception:
        return None
    return p.stdout if p.returncode == 0 else None


def git_value(*args):
    """A single-line git read (HEAD, branch, toplevel), stripped."""
    out = git(*args)
    return None if out is None else out.strip()


def quiet():
    sys.exit(0)


def report(context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    }))
    sys.exit(0)


def primary_git_dir(path):
    """Absolute git dir of the PRIMARY tree of `path`'s clone.

    A linked worktree's git dir is `<shared>/.git/worktrees/<name>`, which maps
    back to the shared `.git` — so a session working inside a worktree still
    watches the primary tree, which is exactly the point.
    """
    gd = git_value("-C", path or ".", "rev-parse", "--absolute-git-dir")
    if not gd:
        return ""
    m = re.match(r"^(.*)/worktrees/[^/]+/?$", gd)
    return os.path.abspath(m.group(1) if m else gd)


def primary_worktree(git_dir):
    """The primary tree's checkout root. `git_dir` is normally
    `<root>/.git`, but a bare-ish or relocated layout is possible, so ask git
    rather than assuming the parent directory."""
    root = git_value("-C", os.path.dirname(git_dir) or ".", "rev-parse",
                     "--show-toplevel")
    return root or (os.path.dirname(git_dir) or "")


def snapshot(tree):
    """(dirty paths, HEAD, branch) for `tree`, or None if git can't answer.

    `status --porcelain` is parsed into a set of PATHS rather than kept as raw
    text: comparing raw text would flag a file merely moving from unstaged to
    staged, which is not a new write and not what this watches for.
    """
    status = git("-C", tree, "status", "--porcelain")
    head = git_value("-C", tree, "rev-parse", "HEAD")
    if status is None or head is None:
        return None
    paths = set()
    for line in status.splitlines():
        # `XY PATH` — two status columns then a space. Anything shorter is not
        # a real entry. The slice is at 3 and the string is NOT pre-stripped;
        # see git() for why that matters.
        if len(line) > 3:
            # Rename/copy entries read "R  old -> new"; the destination is the
            # written path, so keep that side.
            path = line[3:].strip()
            paths.add(path.split(" -> ")[-1].strip().strip('"'))
    branch = git_value("-C", tree, "branch", "--show-current")
    return {"paths": sorted(paths), "head": head,
            "branch": branch if branch is not None else ""}


def baseline_path(git_dir, session_id):
    key = hashlib.sha256((session_id or "nosession").encode()).hexdigest()[:16]
    return os.path.join(git_dir, BASELINE_PREFIX + key)


def sweep_stale(git_dir):
    """Best-effort removal of baselines from long-finished sessions, so the
    git dir does not accumulate one file per session forever."""
    try:
        now = time.time()
        for name in os.listdir(git_dir):
            if not name.startswith(BASELINE_PREFIX):
                continue
            p = os.path.join(git_dir, name)
            if now - os.path.getmtime(p) > BASELINE_TTL_SECONDS:
                os.unlink(p)
    except Exception:
        pass


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def store(path, snap):
    """Write the baseline atomically — a truncated file read by the next call
    would look like a corrupt baseline and re-arm a false alarm."""
    try:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _listing(paths):
    shown = paths[:MAX_REPORTED_PATHS]
    more = len(paths) - len(shown)
    out = "\n".join(f"    {p}" for p in shown)
    if more > 0:
        out += f"\n    … and {more} more"
    return out


def build_report(tree, before, after):
    """The findings, or '' when nothing worth saying changed."""
    parts = []

    new_paths = sorted(set(after["paths"]) - set(before.get("paths") or []))
    if new_paths:
        parts.append(
            "Files changed in the PRIMARY working tree that were not dirty "
            "before your last command:\n" + _listing(new_paths))

    if before.get("head") and after["head"] != before["head"]:
        parts.append(
            f"The primary tree's HEAD moved: {before['head'][:12]} → "
            f"{after['head'][:12]}.")

    if before.get("branch") != after["branch"]:
        parts.append(
            f"The primary tree's branch changed: "
            f"'{before.get('branch') or '(detached)'}' → "
            f"'{after['branch'] or '(detached)'}'.")

    if not parts:
        return ""

    return (
        f"PRIMARY-TREE DRIFT DETECTED at {tree}\n\n"
        + "\n\n".join(parts)
        + "\n\nThis hook watches the shared primary tree because the PreToolUse "
          "guard cannot see Bash file writes (redirects, sed -i, interpreters "
          "fed by a heredoc, and anything a script does).\n"
          "  • If your last command did this — it was almost certainly a "
          "relative path resolving against the primary tree instead of your "
          "worktree, since `cd` does not persist between calls. Undo it "
          "(`git -C <primary> checkout -- <path>`) and re-run with ABSOLUTE "
          "paths or `git -C <worktree>`.\n"
          "  • If it was not you, a concurrent session is working in the "
          "primary tree. Leave it alone — never add, commit over, or revert "
          "another session's files.\n"
          "This hook cannot tell those two apart; it reports what changed.\n"
          "Reported once — the baseline is now refreshed to this state."
    )


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
        quiet()                       # not a repo — nothing to watch

    # Solo mode: the operator is deliberately working in the primary tree, so
    # every command would trip this. Checked AFTER resolving the git dir so the
    # marker is read from the same clone the guard reads it from.
    if os.environ.get("CIM_SOLO") or os.path.exists(
            os.path.join(git_dir, "cim-solo")):
        quiet()

    tree = primary_worktree(git_dir)
    if not tree or not os.path.isdir(tree):
        quiet()

    after = snapshot(tree)
    if after is None:
        quiet()                       # git could not answer; say nothing

    path = baseline_path(git_dir, data.get("session_id"))
    before = load(path)
    if before is None:
        # First call of the session (or an unreadable baseline): establish it
        # and stay silent. There is nothing to compare against yet, and
        # inventing an alarm from a missing baseline would train the operator
        # to ignore this hook.
        sweep_stale(git_dir)
        store(path, after)
        quiet()

    context = build_report(tree, before, after)
    if not context:
        # Refresh anyway: a path returning to clean must not leave the baseline
        # claiming it is still dirty, or the next real write to it is missed.
        store(path, after)
        quiet()

    store(path, after)
    report(context)


if __name__ == "__main__":
    main()
