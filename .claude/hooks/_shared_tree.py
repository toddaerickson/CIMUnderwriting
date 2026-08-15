"""Shared primitives for the worktree hooks — ONE definition of four questions.

`guard-shared-worktree.py` (PreToolUse, denies) and
`detect-primary-tree-writes.py` (PostToolUse, reports) must agree on:

  1. which git dir is the PRIMARY tree of this clone, and
  2. whether solo mode is on.

`precompact-carryover.py` (PreCompact, saves) and
`session-start-carryover.py` (SessionStart, restores) must agree on:

  3. where the carry-over snapshot for a session is written, and
  4. where the durable, agent-maintained notes file lives.

The second pair is the same class of coupling as the first, for the same
reason: a writer and a reader that disagree about a path do not fail loudly,
they simply preserve nothing — and a context-preservation mechanism that
silently preserves nothing is worse than none, because the operator stops
checking.

They agreed by hand-copy until this module existed. That is the divergence the
project's single-source-of-truth rule exists to prevent, and this particular
resolution logic is the wrong thing to fork: it did not survive the guard's
first two versions (a cruder `"/worktrees/" in gitdir` substring test), and the
regex form below only arrived in the v3 adversarial hardening round. A future
fix for a git-version quirk or a nested-worktree layout applied to one copy and
not the other would leave the guard and the detector protecting different trees
— with nothing to notice.

Imported by both hooks. Python puts a script's own directory on `sys.path`, so
`import _shared_tree` resolves for either of them however they are invoked.

Pure stdlib, no side effects at import.
"""
import hashlib
import os
import re
import subprocess
import time

#: Both escape hatches, in one place. The env var is per-session (launch with
#: it set); the marker file is per-clone and must be removed immediately after,
#: since leaving it disables the hooks for every future session.
#:
#: The marker belongs in the PRIMARY git dir, which is the only place
#: `solo_mode` below looks — `touch "$(git -C <primary> rev-parse
#: --absolute-git-dir)/cim-solo"`. Both halves are load-bearing and the shorter
#: spelling silently fails: `--git-dir` prints a relative `.git`, which inside a
#: linked worktree is a FILE (so `touch` dies with ENOTDIR), and dropping `-C`
#: resolves to the worktree's own git dir, creating a marker nothing reads.
SOLO_ENV = "CIM_SOLO"
SOLO_MARKER = "cim-solo"

#: Carry-over across a context reset. Two files, with different lifetimes and
#: different authors — the distinction is the whole design:
#:
#:   NOTES  is written by the AGENT, by hand, during the session. One per
#:          clone. Hooks never write it. It holds what no snapshot can
#:          derive — which phase of a plan is in flight, what was decided
#:          and why, what to do next. This is the part that actually
#:          survives, because it is the part a machine cannot reconstruct.
#:   SNAP   is written by the PreCompact hook. One per session. It holds
#:          only what IS derivable — branch, dirty paths, recent commits,
#:          worktrees — so the notes never have to restate git state that
#:          would be stale by the time it was read.
#:
#: The two names must NOT share a prefix: the snapshot sweep and the
#: newest-snapshot scan both match on `CARRYOVER_PREFIX`, and a notes file
#: caught by that glob would be returned as a snapshot and eventually
#: deleted as a stale one. `test_the_notes_file_is_not_matched_as_a_snapshot`
#: pins the separation.
CARRYOVER_NOTES = "cim-session-notes.md"
CARRYOVER_PREFIX = "cim-carryover-"

#: Snapshots from finished sessions are swept on write, matching the
#: detector's baseline TTL. A paused session resuming still finds its own.
CARRYOVER_TTL_SECONDS = 7 * 24 * 60 * 60

#: How far back `latest_carryover` will reach when the session id does not
#: match. `/clear` may mint a NEW session id, which would orphan a snapshot
#: keyed by the old one — so the reader falls back to the most recent
#: snapshot from this clone. Bounded at two hours because a snapshot older
#: than that is likely from a different piece of work, and restoring the
#: wrong context is worse than restoring none: it reads as authoritative.
CARRYOVER_FALLBACK_SECONDS = 2 * 60 * 60

#: A linked worktree's git dir is `<shared>/.git/worktrees/<name>`; stripping
#: that suffix maps it back to the shared `.git`, which IS the primary tree's.
_WORKTREE_GITDIR = re.compile(r"^(.*)/worktrees/[^/]+/?$")


def git_out(*args, timeout=5):
    """Read-only git. RAW stdout, or None when the command failed.

    Raw on purpose — `status --porcelain` is columnar and its leading space is
    load-bearing. Callers wanting a single value use `git_value`.
    """
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=timeout)
    except Exception:
        return None
    return p.stdout if p.returncode == 0 else None


def git_value(*args, **kw):
    """A single-line git read (HEAD, branch, a config key), stripped."""
    out = git_out(*args, **kw)
    return None if out is None else out.strip()


