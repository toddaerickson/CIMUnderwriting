#!/usr/bin/env python3
"""PostToolUse detector: report writes that landed in the PRIMARY working tree.

The companion to `guard-shared-worktree.py`, covering the half that guard
structurally cannot.

WHY A SECOND HOOK. The PreToolUse guard denies file-tool edits by path and git
mutations by parsing the command. For Bash it only ever inspects commands whose
command WORD is `git` — so every other way a shell command writes a file passes
untouched:

    echo x > config.py            sed -i s/a/b/ config.py       tee config.py
    cp / mv / rm / truncate / dd  python -c 'open(...,"w")'      patch, tar -x
    a heredoc feeding any interpreter, or any script the command invokes

Closing that by parsing is not achievable: deciding whether an arbitrary shell
string writes to a path — through interpreters, subshells, and cwd-relative
paths — is undecidable in general, and every approximation is both leaky (misses
a vector) and noisy (denies legitimate writes to worktrees and /tmp). The guard's
own docstring already concedes the class.

So this hook does not predict. It OBSERVES: after each Bash call it re-reads the
primary tree's state and compares it to a baseline. That is decidable, costs one
`git status` plus a stat per dirty path, and is blind to HOW the write happened
— which is the property that makes it cover vectors nobody enumerated.

It also catches, for free, the collision the whole rule set exists for: ANOTHER
session switching the shared primary tree's branch out from under this one.

WHAT IT IS NOT — read this before trusting it:

  * It DETECTS, it does not prevent. The write has already happened when this
    fires. A report means "undo this now", not "you were protected".
  * It cannot see writes to GITIGNORED paths. `git status` is the eye, so
    anything `.gitignore` hides is invisible — `.env`, `deals/`, the comps DB,
    `*.log`, `staticfiles/`, `.venv/`. That exclusion is deliberate, not an
    oversight to fix later: those paths churn during ordinary test and dev runs,
    and reporting them would bury every real finding under build noise.
  * With genuinely concurrent sessions it cannot tell YOUR write from another
    session's. It reports what changed and says so rather than accusing.
  * If git itself cannot answer, it says MONITORING DEGRADED once rather than
    going quiet — a silent detector is indistinguishable from a clean tree,
    which is the one thing it must never be.

Scope: PostToolUse on Bash, the demonstrated hole. Broadening the matcher (e.g.
to MCP write tools, which the PreToolUse guard also cannot see) is a one-line
change in settings.json; it is not done here because Bash is the gap actually
observed, and a `git status` after every Read would be cost with no cause.

Baseline lives beside the solo marker in the PRIMARY git dir, keyed by session,
and is REFRESHED after each report so one drift is reported once rather than on
every subsequent command.

Solo mode (CIM_SOLO / .git/cim-solo) disables it: deliberate solo work dirties
the primary tree by design.

Pure-stdlib. Every path exits 0 — a detector must never fail a tool call.
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared_tree import (git_out, git_value, primary_git_dir,  # noqa: E402
                          primary_worktree, solo_mode)

#: Bumped when the baseline's shape changes. A baseline written by an older
#: version is discarded and re-armed rather than misread — a stale shape would
#: otherwise be compared field-by-field against a new one and report nonsense.
BASELINE_VERSION = 2

#: Stale baselines from finished sessions are swept on write. A week is long
#: enough that a paused session resuming still finds its own file.
BASELINE_TTL_SECONDS = 7 * 24 * 60 * 60
BASELINE_PREFIX = "cim-tree-baseline-"

#: A truncated listing is still an accurate alarm; an unbounded one could paste
#: a generated tree into the transcript.
MAX_REPORTED_PATHS = 20


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


def _signature(tree, path):
    """A cheap fingerprint of a dirty file: (size, mtime_ns).

    Tracking PATHS alone is not enough. Foreign dirty state is the normal
    condition of a shared tree, so a path is routinely already listed at
    baseline time — and then a genuine new write to that same path adds no new
    path and would go unreported. That is the exact situation this hook exists
    for, so it must compare something that CHANGES on a rewrite.

    stat is used rather than hashing contents because dirty sets are small but
    individual files need not be, and a rewrite that preserves both size and
    nanosecond mtime is not a thing an editor or a script does.
    """
    try:
        st = os.stat(os.path.join(tree, path))
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None                   # deleted, or unreadable — still a state


def snapshot(tree):
    """{entries, head, branch} for `tree`, or None if git can't answer."""
    status = git_out("-C", tree, "status", "--porcelain")
    head = git_value("-C", tree, "rev-parse", "HEAD")
    if status is None or head is None:
        return None
    entries = {}
    for line in status.splitlines():
        # `XY PATH` — two status columns then a space. The string is NOT
        # pre-stripped; see _shared_tree.git_out for why that matters.
        if len(line) <= 3:
            continue
        path = line[3:].strip()
        # Rename/copy entries read `R  old -> new`; the destination is the
        # written path, so keep that side.
        path = path.split(" -> ")[-1].strip().strip('"')
        entries[path] = _signature(tree, path)
    branch = git_value("-C", tree, "branch", "--show-current")
    return {"version": BASELINE_VERSION, "entries": entries, "head": head,
            "branch": branch if branch is not None else "",
            "degraded_reported": False}


