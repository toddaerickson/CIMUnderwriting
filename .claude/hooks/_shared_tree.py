"""Shared primitives for the worktree hooks — ONE definition of two questions.

`guard-shared-worktree.py` (PreToolUse, denies) and
`detect-primary-tree-writes.py` (PostToolUse, reports) must agree on:

  1. which git dir is the PRIMARY tree of this clone, and
  2. whether solo mode is on.

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
import os
import re
import subprocess

#: Both escape hatches, in one place. The env var is per-session (launch with
#: it set); the marker file is per-clone (`touch "$(git rev-parse
#: --git-dir)/cim-solo"`) and must be removed immediately after, since leaving
#: it disables the hooks for every future session.
SOLO_ENV = "CIM_SOLO"
SOLO_MARKER = "cim-solo"

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