def primary_git_dir(path):
    """Absolute git dir of the PRIMARY tree of `path`'s clone, or ''.

    Called from inside a linked worktree this still returns the SHARED git dir
    — which is the point: a session working in its own worktree is exactly the
    session that needs the primary tree watched.
    """
    gd = git_value("-C", path or ".", "rev-parse", "--absolute-git-dir")
    if not gd:
        return ""
    m = _WORKTREE_GITDIR.match(gd)
    return os.path.abspath(m.group(1) if m else gd)


def primary_worktree(git_dir):
    """The MAIN checkout root belonging to `git_dir`, or '' if unresolvable.

    Asking git is the point. `git worktree list` names the main worktree first
    by definition, which is the answer wanted here even when the caller is
    sitting in a linked one. Two degenerate replies are rejected rather than
    returned: a `bare` repository has no checkout at all, and a git dir with no
    work tree reports ITSELF, which would send every later `git -C` at the git
    dir instead of a tree.

    `core.worktree` is the fallback, since it is the only recorded back-pointer
    in a submodule or a manually-configured split layout. The conventional
    `<root>/.git` parent comes last, and is VERIFIED to be a work tree rather
    than returned on faith.

    When nothing resolves, this returns '' — deliberately. `git init
    --separate-git-dir` records no back-pointer at all, so from the git dir
    alone the checkout genuinely cannot be found. The caller must report that
    it has gone blind; a plausible-looking guess would instead produce
    confident silence about the wrong directory, which is indistinguishable
    from a clean tree.
    """
    if not git_dir:
        return ""
    listing = git_out("--git-dir", git_dir, "worktree", "list", "--porcelain")
    if listing:
        first = listing.split("\n\n", 1)[0].splitlines()
        entry = next((l[len("worktree "):] for l in first
                      if l.startswith("worktree ")), "")
        bare = any(l.strip() == "bare" for l in first)
        p = os.path.abspath(entry) if entry else ""
        if p and not bare and p != os.path.abspath(git_dir) and os.path.isdir(p):
            return p
    configured = git_value("--git-dir", git_dir, "config", "--get",
                           "core.worktree")
    if configured:
        p = configured if os.path.isabs(configured) else os.path.join(
            git_dir, configured)
        p = os.path.abspath(p)
        if os.path.isdir(p):
            return p
    parent = os.path.dirname(git_dir)
    if parent and os.path.isdir(parent):
        top = git_value("-C", parent, "rev-parse", "--show-toplevel")
        if top and os.path.isdir(top):
            return top
    return ""


def solo_mode(git_dir):
    """True when the operator has deliberately opted out of the worktree rules.

    Both hooks must honour BOTH switches or the escape hatch is only half an
    escape — someone who set the env var would still be denied by one hook, or
    still be reported by the other.
    """
    if os.environ.get(SOLO_ENV):
        return True
    return bool(git_dir) and os.path.exists(os.path.join(git_dir, SOLO_MARKER))


def notes_path(git_dir):
    """The durable, agent-maintained notes file for this clone, or ''.

    One per clone rather than one per session, deliberately. Concurrent
    sessions sharing it is a feature — it becomes the shared task board the
    parallel-session rules otherwise lack — and keying it by session would
    lose it at exactly the moment it is needed, since `/clear` can mint a
    new session id.
    """
    return os.path.join(git_dir, CARRYOVER_NOTES) if git_dir else ""


def carryover_path(git_dir, session_id):
    """Where THIS session's snapshot lives.

    Keyed the same way the detector keys its baseline, for the same reason:
    a raw session id is not a safe filename, and a non-string one must never
    reach `.encode()` — these hooks' one invariant is that they never fail.
    """
    if not isinstance(session_id, str) or not session_id:
        session_id = "nosession"
    key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return os.path.join(git_dir, CARRYOVER_PREFIX + key)


def latest_carryover(git_dir, max_age=CARRYOVER_FALLBACK_SECONDS):
    """Newest snapshot in `git_dir` written within `max_age`, or ''.

    The fallback for the case the session-keyed lookup cannot cover: a
    `/clear` that starts a new session id leaves the previous session's
    snapshot on disk under a key the reader will never guess. Reaching for
    the newest recent one recovers it.

    Bounded, and it returns '' rather than the oldest match it can find —
    restoring stale context silently is the failure mode worth avoiding,
    because re-injected text reads as current by construction.
    """
    if not git_dir:
        return ""
    try:
        now = time.time()
        best, best_mtime = "", 0.0
        for name in os.listdir(git_dir):
            if not name.startswith(CARRYOVER_PREFIX):
                continue
            p = os.path.join(git_dir, name)
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            if now - mtime <= max_age and mtime > best_mtime:
                best, best_mtime = p, mtime
        return best
    except Exception:
        return ""


def sweep_carryover(git_dir):
    """Best-effort removal of snapshots from long-finished sessions."""
    try:
        now = time.time()
        for name in os.listdir(git_dir):
            if not name.startswith(CARRYOVER_PREFIX):
                continue
            p = os.path.join(git_dir, name)
            if now - os.path.getmtime(p) > CARRYOVER_TTL_SECONDS:
                os.unlink(p)
    except Exception:
        pass