def baseline_path(git_dir, session_id):
    if not isinstance(session_id, str) or not session_id:
        # Any non-string the payload might carry must not reach .encode();
        # this hook's one absolute invariant is that it never fails a tool
        # call, and `cwd` is type-checked for the same reason.
        session_id = "nosession"
    key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return os.path.join(git_dir, BASELINE_PREFIX + key)


def sweep_stale(git_dir):
    """Best-effort removal of baselines from long-finished sessions, so the git
    dir does not accumulate one file per session forever."""
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
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        return None                   # absent, corrupt, or an older shape
    return data


def store(path, snap):
    """Write atomically — a truncated file read by the next call would look
    like a corrupt baseline and re-arm a false alarm."""
    try:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _listing(paths):
    shown = paths[:MAX_REPORTED_PATHS]
    more = len(paths) - len(shown)
    out = "\n".join(f"    {p}" for p in shown)
    if more > 0:
        out += f"\n    … and {more} more"
    return out


FOOTER = (
    "\n\nThis hook watches the shared primary tree because the PreToolUse guard "
    "cannot see Bash file writes (redirects, sed -i, interpreters fed by a "
    "heredoc, and anything a script does).\n"
    "  • If your last command did this — it was most likely a relative path "
    "resolving against the primary tree instead of your worktree, since `cd` "
    "does not persist between calls. Undo it (`git -C <primary> checkout -- "
    "<path>`) and re-run with ABSOLUTE paths or `git -C <worktree>`.\n"
    "  • If it was not you, a concurrent session is working in the primary "
    "tree. Leave it alone — never add, commit over, or revert another "
    "session's files.\n"
    "This hook cannot tell those two apart; it reports what changed. It also "
    "cannot see gitignored paths at all.\n"
    "Reported once — the baseline is now refreshed to this state."
)


def build_report(tree, before, after):
    """The findings, or '' when nothing worth saying changed."""
    parts = []
    before_entries = before.get("entries") or {}
    after_entries = after["entries"]

    added = sorted(set(after_entries) - set(before_entries))
    # A path dirty in BOTH snapshots whose fingerprint moved was written again.
    # Without this the common case — foreign dirt already on the path you then
    # write to — reports nothing at all.
    rewritten = sorted(p for p in set(after_entries) & set(before_entries)
                       if after_entries[p] != before_entries[p])

    if added:
        parts.append("Files newly changed in the PRIMARY working tree:\n"
                     + _listing(added))
    if rewritten:
        parts.append("Files written again in the PRIMARY working tree (already "
                     "dirty before, changed since):\n" + _listing(rewritten))
    if before.get("head") and after["head"] != before["head"]:
        parts.append(f"The primary tree's HEAD moved: {before['head'][:12]} → "
                     f"{after['head'][:12]}.")
    if before.get("branch") != after["branch"]:
        parts.append(f"The primary tree's branch changed: "
                     f"'{before.get('branch') or '(detached)'}' → "
                     f"'{after['branch'] or '(detached)'}'.")

    if not parts:
        return ""
    return (f"PRIMARY-TREE DRIFT DETECTED at {tree}\n\n"
            + "\n\n".join(parts) + FOOTER)


DEGRADED = (
    "PRIMARY-TREE MONITORING DEGRADED\n\n"
    "The hook that watches the shared primary working tree could not read it "
    "({why}). It is NOT currently able to tell you if a command writes there, "
    "and silence from it no longer means the tree is clean.\n"
    "Check the tree by hand (`git -C <primary> status`) before trusting that "
    "nothing has drifted. Reported once per session."
)


def degraded(path, why, snap=None):
    """Say so, once. A detector that goes quiet on failure is indistinguishable
    from one reporting a clean tree, which is the single thing it must never
    be — but repeating it after every command would be noise, so the flag is
    persisted."""
    state = snap or {"version": BASELINE_VERSION, "entries": {}, "head": "",
                     "branch": "", "degraded_reported": True}
    state["degraded_reported"] = True
    store(path, state)
    report(DEGRADED.format(why=why))


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

    # Checked AFTER resolving the git dir, so the marker is read from the same
    # clone the guard reads it from.
    if solo_mode(git_dir):
        quiet()

    path = baseline_path(git_dir, data.get("session_id"))
    before = load(path)

    tree = primary_worktree(git_dir)
    if not tree:
        if not (before or {}).get("degraded_reported"):
            degraded(path, "its checkout root could not be resolved", before)
        quiet()

    after = snapshot(tree)
    if after is None:
        if not (before or {}).get("degraded_reported"):
            degraded(path, "git could not report its status", before)
        quiet()

    if before is None:
        # First call of the session, or a baseline from an older format:
        # establish it and stay silent. There is nothing to compare against
        # yet, and inventing an alarm from a missing baseline would train the
        # operator to ignore this hook.
        sweep_stale(git_dir)
        store(path, after)
        quiet()

    context = build_report(tree, before, after)
    # Refresh either way: a path returning to clean must not leave the baseline
    # claiming it is still dirty, or the next real write to it is missed.
    store(path, after)
    if not context:
        quiet()
    report(context)


if __name__ == "__main__":
    main()
