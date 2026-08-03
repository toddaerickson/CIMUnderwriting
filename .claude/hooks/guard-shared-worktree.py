#!/usr/bin/env python3
"""PreToolUse guard: REQUIRE an isolated git worktree for mutations in the PRIMARY
working tree of THIS project's shared clone.

Multiple concurrent Claude sessions share this one clone. A session that mutates the
PRIMARY working tree — edits a repo file, or runs a git branch/commit/reset/checkout/
pull there — can collide with another session that switches the primary clone's branch
out from under it (the failure this guard exists to prevent; see CLAUDE.md rule
"Simultaneous sessions").

This guard DENIES file mutations (Edit/Write/MultiEdit/NotebookEdit) and git-mutating
Bash commands that would land in the PRIMARY working tree of THIS clone, and tells you
to isolate:

    git worktree add .claude/worktrees/<slug> -b <branch> origin/main

Mutations inside a linked worktree of this clone are ALLOWED. Mutations to OTHER repos,
reads, non-git Bash, read-only git, and edits outside any repo are ALLOWED — the guard
only protects this clone's primary tree.

Design (v6 — hardened over seven adversarial-review rounds of the naive substring/cwd
version; rounds 2-5 each found real holes in what the previous round called done):
  * Heredoc bodies (`… <<EOF … EOF`) are stripped BEFORE parsing, so command examples
    inside a script/PR body written via `cat <<EOF` aren't misread as real commands.
  * Bash is split into segments; cwd is tracked with a proper `cd`/`pushd`/`popd`
    STACK, so `pushd <wt> && … && popd && git reset` is judged against the restored
    primary cwd, and `cd <primary> && git reset` against the primary tree.
  * Only a segment whose actual COMMAND WORD is `git` is treated as git — a body that
    merely QUOTES "git checkout" (`gh pr create --body "…git checkout…"`) is ignored.
  * Each git segment is judged against ITS OWN `git -C` target (all leading globals,
    incl. `-c k=v`, parsed) else the tracked cwd; a read-only `git -C <wt>` decoy can't
    launder a later primary mutation, and `-c k=v -C <primary>` can't hide the target.
  * A working-tree restore (`checkout … -- <file>`, `restore <file>`) is treated as a
    mutation — it overwrites shared-tree file content. Only index-only ops
    (`restore --staged`, bare `reset`, `reset -- <path>`, `rm --cached`) are allowed.
  * The subcommand list is by WRITE EFFECT, not by familiarity. `pull` shipped
    unguarded because only its halves (`merge`, `rebase`) were listed while the
    compound name was not — it rewrites the tree just the same. `rm`/`mv`/`bisect`
    were the same oversight: each writes the tree without naming a listed subcommand.
    Two further adversarial sweeps each caught what the previous one missed, and
    that repetition is the point — the audit, not the list, is the fragile part.
    Round 2: `sparse-checkout set` DELETES directories from disk,
    `checkout-index -a -f` DISCARDS uncommitted content, and `submodule update`/
    `read-tree -u`/`merge-file`/`filter-branch` all write tracked files.
    Round 3, the subtler shape — commands that write without looking like it:
    `archive -o <tracked>` overwrites a file with tar bytes, `symbolic-ref HEAD
    <ref>` MOVES THE CHECKED-OUT BRANCH while `git status` stays clean, and
    `config core.worktree`/`core.hooksPath` corrupt the shared clone for every
    later session. Enumerate by asking "does this touch the tree or the branch",
    never by recognising the name.
    `fetch` stays ALLOWED — it moves remote-tracking refs only, never the working
    tree, and it is how a session syncs without touching the shared checkout.
  * Aliases are resolved BEFORE classification. `co = checkout` is an ordinary
    convenience alias, not an attack, and it disabled every rule in this file
    at once, because classification stopped at the literal token and never
    asked whether the token was a rename. The lookup runs for the guarded tree
    AND for an unresolved target — resolving only the former left
    `cd $UNSET && git co main` walking through the fail-closed path, which is
    the one path that most needed it. Every exit that cannot see through an
    alias fails closed (`!shell`, a globals-only expansion, a chain deeper
    than 10); a cycle need not, since git itself refuses to run one.
  * `--help` exempts only as the LEADING argument. `git commit -m --help`
    COMMITS, with "--help" as the message — verified against real git — so
    scanning the whole arg list for it exempts a real mutation.
  * v5 tried to narrow `worktree remove`/`prune` and `branch -d`/`-D`, on the
    argument that git refuses each on the shapes that hurt. v6 keeps HALF of
    that and reverts the rest, because an adversarial pass reproduced two
    losses in what v5 had just allowed:
      - `branch -D` bypasses the MERGE check, not merely the checked-out
        check v5 leaned on. `worktree remove` a clean tree, then `branch -D`
        its branch, and committed unpushed work is unreachable — the exact
        durability CLAUDE.md rule 1 promises. `-D` is the branch equivalent
        of `worktree remove --force`; both stay denied. `-d` is allowed, as
        git refuses it on an unmerged branch.
      - `worktree prune` treats "directory not reachable right now" as
        "already gone", and permanently broke a live worktree whose path was
        temporarily moved. Denied again.
    `worktree remove` (unforced) stays ALLOWED — git will not run it on the
    main tree at all — but the comment no longer claims uncommitted work is
    safe, because git's dirty check does not see GITIGNORED files and the
    plain form deletes them.
  * Exact-token flag matching missed BUNDLED short options for seven rounds:
    `git branch -fm src dst` force-renames, and `a in ("-m", "-f", …)` never
    matched it, while the unbundled spelling denied correctly. `_short_flags`
    now decomposes bundles. The lesson generalises past this one function —
    `_dry_run` had always done it right, and nobody carried it across.
  * Scoped to THIS clone via $CLAUDE_PROJECT_DIR — other repos are never guarded.
  * A git mutation whose target can't be resolved (unexpanded $VAR / missing dir)
    FAILS CLOSED (deny).

Scope, stated plainly because four adversarial rounds each found something the
previous one called finished: this classifies the GIT COMMAND WORD and its
arguments. It cannot see a shell redirect (`git archive HEAD > tracked.py`
writes through the shell, not through git), and it never sees a non-git writer
at all. Those are `detect-primary-tree-writes.py`'s job — it snapshots and
compares, so it covers vectors nobody enumerated. Treat this hook as the half
that PREVENTS what it recognises, not as a perimeter.

Not defended against (ADVERSARIAL, not the accidental collisions this targets):
`bash -c '…'`, `eval`, command substitution `$(cd … && git …)`, brace/subshell groups
that hide a `cd`, line-continuation splits, writing then running a script,
base64/obfuscation, and any non-Bash/Edit/Write/MultiEdit/NotebookEdit tool (e.g. an
MCP git tool). A well-meaning session doesn't do those; no shell-command guard can stop
a determined bypass — the robust half is the file-path check + the tracked-cwd/-C
judgement of ordinary `cd`/`pushd`/`env`/`&&`/`;`/`|` forms.

Escape hatches for DELIBERATE solo work on the primary clone:
  * env  CIM_SOLO=1   (per-session — launch the session with it set), or
  * file .git/cim-solo (per-clone — `touch "$(git rev-parse --git-dir)/cim-solo"`).

Pure-stdlib. Every non-firing path exits 0. Wired in .claude/settings.json as a
PreToolUse hook on Edit|Write|MultiEdit|NotebookEdit and on Bash.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared_tree import primary_git_dir, solo_mode   # noqa: E402

FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SEG_SPLIT = re.compile(r"&&|\|\||;|\||\n|\(|\)")
HEREDOC = re.compile(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")
GLOBAL_TAKES_ARG = ("-C", "-c", "--git-dir", "--work-tree", "--namespace",
                    "--exec-path", "--super-prefix")


def git(*args):
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


def allow():
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def nearest_existing_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    return d or "."


def _resolve_dir(tok, base):
    """Resolve a cd/env/-C target against `base`. Absolute existing dir, or None if
    unresolvable (shell var, ~, missing dir) — callers fail closed."""
    if not tok or tok[0] in "$`~" or "$" in tok or "`" in tok:
        return None
    b = base if (base and os.path.isdir(base)) else None
    p = tok if os.path.isabs(tok) else (os.path.join(b, tok) if b else None)
    if p is None:
        return None
    p = os.path.abspath(p)
    return p if os.path.isdir(p) else None


# Primary-clone resolution and the solo-mode switches now live in
# _shared_tree.py, imported above. They were hand-copied into the PostToolUse
# detector, which is the divergence the single-source-of-truth rule exists to
# prevent — and this logic in particular did not survive v1 or v2 of this
# guard, so it is the wrong thing to keep two copies of. Behaviour here is
# unchanged; tests/test_hook_shared_tree.py pins both callers to one answer.


def _target_guarded(target, project_clone):
    if target is None:
        return False
    gd = git("-C", target, "rev-parse", "--absolute-git-dir")
    if not gd:
        return False                                   # outside any git repo
    if "/worktrees/" in gd:
        return False                                   # a linked worktree
    if project_clone and os.path.abspath(gd) != project_clone:
        return False                                   # a different clone
    if solo_mode(os.path.abspath(gd)):
        return False                                   # solo opt-out
    return True


def _strip_heredocs(cmd):
    """Drop heredoc BODY + closing delimiter lines, keeping the line bearing `<<WORD`
    (which holds the real command). Prevents body text like `git reset` from being
    parsed as a command."""
    if "<<" not in cmd:
        return cmd
    lines = cmd.split("\n")
    out, i = [], 0
    while i < len(lines):
        out.append(lines[i])
        m = HEREDOC.search(lines[i])
        i += 1
        if m:
            delim = m.group(1)
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1                                     # skip the closing delimiter line
    return "\n".join(out)


# ---- git mutation classification (subcommand + its args) ----

def _split_git(gitargs):
    """Skip git global options -> (subcommand, subargs)."""
    i = 0
    while i < len(gitargs):
        t = gitargs[i]
        if t in GLOBAL_TAKES_ARG:
            i += 2
            continue
        if t.startswith("--") and "=" in t:
            i += 1
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t, gitargs[i + 1:]
    return "", []


def _reset_mut(args):
    if _helpish(args):
        return False
    if any(a in ("--soft", "--mixed", "--hard", "--merge", "--keep") for a in args):
        return True                                    # explicit mode = ref/tree move
    if "--" in args:
        return False                                   # path-scoped unstage
    return bool([a for a in args if not a.startswith("-")])  # 'reset <commit>' ref move; bare=unstage


def _restore_mut(args):
    if _helpish(args):
        return False
    staged = "--staged" in args or "-S" in args
    worktree = "--worktree" in args or "-W" in args
    return worktree or not staged                      # --staged-only = index unstage (safe)


def _short_flags(args):
    """Every letter across short-option bundles, plus the long flags seen.

    Git accepts bundled single-letter options: `-fm` IS `-f -m`. Exact-token
    membership (`a in ("-m", "-f", …)`) never sees that, which let
    `git branch -fm src dst` — a FORCE RENAME — through this classifier while
    the unbundled spelling was correctly denied. Found by adversarial review
    of v5; the same bug was present in v4 and every round before it, and the
    file already avoided it in `_dry_run` without the lesson being carried
    across.
    """
    short, longs = set(), set()
    for a in args:
        if a.startswith("--"):
            longs.add(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            short.update(a[1:])
    return short, longs


def _branch_mut(args):
    """Ref admin, judged by whether it can DESTROY something durable.

    `-d` is ALLOWED: git refuses it on a branch that is not fully merged
    (`error: the branch 'x' is not fully merged`) and on one checked out in
    any worktree, so it can only drop a ref whose commits already live
    somewhere else. It writes no tracked file and moves no HEAD.

    `-D` STAYS DENIED, reversing this item's first draft. The draft argued
    that git refuses to delete a checked-out branch, which is true and is
    the wrong protection to lean on: `-D` exists to bypass the MERGE check.
    Adversarial review reproduced the loss in two commands this same change
    had newly allowed — `worktree remove` a clean worktree, then `branch -D`
    its branch — and the committed, unpushed work became unreachable. That
    is precisely the guarantee CLAUDE.md rule 1 makes ("branch refs are
    durable even if the worktree dir is lost"), so `-D` is the branch
    equivalent of `worktree remove --force` and is denied for the same
    reason. A stale local ref costs nothing; deliberate pruning is what
    CIM_SOLO=1 is for.

    Renames, force-updates, force-copies and the upstream/description forms
    stay denied: each repoints a ref or persists in .git/config for every
    later session.
    """
    if _helpish(args):
        return False
    short, longs = _short_flags(args)

    # Rename, force-copy over an existing ref, or write branch.* into the
    # shared .git/config. `u` is in the short set because the rewrite to
    # `_short_flags` dropped it and only kept `--set-upstream-to` — caught
    # on re-review, and exactly the "carried the long form, forgot the
    # short one" slip that `_short_flags` exists to make impossible.
    if short & set("mMCu") or longs & {"--move", "--copy", "--unset-upstream",
                                       "--edit-description", "--set-upstream-to"}:
        return True
    forced = bool(short & set("fD")) or "--force" in longs
    deleting = bool(short & set("dD")) or "--delete" in longs
    if deleting:
        return forced          # -d allowed; -D / --delete --force denied
    return forced              # bare -f force-updates a ref; listing/creating is fine


def _worktree_mut(args):
    """Worktree admin, judged by whether it can reach the primary tree.

    `remove` (unforced) is ALLOWED. Git refuses it on the main worktree
    outright (`fatal: '<path>' is a main working tree`), so it cannot touch
    the tree this hook protects, and it refuses a worktree with modified or
    untracked files, so a concurrent session's VISIBLE work-in-progress is
    safe from the plain form. This is the post-merge cleanup CLAUDE.md rule
    3 names, and denying it bought nothing — "isolate into a worktree" is
    not advice you can follow when the command removes one.

    **The limit of that, stated because the first draft overclaimed it:**
    git's dirty check only sees what `git status` sees. A worktree whose
    only uncommitted content is GITIGNORED — an `.env`, build output, and in
    this repo a `*.xlsm` UW template — reports clean, and plain `remove`
    deletes it silently. So this allows a command that can destroy ignored
    files in someone else's clean worktree. That is a real cost, accepted
    because the branch ref and every tracked file survive, and because the
    alternative blocks routine cleanup outright. Do not restate the earlier
    claim that uncommitted work is safe here; it is not, for ignored paths.

    `prune` STAYS DENIED. It looks like bookkeeping, but it decides
    "already gone" by whether the directory is reachable RIGHT NOW —
    adversarial review broke a live worktree permanently by pruning while
    its directory was temporarily moved, which on a WSL or network mount is
    not a hypothetical. It was never needed for the cleanup this item is
    about.

    `--force`, `move` and `repair` stay denied: the first deletes another
    session's uncommitted work, the other two rewrite shared .git metadata.
    """
    if _helpish(args) or not args:
        return False
    sub = args[0]
    if sub == "remove":
        short, longs = _short_flags(args[1:])
        return "f" in short or "--force" in longs
    return sub in ("prune", "move", "repair")


def _helpish(args):
    """Only a LEADING `--help` is help.

    Verified against real git: `commit --allow-empty -m --help` COMMITS, with
    the message set to the literal string "--help". So a positional scan of the
    arg list exempts a real mutation — it has to be the first argument.
    """
    return bool(args) and args[0] in ("--help", "-h")


def _dry_run(args):
    """`--dry-run`, `-n`, and git's bundled short forms (`rm -rn`, `mv -fn`).

    Bundles are scanned across SHORT flags only. A substring test over every
    arg — the shortcut `clean` can afford — would read the `n` in
    `rm --ignore-unmatch` as a dry run and wave a real deletion through.
    """
    return "--dry-run" in args or any(
        a.startswith("-") and not a.startswith("--") and "n" in a for a in args)


def _positional(args):
    return [a for a in args if not a.startswith("-")]


def _archive_mut(args):
    # Plain `git archive` streams to stdout; `-o FILE` writes, and FILE may be
    # a tracked path — `archive -o config.py HEAD` replaces it with tar bytes.
    if _helpish(args):
        return False
    return "-o" in args or any(a.startswith("--output") for a in args)


def _symbolic_ref_mut(args):
    # `symbolic-ref HEAD refs/heads/x` repoints the checked-out branch without
    # touching one file — the collision this guard exists for, invisible to
    # `git status`. One positional reads; two write.
    if _helpish(args):
        return False
    if "--delete" in args or "-d" in args:
        return True
    return len(_positional(args)) >= 2


def _config_mut(args):
    """Writes to the shared clone's config outlive any one command.

    `core.worktree` redirects what this .git calls its working tree and
    `core.hooksPath` makes every later commit run code from elsewhere — both
    persist for every session until someone notices. Reads stay allowed.
    """
    if _helpish(args):
        return False
    if any(a in ("--unset", "--unset-all", "--add", "--replace-all",
                 "--edit", "-e") for a in args):
        return True
    if any(a in ("--get", "--get-all", "--get-regexp", "--get-urlmatch",
                 "--list", "-l") for a in args):
        return False
    return len(_positional(args)) >= 2             # `config <key> <value>`


def _mv_mut(args):
    if _helpish(args):
        return False
    return not _dry_run(args)


def _rm_mut(args):
    # --cached unstages but leaves the file on disk — index-only, like `restore --staged`
    return _mv_mut(args) and "--cached" not in args


def _bisect_mut(args):
    if _helpish(args):
        return False
    if not args:
        return False                                   # bare `git bisect` prints status
    return args[0] not in ("log", "view", "visualize", "terms", "help")


def _second_word_mut(args, readonly):
    """Deny a two-level subcommand unless its verb is in `readonly`.

    Bare (`git remote`, `git submodule`) lists or prints usage, so it is a read.
    """
    if _helpish(args) or not args:
        return False
    return args[0] not in readonly


ALIAS_SHELL = "\x00ALIAS_SHELL"


def _resolve_alias(sub, args, probe, depth=10):
    """Follow `alias.<sub>` before classifying, so a rename cannot launder a
    mutation.

    `co = checkout` is an ordinary convenience alias, not an attack — and it
    disabled every rule in this file, because classification stopped at the
    literal token and never asked whether the token was a rename.

    Every exit that cannot see through the alias FAILS CLOSED, because the
    alternative is "too clever to classify" reading as "safe": a `!shell`
    expansion runs anything, an expansion of only global options
    (`-c user.name=x commit …`) leaves no subcommand to judge, and a chain
    deeper than we follow is one real git resolves anyway. A cycle is the one
    case that need not: git itself refuses it, so it never runs.
    """
    seen = set()
    while probe and sub and sub not in seen and depth > 0:
        seen.add(sub)
        expansion = git("-C", probe, "config", "--get", f"alias.{sub}")
        if not expansion:
            return sub, args
        if expansion.startswith("!"):
            return ALIAS_SHELL, args
        # Skip the expansion's own globals exactly as a real command's are.
        nxt, rest = _split_git(expansion.split())
        if not nxt:
            return ALIAS_SHELL, args
        sub, args = nxt, rest + args
        depth -= 1
    if depth <= 0:
        return ALIAS_SHELL, args
    return sub, args


def _is_mutation(sub, args):
    if sub == ALIAS_SHELL:
        return True                                    # `!cmd` alias runs anything
    if sub in ("commit", "merge", "rebase", "cherry-pick", "am", "revert", "apply",
               "update-ref", "update-index", "gc", "init"):
        # `init --template=<dir>` re-inits in place and COPIES HOOKS into
        # .git/hooks — which then fire on `worktree add`, the command every
        # deny message here tells the operator to run.
        return not _helpish(args)
    if sub in ("checkout", "switch", "pull"):
        return not _helpish(args)  # tree/ref ops; pull = fetch + merge/rebase
    if sub == "restore":
        return _restore_mut(args)
    if sub == "reset":
        return _reset_mut(args)
    if sub == "branch":
        return _branch_mut(args)
    if sub == "clean":
        return any(a.startswith("-") and "f" in a for a in args)
    if sub == "stash":
        return not (args and args[0] in ("list", "show"))
    if sub == "reflog":
        return bool(args) and args[0] in ("delete", "expire")
    if sub == "worktree":
        return _worktree_mut(args)
    if sub == "remote":
        # set-url/add/rename rewrite .git/config — where every session pushes.
        return _second_word_mut(args, ("show", "get-url", "-v", "--verbose"))
    if sub == "mv":
        return _mv_mut(args)
    if sub == "rm":
        return _rm_mut(args)
    if sub == "bisect":
        return _bisect_mut(args)
    if sub == "sparse-checkout":
        return _second_word_mut(args, ("list", "check-rules"))
    if sub == "submodule":
        return _second_word_mut(args, ("status", "summary"))
    if sub == "archive":
        return _archive_mut(args)
    if sub == "symbolic-ref":
        return _symbolic_ref_mut(args)
    if sub == "config":
        return _config_mut(args)
    # Plumbing that writes tracked files. None of it is run casually, so these
    # are denied outright rather than parsed for a working-tree flag: the
    # index-only carve-out exists for `reset`/`restore --staged`/`rm --cached`,
    # which people use daily, not for commands reached only on purpose.
    if sub in ("checkout-index", "read-tree", "merge-file", "filter-branch"):
        return not _helpish(args)
    return False


def _git_c(gitargs, base):
    """Resolve `git -C <path>` / `--git-dir=<path>` on this invocation, correctly
    skipping value-consuming globals like `-c k=v`. '' if none, None if unresolvable."""
    i = 0
    while i < len(gitargs):
        t = gitargs[i]
        if t == "-C" and i + 1 < len(gitargs):
            return _resolve_dir(gitargs[i + 1].strip("\"'"), base)
        if t.startswith("--git-dir="):
            gd = t[len("--git-dir="):].strip("\"'")
            return _resolve_dir(os.path.dirname(gd) or gd, base)
        if t in GLOBAL_TAKES_ARG:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break                                          # reached the subcommand
    return ""


def _env_chdir(toks):
    """For a segment starting with `env`, return (cmd_word_index, chdir_target|'' ,
    unresolved). Handles env -C <p> / --chdir[=]<p> / VAR=val / flags."""
    i, target, un = 1, "", False
    while i < len(toks):
        t = toks[i]
        if t == "-C" and i + 1 < len(toks):
            d = _resolve_dir(toks[i + 1].strip("\"'"), None)
            target, un = (d or ""), (d is None)
            i += 2
            continue
        if t.startswith("--chdir="):
            d = _resolve_dir(t.split("=", 1)[1].strip("\"'"), None)
            target, un = (d or ""), (d is None)
            i += 1
            continue
        if t == "--chdir" and i + 1 < len(toks):
            d = _resolve_dir(toks[i + 1].strip("\"'"), None)
            target, un = (d or ""), (d is None)
            i += 2
            continue
        if t.startswith("-") or "=" in t:
            i += 1
            continue
        break
    return i, target, un


def _reason(target, sub=""):
    branch = git("-C", target, "branch", "--show-current") or "(detached HEAD)"
    # Syncing the primary tree is a legitimate errand a new worktree cannot run,
    # so `pull` gets pointed at the solo hatch instead of the generic isolate advice.
    if sub == "pull":
        return (
            f"BLOCKED: `git pull` rewrites the PRIMARY working tree of the shared clone "
            f"(branch '{branch}') — it is fetch + merge/rebase, and a concurrent session "
            f"reading this tree would have the files move under it (CLAUDE.md: Simultaneous "
            f"sessions). A worktree does not sync this tree, so isolating is not the fix "
            f"here. Either `git fetch origin --prune` (moves remote-tracking refs only, "
            f"never the working tree — new worktrees branch from origin/main anyway), or, "
            f"once no other session is live, re-launch with CIM_SOLO=1 and pull --ff-only."
        )
    if sub in ("worktree", "branch"):
        return (
            f"BLOCKED: this `git {sub}` form can destroy something durable in "
            f"the shared clone. Allowed instead: `worktree remove` (unforced) "
            f"and `branch -d`, which git itself refuses on the main tree, on a "
            f"worktree with visible changes, and on an unmerged or checked-out "
            f"branch. This form defeats one of those: `-D`/`--delete --force` "
            f"drops a branch git says is NOT merged — the ref is the durable "
            f"copy once a worktree is gone (CLAUDE.md rule 1) — `worktree "
            f"remove --force` deletes uncommitted work, `worktree prune` can "
            f"break a worktree whose path is only temporarily unreachable, and "
            f"`move`/`repair`/`branch -m`/`-f`/`--set-upstream-to` rewrite "
            f"shared .git state. A stale local ref is harmless; if you really "
            f"want it gone, confirm no other session owns it and re-launch "
            f"with CIM_SOLO=1."
        )
    return (
        f"BLOCKED: this mutation targets the PRIMARY working tree of the shared clone "
        f"(branch '{branch}'). Concurrent Claude sessions share it; mutating here risks a "
        f"branch-switch/reset collision (CLAUDE.md: Simultaneous sessions). Isolate first:\n"
        f"  git worktree add .claude/worktrees/<slug> -b <branch> origin/main\n"
        f"then work there (cd into it, or `git -C <path>`). Deliberately solo on the "
        f"primary clone? Re-launch with CIM_SOLO=1, or `touch \"$(git rev-parse --git-dir)/cim-solo\"`."
    )


UNRESOLVED = "\x00UNRESOLVED"


def _eval_bash(cmd, session_cwd, project_clone):
    """Deny reason, UNRESOLVED sentinel, or None (allow)."""
    cmd = _strip_heredocs(cmd)
    cwd = session_cwd if (session_cwd and os.path.isdir(session_cwd)) else "."
    stack = []
    for raw in SEG_SPLIT.split(cmd):
        toks = raw.split()
        if not toks:
            continue
        w = toks[0]
        if w == "cd":
            cwd = _resolve_dir(toks[1].strip("\"'"), cwd) if len(toks) > 1 else None
            continue
        if w == "pushd":
            stack.append(cwd)
            cwd = _resolve_dir(toks[1].strip("\"'"), cwd) if len(toks) > 1 else None
            continue
        if w == "popd":
            cwd = stack.pop() if stack else cwd
            continue
        seg_cwd, i = cwd, 0
        if w == "env":
            i, chd, un = _env_chdir(toks)
            seg_cwd = None if un else (chd or cwd)
        if i >= len(toks) or toks[i] != "git":
            continue                                   # command word isn't git
        gitargs = toks[i + 1:]
        sub, subargs = _split_git(gitargs)
        c = _git_c(gitargs, cwd)
        target = None if c is None else (c or seg_cwd)
        # Resolve the target BEFORE classifying: an alias renames the
        # subcommand, and reading `alias.<sub>` needs a repo to read it from.
        # Only the guarded tree pays for the lookup.
        guarded = _target_guarded(target, project_clone)
        if not _is_mutation(sub, subargs) and (guarded or target is None):
            # Pay for the alias lookup only where a verdict could still change:
            # the guarded tree, or an UNRESOLVED target. Probing `session_cwd`
            # when the target did not resolve is what keeps `cd $UNSET && git co`
            # from walking through the fail-closed path — reading an alias needs
            # SOME repo, and the one we are running in reads global config too.
            sub, subargs = _resolve_alias(sub, subargs, target or session_cwd)
        if not _is_mutation(sub, subargs):
            continue
        if target is None:
            return UNRESOLVED
        if guarded:
            return _reason(target, sub)
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        allow()
    if not isinstance(data, dict):
        allow()

    if os.environ.get("CIM_SOLO"):
        allow()

    tool = data.get("tool_name") or ""
    tin = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    session_cwd = data.get("cwd")
    session_cwd = session_cwd if isinstance(session_cwd, str) else "."
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or session_cwd
    project_clone = primary_git_dir(project_dir)

    if tool in FILE_TOOLS:
        fp = tin.get("file_path") or tin.get("notebook_path") or ""
        if not isinstance(fp, str) or not fp:
            allow()
        probe = nearest_existing_dir(fp)
        if _target_guarded(probe, project_clone):
            deny(_reason(probe))
        allow()

    if tool == "Bash":
        cmd = tin.get("command")
        if not isinstance(cmd, str) or not cmd:
            allow()
        verdict = _eval_bash(cmd, session_cwd, project_clone)
        if verdict is UNRESOLVED:
            deny(
                "BLOCKED: a git-mutating command relocates (cd/pushd/env) to a path this "
                "guard can't resolve, so it can't prove the target isn't the shared primary "
                "tree. Run it from inside the worktree, or use `git -C <literal worktree "
                "path>`. Deliberately solo? CIM_SOLO=1 or "
                "`touch \"$(git rev-parse --git-dir)/cim-solo\"`."
            )
        if verdict:
            deny(verdict)
        allow()

    allow()


if __name__ == "__main__":
    main()