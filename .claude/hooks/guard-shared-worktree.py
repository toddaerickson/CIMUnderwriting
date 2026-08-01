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

Design (v3 — hardened over two adversarial-review rounds of the naive substring/cwd
version):
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
    asked whether the token was a rename. Only the guarded tree pays for the
    lookup; a `!shell` alias can run anything, so it fails closed.
  * Scoped to THIS clone via $CLAUDE_PROJECT_DIR — other repos are never guarded.
  * A git mutation whose target can't be resolved (unexpanded $VAR / missing dir)
    FAILS CLOSED (deny).

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
    if "--help" in args or "-h" in args:
        return False
    if any(a in ("--soft", "--mixed", "--hard", "--merge", "--keep") for a in args):
        return True                                    # explicit mode = ref/tree move
    if "--" in args:
        return False                                   # path-scoped unstage
    return bool([a for a in args if not a.startswith("-")])  # 'reset <commit>' ref move; bare=unstage


def _restore_mut(args):
    if "--help" in args or "-h" in args:
        return False
    staged = "--staged" in args or "-S" in args
    worktree = "--worktree" in args or "-W" in args
    return worktree or not staged                      # --staged-only = index unstage (safe)


def _branch_mut(args):
    return any(a in ("-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force")
               for a in args)                          # delete/rename/force (not list/create)


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
    if "--help" in args or "-h" in args:
        return False
    return "-o" in args or any(a.startswith("--output") for a in args)


def _symbolic_ref_mut(args):
    # `symbolic-ref HEAD refs/heads/x` repoints the checked-out branch without
    # touching one file — the collision this guard exists for, invisible to
    # `git status`. One positional reads; two write.
    if "--help" in args or "-h" in args:
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
    if "--help" in args or "-h" in args:
        return False
    if any(a in ("--unset", "--unset-all", "--add", "--replace-all",
                 "--edit", "-e") for a in args):
        return True
    if any(a in ("--get", "--get-all", "--get-regexp", "--get-urlmatch",
                 "--list", "-l") for a in args):
        return False
    return len(_positional(args)) >= 2             # `config <key> <value>`


def _mv_mut(args):
    if "--help" in args or "-h" in args:
        return False
    return not _dry_run(args)


def _rm_mut(args):
    # --cached unstages but leaves the file on disk — index-only, like `restore --staged`
    return _mv_mut(args) and "--cached" not in args


def _bisect_mut(args):
    if "--help" in args or "-h" in args:
        return False
    if not args:
        return False                                   # bare `git bisect` prints status
    return args[0] not in ("log", "view", "visualize", "terms", "help")


def _second_word_mut(args, readonly):
    """Deny a two-level subcommand unless its verb is in `readonly`."""
    if "--help" in args or "-h" in args:
        return False
    return not (args and args[0] in readonly)


ALIAS_SHELL = "\x00ALIAS_SHELL"


def _resolve_alias(sub, args, target, depth=3):
    """Follow `alias.<sub>` before classifying, so a rename cannot launder a
    mutation.

    `co = checkout` is an ordinary convenience alias, not an attack — and it
    disabled every rule in this file, because classification stopped at the
    literal token and never asked whether the token was a rename. Bounded
    depth, and a `!shell` alias can run anything so it fails closed.
    """
    seen = set()
    while target and sub and sub not in seen and depth > 0:
        seen.add(sub)
        expansion = git("-C", target, "config", "--get", f"alias.{sub}")
        if not expansion:
            break
        if expansion.startswith("!"):
            return ALIAS_SHELL, args
        parts = expansion.split()
        if not parts:
            break
        sub, args = parts[0], parts[1:] + args
        depth -= 1
    return sub, args


def _is_mutation(sub, args):
    if sub == ALIAS_SHELL:
        return True                                    # `!cmd` alias runs anything
    if sub in ("commit", "merge", "rebase", "cherry-pick", "am", "revert", "apply",
               "update-ref", "update-index", "gc"):
        return True
    if sub in ("checkout", "switch", "pull"):
        return not ("--help" in args or "-h" in args)  # tree/ref ops; pull = fetch + merge/rebase
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
        return bool(args) and args[0] in ("remove", "prune", "move")
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
        return not ("--help" in args or "-h" in args)
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
        if guarded:
            sub, subargs = _resolve_alias(sub, subargs, target)
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