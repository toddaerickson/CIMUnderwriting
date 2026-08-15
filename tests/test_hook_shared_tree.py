"""`.claude/hooks/_shared_tree.py` — the one definition both worktree hooks read.

Two jobs here.

**Cover the primitives**, which are not obvious five-liners: primary-clone
resolution did not survive the guard's first two versions and only reached its
current form in a v3 adversarial hardening round. It is now imported by two
hooks instead of hand-copied into both, which removes the divergence risk but
concentrates the blast radius — so it gets direct tests.

**Pin the guard**, which had none. `guard-shared-worktree.py` is the ENFORCEMENT
half of the parallel-session rules and was refactored onto this module; a
regression there fails open silently, so its core verdicts are asserted against
real repos rather than trusted to a careful read.
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, ".claude", "hooks")
GUARD = os.path.join(HOOKS, "guard-shared-worktree.py")


def _load_shared():
    spec = importlib.util.spec_from_file_location(
        "_shared_tree", os.path.join(HOOKS, "_shared_tree.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


shared = _load_shared()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "clone"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    (r / "config.py").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


# ── primary_git_dir ──────────────────────────────────────────────────

def test_primary_git_dir_of_a_plain_repo(repo):
    assert shared.primary_git_dir(str(repo)) == str(repo / ".git")


def test_a_worktree_resolves_back_to_the_shared_git_dir(repo, tmp_path):
    """The property the whole design rests on: a session inside its own
    worktree still watches — and is judged against — the PRIMARY tree."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    assert shared.primary_git_dir(str(wt)) == str(repo / ".git")


def test_outside_a_repo_it_returns_empty(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert shared.primary_git_dir(str(plain)) == ""


# ── primary_worktree ─────────────────────────────────────────────────

def test_primary_worktree_of_a_standard_layout(repo):
    assert shared.primary_worktree(str(repo / ".git")) == str(repo)


def test_a_relocated_git_dir_refuses_rather_than_guessing(tmp_path):
    """`git init --separate-git-dir` records NO back-pointer to its checkout —
    not core.worktree, and `worktree list` answers with the git dir itself. So
    the checkout genuinely cannot be found from the git dir alone.

    The review finding was that the hook went SILENT here. The fix is not a
    cleverer guess — a wrong path produces confident silence about the wrong
    directory, which is worse. It refuses, and the caller reports that it has
    gone blind (see the detector's MONITORING DEGRADED test)."""
    work = tmp_path / "work"
    elsewhere = tmp_path / "elsewhere.git"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main",
                    f"--separate-git-dir={elsewhere}", str(work)], check=True)
    assert os.path.dirname(str(elsewhere)) != str(work), (
        "the fixture must actually exercise the relocated case")

    assert shared.primary_worktree(str(elsewhere)) == ""


def test_a_manually_configured_core_worktree_is_honoured(tmp_path):
    """The split layout that DOES record a back-pointer (submodules, manual
    config) must resolve — the fallback chain exists for it."""
    work = tmp_path / "work2"
    elsewhere = tmp_path / "elsewhere2.git"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main",
                    f"--separate-git-dir={elsewhere}", str(work)], check=True)
    subprocess.run(["git", f"--git-dir={elsewhere}", "config",
                    "core.worktree", str(work)], check=True)

    assert shared.primary_worktree(str(elsewhere)) == str(work)


def test_an_unresolvable_git_dir_returns_empty_rather_than_a_guess(tmp_path):
    """It must say 'I don't know' — the caller reports degraded monitoring on
    an empty answer, which is the honest outcome. Returning a plausible guess
    would produce confident silence about the wrong directory."""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    assert shared.primary_worktree(str(bare)) == ""
    assert shared.primary_worktree("") == ""


# ── solo_mode ────────────────────────────────────────────────────────

def test_solo_mode_off_by_default(repo, monkeypatch):
    monkeypatch.delenv("CIM_SOLO", raising=False)
    assert shared.solo_mode(str(repo / ".git")) is False


def test_solo_mode_via_env(repo, monkeypatch):
    monkeypatch.setenv("CIM_SOLO", "1")
    assert shared.solo_mode(str(repo / ".git")) is True


def test_solo_mode_via_marker(repo, monkeypatch):
    monkeypatch.delenv("CIM_SOLO", raising=False)
    (repo / ".git" / "cim-solo").write_text("")
    assert shared.solo_mode(str(repo / ".git")) is True


# ── The no-drift invariant ───────────────────────────────────────────

def test_both_hooks_resolve_the_same_primary_tree_and_solo_state(repo, tmp_path):
    """The reason this module exists. If the PreToolUse guard and the
    PostToolUse detector ever disagree about which tree is primary, or about
    whether solo mode is on, they protect different things and nothing notices.
    Asserted through the REAL import each hook performs, not a copy."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")

    probe = (
        "import importlib.util, json, sys\n"
        f"sys.path.insert(0, {HOOKS!r})\n"
        "import _shared_tree as s\n"
        f"gd = s.primary_git_dir({str(wt)!r})\n"
        "print(json.dumps([gd, s.solo_mode(gd)]))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, check=True).stdout
    git_dir, solo = json.loads(out)
    assert git_dir == str(repo / ".git")
    assert solo is False

    # Both hook scripts must import the shared module rather than carry a copy.
    for name in ("guard-shared-worktree.py", "detect-primary-tree-writes.py"):
        source = open(os.path.join(HOOKS, name)).read()
        assert "from _shared_tree import" in source, name
        assert 'worktrees/[^/]+' not in source, (
            f"{name} still carries its own copy of the resolution regex")


# ── Guard verdicts (pinning the refactor) ────────────────────────────

def run_guard(command, cwd, project_dir, env=None):
    """Returns the guard's permissionDecision, or None when it allowed."""
    payload = json.dumps({"tool_name": "Bash", "cwd": str(cwd),
                          "tool_input": {"command": command}})
    e = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    e.pop("CIM_SOLO", None)
    e.update(env or {})
    p = subprocess.run([sys.executable, GUARD], input=payload,
                       capture_output=True, text=True, env=e, timeout=30)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_guard_still_denies_a_git_mutation_in_the_primary_tree(repo):
    assert run_guard(f"git -C {repo} commit -m x", repo, repo) == "deny"


def test_guard_still_allows_a_git_mutation_inside_a_worktree(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    assert run_guard(f"git -C {wt} commit -m x", wt, repo) is None


def test_guard_still_allows_read_only_git_and_non_git_commands(repo):
    assert run_guard(f"git -C {repo} status", repo, repo) is None
    assert run_guard("echo hello", repo, repo) is None


def test_guard_solo_mode_still_opens_the_gate(repo):
    assert run_guard(f"git -C {repo} commit -m x", repo, repo,
                     env={"CIM_SOLO": "1"}) is None
    (repo / ".git" / "cim-solo").write_text("")
    assert run_guard(f"git -C {repo} commit -m x", repo, repo) is None


# ── Working-tree writers the subcommand list missed ──────────────────
# `pull` reached main unguarded: it is fetch + merge/rebase, so it rewrites the
# shared tree, but only `merge` and `rebase` were listed. `rm`/`mv`/`bisect` are
# the same failure mode — they write the tree without naming a listed subcommand.

def test_guard_denies_pull_in_the_primary_tree(repo):
    assert run_guard(f"git -C {repo} pull --ff-only origin main", repo, repo) == "deny"


def test_guard_denies_pull_with_no_explicit_target(repo):
    assert run_guard("git pull", repo, repo) == "deny"


def test_guard_allows_pull_inside_a_worktree(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    assert run_guard(f"git -C {wt} pull --ff-only origin main", wt, repo) is None


def test_guard_allows_pull_help(repo):
    assert run_guard(f"git -C {repo} pull --help", repo, repo) is None


def test_guard_denies_rm_and_mv_in_the_primary_tree(repo):
    assert run_guard(f"git -C {repo} rm config.py", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} mv config.py other.py", repo, repo) == "deny"


def test_guard_allows_rm_cached_and_dry_runs(repo):
    # --cached unstages but leaves the file on disk — index-only, like `restore --staged`
    assert run_guard(f"git -C {repo} rm --cached config.py", repo, repo) is None
    assert run_guard(f"git -C {repo} rm -n config.py", repo, repo) is None
    assert run_guard(f"git -C {repo} mv --dry-run config.py other.py", repo, repo) is None


def test_guard_denies_bisect_checkouts_but_allows_reading_the_log(repo):
    assert run_guard(f"git -C {repo} bisect start", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} bisect log", repo, repo) is None


def test_guard_still_allows_plain_fetch(repo):
    # fetch only moves remote-tracking refs; blocking it would break syncing
    assert run_guard(f"git -C {repo} fetch origin --prune", repo, repo) is None


def test_guard_denies_the_exotic_tree_writers(repo):
    """Second sweep. `sparse-checkout set` deletes directories from disk and
    `checkout-index -a -f` discards uncommitted content — both were allowed by
    the first pass, which proves the audit, not just the old list, was the bug.
    """
    for cmd in ("checkout-index -a -f", "read-tree -u HEAD",
                "merge-file a.txt b.txt c.txt", "filter-branch --all",
                "sparse-checkout init --cone", "sparse-checkout set keep",
                "submodule update --init", "submodule deinit --all"):
        assert run_guard(f"git -C {repo} {cmd}", repo, repo) == "deny", cmd


def test_guard_allows_reading_sparse_checkout_and_submodule_state(repo):
    for cmd in ("sparse-checkout list", "submodule status", "submodule summary",
                "checkout-index --help", "read-tree --help", "submodule --help"):
        assert run_guard(f"git -C {repo} {cmd}", repo, repo) is None, cmd


def test_guard_allows_bundled_short_dry_run_flags(repo):
    # `git rm -rn` and `git mv -fn` are real no-ops; denying them denies nothing
    assert run_guard(f"git -C {repo} rm -rn config.py", repo, repo) is None
    assert run_guard(f"git -C {repo} mv -fn config.py other.py", repo, repo) is None


def test_a_long_flag_that_merely_contains_n_is_not_a_dry_run(repo):
    # `--ignore-unmatch` carries an 'n'; a naive substring test would read it as
    # a dry run and wave a real deletion through.
    assert run_guard(f"git -C {repo} rm --ignore-unmatch config.py",
                     repo, repo) == "deny"


def test_guard_allows_read_only_bisect_queries(repo):
    for cmd in ("bisect", "bisect terms", "bisect visualize", "bisect log"):
        assert run_guard(f"git -C {repo} {cmd}", repo, repo) is None, cmd


def test_guard_denies_writes_that_do_not_look_like_writes(repo):
    """Round 3. None of these name a tree-writing verb. `archive -o` overwrites
    a tracked file; `symbolic-ref` moves the checked-out branch with no file
    changing at all — the guard's core collision, wearing no costume; `config
    core.worktree` redirects what the shared .git calls its working tree, and
    `core.hooksPath` makes every later commit run code from elsewhere.
    """
    assert run_guard(f"git -C {repo} archive -o config.py HEAD",
                     repo, repo) == "deny"
    assert run_guard(f"git -C {repo} symbolic-ref HEAD refs/heads/other",
                     repo, repo) == "deny"
    assert run_guard(f"git -C {repo} config core.worktree /tmp/elsewhere",
                     repo, repo) == "deny"
    assert run_guard(f"git -C {repo} config core.hooksPath /tmp/hooks",
                     repo, repo) == "deny"


def test_guard_allows_reading_config_refs_and_archiving_to_stdout(repo):
    for cmd in ("config --get user.email", "config --list", "config user.email",
                "symbolic-ref --short HEAD", "archive HEAD",
                "sparse-checkout check-rules"):
        assert run_guard(f"git -C {repo} {cmd}", repo, repo) is None, cmd


def test_an_alias_cannot_launder_a_mutation(repo):
    """`co = checkout` is an ordinary convenience alias, not an attack, and it
    silently disabled every rule in this file — classification stopped at the
    literal token and never asked whether the token was a rename.
    """
    git(repo, "config", "alias.co", "checkout")
    assert run_guard(f"git -C {repo} co other-branch", repo, repo) == "deny"
    # A shell alias can run anything, so it is denied without inspection.
    git(repo, "config", "alias.sync", "!git pull --ff-only")
    assert run_guard(f"git -C {repo} sync", repo, repo) == "deny"
    # A read-only alias stays allowed.
    git(repo, "config", "alias.st", "status")
    assert run_guard(f"git -C {repo} st", repo, repo) is None


def test_an_alias_still_fails_closed_when_the_target_is_unresolvable(repo):
    """The round-3 fix resolved aliases only once the target was known to be
    guarded — and an unresolvable target is exactly when it is not known. So
    `cd $UNSET && git co main` walked through the fail-closed path.
    """
    git(repo, "config", "alias.co", "checkout")
    assert run_guard("cd $NOPE && git co main", repo, repo) == "deny"


def test_an_alias_expansion_carrying_a_global_option_is_still_classified(repo):
    # Expansion begins `-c user.name=…`, so naive parsing reads the subcommand
    # as `-c`, which matches no rule. Globals must be skipped as in a real command.
    git(repo, "config", "alias.setcfg",
        "-c user.name=X commit --allow-empty -m y")
    assert run_guard(f"git -C {repo} setcfg", repo, repo) == "deny"


def test_a_long_alias_chain_does_not_run_out_of_depth_and_fail_open(repo):
    # Real git follows an arbitrarily long chain; a fixed cap that gives up and
    # allows turns "too deep to follow" into "safe".
    for name, points_at in (("loopa", "loopb"), ("loopb", "loopc"),
                            ("loopc", "loopd")):
        git(repo, "config", f"alias.{name}", points_at)
    git(repo, "config", "alias.loopd", "reset --hard")
    assert run_guard(f"git -C {repo} loopa", repo, repo) == "deny"


def test_guard_denies_rewrites_of_the_shared_clones_own_config(repo):
    """These touch no tracked file but reconfigure the clone for every later
    session — and `init --template` plants hooks that fire on `worktree add`,
    the very command every deny message tells the operator to run.
    """
    for cmd in ("remote set-url origin https://elsewhere.example/r.git",
                "remote add other https://elsewhere.example/r.git",
                "remote rename origin upstream",
                "branch --set-upstream-to=origin/main",
                "worktree repair",
                "init --template=/tmp/sometemplate"):
        assert run_guard(f"git -C {repo} {cmd}", repo, repo) == "deny", cmd


def test_guard_allows_reading_remotes(repo):
    for cmd in ("remote", "remote -v", "remote show origin",
                "remote get-url origin"):
        assert run_guard(f"git -C {repo} {cmd}", repo, repo) is None, cmd


def test_a_consumed_help_flag_is_not_help(repo):
    """Verified against real git: `commit --allow-empty -m --help` COMMITS —
    HEAD moves and the message is literally "--help". So only a LEADING
    `--help` may exempt; scanning the whole arg list fails open.
    """
    assert run_guard(f"git -C {repo} commit --allow-empty -m --help",
                     repo, repo) == "deny"
    assert run_guard(f"git -C {repo} checkout -b --help", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} commit --help", repo, repo) is None


def test_help_is_never_a_mutation(repo):
    # `git commit --help` opens a man page. Denying it teaches the operator the
    # guard cries wolf, which is how a real deny gets waved through later.
    for cmd in ("commit --help", "rebase --help", "merge --help",
                "apply --help", "gc --help", "revert --help",
                "cherry-pick --help", "am --help"):
        assert run_guard(f"git -C {repo} {cmd}", repo, repo) is None, cmd


def test_pull_deny_reason_points_at_solo_mode_not_just_a_worktree(repo):
    payload = json.dumps({"tool_name": "Bash", "cwd": str(repo),
                          "tool_input": {"command": f"git -C {repo} pull"}})
    e = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    e.pop("CIM_SOLO", None)
    p = subprocess.run([sys.executable, GUARD], input=payload,
                       capture_output=True, text=True, env=e, timeout=30)
    reason = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "CIM_SOLO" in reason
    assert "worktree does not sync" in reason


def test_guard_still_denies_a_file_edit_in_the_primary_tree(repo):
    payload = json.dumps({"tool_name": "Write",
                          "tool_input": {"file_path": str(repo / "config.py")},
                          "cwd": str(repo)})
    e = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    e.pop("CIM_SOLO", None)
    p = subprocess.run([sys.executable, GUARD], input=payload,
                       capture_output=True, text=True, env=e, timeout=30)
    assert p.returncode == 0
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── v6: worktree/branch admin, after an adversarial pass reverted half
#        of v5 ─────────────────────────────────────────────────────────
# Every "allowed" case is allowed because GIT refuses the destructive
# shape itself — verified against real git, and the cases v5 got WRONG
# are pinned here too so the argument cannot be re-made from memory:
#   worktree remove <main>       fatal: is a main working tree
#   worktree remove <visible-dirt> fatal: contains modified or untracked files
#   branch -d <unmerged>         error: the branch is not fully merged
#   branch -D <unmerged>         DELETES IT — which is why -D stays denied


def test_guard_allows_removing_a_worktree(repo, tmp_path):
    """The post-merge cleanup CLAUDE.md rule 3 names. Git will not run this
    on the main tree at all, so it cannot reach what the hook protects."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    assert run_guard(f"git -C {repo} worktree remove {wt}", repo, repo) is None
    assert run_guard(f"git -C {repo} worktree list", repo, repo) is None
    assert run_guard(f"git -C {repo} worktree add {tmp_path}/x -b y", repo, repo) is None


def test_guard_denies_the_destructive_worktree_forms(repo, tmp_path):
    """`--force` deletes a concurrent session's uncommitted work. `prune`
    decides "already gone" from whether the path resolves RIGHT NOW, and
    permanently breaks a worktree that was only temporarily moved."""
    wt = tmp_path / "wt"
    assert run_guard(f"git -C {repo} worktree remove --force {wt}", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} worktree remove -f {wt}", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} worktree prune", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} worktree move {wt} {tmp_path}/z", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} worktree repair", repo, repo) == "deny"


def test_guard_allows_a_safe_branch_delete_but_never_a_forced_one(repo):
    """`-d` is safe because git refuses it on an unmerged branch. `-D`
    exists to bypass exactly that check, and after a worktree is removed
    the ref is the only durable copy of the work (CLAUDE.md rule 1) — so
    it is the branch equivalent of `worktree remove --force`."""
    assert run_guard(f"git -C {repo} branch -d feature", repo, repo) is None
    assert run_guard(f"git -C {repo} branch --delete feature", repo, repo) is None

    assert run_guard(f"git -C {repo} branch -D feature", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch --delete --force feature", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch -df feature", repo, repo) == "deny"


# ── the other half: does CLAUDE.md recommend what the guard permits? ──
# For months rule 3 told every session that post-merge cleanup was
# `git branch -D` — the one command the test directly above proves this
# guard has denied since v6. Both halves were individually right and
# jointly contradictory, and it survived because nothing compared them:
# the guard had tests, the prose had readers. Two sessions ran into the
# denial before anyone treated the doc as the broken half.
#
# The first attempt at this gate was a hand-written table of commands, and
# it reproduced the same defect one level up: it claimed to cover "every
# git command CLAUDE.md's shipping section names" while never opening
# CLAUDE.md, so the claim was already false at the commit that made it —
# two rows named commands the prose does not, and the prose named two the
# table did not. Worse, it could not fail in the direction that caused the
# original bug: putting `git branch -D` back into rule 3 left every row
# green.
#
# So the command list is EXTRACTED from the doc and the table is a
# whitelist over it. What stays hand-written is the VERDICT column, which
# is intent, and no extractor holds intent.
#
# What this does NOT cover, stated because the file it guards says a safety
# tool you over-trust is worse than none:
#   * prose that describes a command without backticking it as `git …`;
#   * the other sections of CLAUDE.md, and every other doc in the repo;
#   * anything run through `gh`, which the guard never inspects at all —
#     that is precisely why rule 7's `--delete-branch` warning exists, and
#     it is the one rule here no test can enforce.

CLAUDE_MD = os.path.join(ROOT, "CLAUDE.md")
SHIPPING_HEADING = "## Shipping work"

#: A floor on what the extractor must find. Renaming or gutting the section
#: would otherwise satisfy every row below by finding nothing at all — a
#: completeness gate that passes vacuously is the failure it exists to catch.
MIN_SHIP_SPANS = 10


def shipping_git_spans():
    """Every distinct backticked `git …` span in CLAUDE.md's shipping section.

    Bounded by the next heading of ANY level, not the next `## ` — the
    section is immediately followed by the `# Compact instructions` H1, and
    a `^## ` boundary silently swallows it.
    """
    with open(CLAUDE_MD, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^%s\b.*?(?=^\#{1,6} )" % re.escape(SHIPPING_HEADING),
                  text, re.S | re.M)
    assert m, (f"CLAUDE.md has no {SHIPPING_HEADING!r} section. If it was "
               f"renamed, rename it here too — do not delete this test.")
    spans = []
    for span in re.findall(r"`([^`\n]+)`", m.group(0)):
        span = span.strip()
        if span.startswith("git ") and span not in spans:
            spans.append(span)
    return spans


#: The span EXACTLY as CLAUDE.md writes it -> (runnable form, expected
#: verdict, which tree it runs in). The key is verbatim so that rewording the
#: prose fails loudly instead of drifting quietly.
SHIP_COMMANDS = {
    "git worktree add .claude/worktrees/<slug> -b <branch> origin/main":
        ("git -C {repo} worktree add {tmp}/fresh -b fresh", None, "primary"),
    # Rule 2 says "in that exact directory" — i.e. YOUR worktree. Asserting
    # these against the primary tree would pin "staging into the shared index
    # is fine", which is the foreign-edit hazard rule 2 warns about.
    "git branch --show-current":
        ("git -C {wt} branch --show-current", None, "worktree"),
    "git diff":
        ("git -C {wt} diff", None, "worktree"),
    "git add":
        ("git -C {wt} add config.py", None, "worktree"),
    "git worktree remove <path>":
        ("git -C {repo} worktree remove {wt}", None, "primary"),
    "git branch -d":
        ("git -C {repo} branch -d feature", None, "primary"),
    "git checkout":
        ("git -C {repo} checkout main", "deny", "primary"),
    "git fetch origin --prune":
        ("git -C {repo} fetch origin --prune", None, "primary"),
    "git -C <primary> status --porcelain":
        ("git -C {repo} status --porcelain", None, "primary"),
    "git -C <primary> rev-list --count origin/main..HEAD":
        ("git -C {repo} rev-list --count origin/main..HEAD", None, "primary"),
    # Denied as written, and rule 8 knows it — the whole point of that rule is
    # that the solo marker is what opens it. The test below pins that half.
    "git -C <primary> pull --ff-only; rm -f <marker>":
        ("git -C {repo} pull --ff-only; rm -f {tmp}/marker", "deny", "primary"),
    "git worktree list":
        ("git -C {repo} worktree list", None, "primary"),
}

#: Extracted spans that are deliberately not commands, each with its reason.
#: Mirrors `NOT_IN_REGISTER` in the assumption register and `ALLOWED` in the
#: literal sweep: an escape that must be argued for, not a silent skip.
NOT_IN_TABLE = {
    "git …": "the preamble's reference to this very table, not a command",
}


def test_every_git_command_in_claude_mds_shipping_section_has_a_verdict():
    """The whitelist gate. A git command added to the shipping rules fails
    here until someone states what the guard should say about it.

    This is the direction the hand-written table could not fail in, and the
    direction the original defect actually came from: `git branch -D` was
    added to the prose, not to the hook."""
    spans = shipping_git_spans()
    assert len(spans) >= MIN_SHIP_SPANS, (
        f"only {len(spans)} `git …` spans found in {SHIPPING_HEADING!r} "
        f"(floor is {MIN_SHIP_SPANS}). Either the section was gutted or the "
        f"extractor stopped matching it; a vacuous pass here is the bug.")
    unaccounted = [s for s in spans
                   if s not in SHIP_COMMANDS and s not in NOT_IN_TABLE]
    assert not unaccounted, (
        f"CLAUDE.md's shipping rules name git commands with no verdict: "
        f"{unaccounted}. Add each to SHIP_COMMANDS with the verdict the prose "
        f"claims, or to NOT_IN_TABLE with a reason. Do not delete the span to "
        f"make this pass — the prose is what sessions actually follow.")
    stale = [s for s in SHIP_COMMANDS if s not in spans]
    assert not stale, (
        f"SHIP_COMMANDS has rows for spans CLAUDE.md no longer contains: "
        f"{stale}. The prose was reworded; re-key the rows to match it "
        f"verbatim so this keeps checking the text sessions read.")


@pytest.mark.parametrize("span", sorted(SHIP_COMMANDS))
def test_the_commands_claude_md_recommends_are_the_ones_the_guard_allows(
        repo, tmp_path, span):
    """Each extracted command, judged by the guard that will actually see it.

    The `deny` rows are the ones the prose explicitly warns are refused, so a
    guard that stopped denying them would make the doc wrong in the other
    direction — both are failures."""
    template, want, where = SHIP_COMMANDS[span]
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    cmd = template.format(repo=repo, tmp=tmp_path, wt=wt)
    cwd = wt if where == "worktree" else repo
    got = run_guard(cmd, cwd, repo)
    assert got == want, (
        f"CLAUDE.md's shipping rules name `{span}`, run here as `{cmd}` in "
        f"the {where} tree, but the guard says {got or 'allow'} where the "
        f"prose claims {want or 'allow'}. The doc and the hook have drifted "
        f"apart — fix one of them on the merits, not by deleting the row.")


def test_rule_8s_documented_marker_command_actually_opens_the_pull(repo, tmp_path):
    """Rule 8's whole procedure rests on one claim: that the `touch` it
    prints puts the marker where `solo_mode` reads it. The first version
    shipped `rev-parse --git-dir`, and that claim was FALSE — the flag prints
    a relative `.git`, which inside a linked worktree is a file, so the touch
    died with ENOTDIR; drop the `-C` instead and it silently seeds the
    worktree's own git dir, which solo mode never consults. Either way the
    pull the rule authorizes stayed denied.

    So the command is run verbatim, from a worktree (where rule 1 puts every
    session), and the guard itself is asked whether the gate opened."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    pull = f"git -C {repo} pull --ff-only"

    assert run_guard(pull, wt, repo) == "deny", "precondition: shut before the hatch"

    documented = f'touch "$(git -C {repo} rev-parse --absolute-git-dir)/cim-solo"'
    subprocess.run(documented, shell=True, cwd=str(wt), check=True)
    assert (repo / ".git" / shared.SOLO_MARKER).exists(), (
        "the documented touch did not land in the primary git dir")
    assert run_guard(pull, wt, repo) is None, (
        "marker placed by rule 8's own command, and the guard still denies "
        "the pull that rule exists to authorize")

    os.unlink(repo / ".git" / shared.SOLO_MARKER)
    assert run_guard(pull, wt, repo) == "deny", "and the hatch shuts again"


def test_the_short_marker_spellings_rule_4_warns_about_really_do_fail(repo, tmp_path):
    """The other half of the claim above: rule 4 tells sessions that the two
    obvious shorter forms are broken. If either quietly started working, the
    warning would be scaring people off a fine command — so both are pinned
    as failing, for the two DIFFERENT reasons the rule states."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    marker = repo / ".git" / shared.SOLO_MARKER

    # (a) relative `.git`, and in a worktree `.git` is a file -> ENOTDIR
    p = subprocess.run(f'touch "$(git -C {repo} rev-parse --git-dir)/cim-solo"',
                       shell=True, cwd=str(wt), capture_output=True, text=True)
    assert p.returncode != 0 and "Not a directory" in p.stderr, p
    assert not marker.exists()

    # (b) no -C: resolves to the WORKTREE's git dir, so the touch succeeds and
    #     the marker is simply never read. The silent one, and the worse one.
    subprocess.run('touch "$(git rev-parse --absolute-git-dir)/cim-solo"',
                   shell=True, cwd=str(wt), check=True)
    assert not marker.exists(), "expected the marker to land somewhere useless"
    assert run_guard(f"git -C {repo} pull --ff-only", wt, repo) == "deny"


def test_bundled_short_flags_cannot_launder_a_branch_rewrite(repo):
    """The hole seven hardening rounds walked past: git accepts `-fm` for
    `-f -m`, and exact-token matching never saw it, so a FORCE RENAME was
    allowed while the unbundled spelling denied. Verified against real git
    that `branch -fm src dst` actually performs the rename."""
    for bundled in ("-fm", "-mf", "-Mf", "-fM"):
        assert run_guard(f"git -C {repo} branch {bundled} old new", repo, repo) == "deny", bundled
    # The unbundled controls must still deny, or the test proves nothing.
    assert run_guard(f"git -C {repo} branch -f -m old new", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch -m old new", repo, repo) == "deny"


def test_guard_still_denies_the_other_branch_rewrites(repo):
    assert run_guard(f"git -C {repo} branch -M old new", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch -f feature HEAD", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch -C old new", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch --set-upstream-to=origin/x", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch --unset-upstream", repo, repo) == "deny"
    # The SHORT spelling too: `-u` writes branch.<n>.remote/.merge into the
    # shared .git/config just as the long form does, and a rewrite of this
    # function once carried the long form and dropped it.
    assert run_guard(f"git -C {repo} branch -u origin/main", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch -u main feature", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} branch --copy --force old new", repo, repo) == "deny"


def test_guard_still_allows_listing_and_creating_branches(repo):
    assert run_guard(f"git -C {repo} branch", repo, repo) is None
    assert run_guard(f"git -C {repo} branch -a", repo, repo) is None
    assert run_guard(f"git -C {repo} branch newbranch", repo, repo) is None
    assert run_guard(f"git -C {repo} branch --help", repo, repo) is None
    assert run_guard(f"git -C {repo} worktree --help", repo, repo) is None


def test_the_narrowed_forms_still_deny_a_real_tree_mutation(repo):
    """Regression fence: narrowing branch/worktree must not have loosened
    anything else in the same classifier."""
    assert run_guard(f"git -C {repo} checkout main", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} reset --hard", repo, repo) == "deny"
    assert run_guard(f"git -C {repo} commit -m x", repo, repo) == "deny"


# ── Carry-over across a context reset ────────────────────────────────
#
# The second pair on this shared module: PreCompact saves, SessionStart
# restores. Same failure mode as the first pair and the same reason for the
# tests — a writer and a reader that disagree about a path preserve nothing,
# silently, and a preservation mechanism nobody can tell is broken is worse
# than none.

PRECOMPACT = os.path.join(HOOKS, "precompact-carryover.py")
SESSION_START = os.path.join(HOOKS, "session-start-carryover.py")


def run_hook(script, payload, project_dir):
    """Run a carry-over hook; return its parsed additionalContext or ''."""
    e = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    e.pop("CIM_SOLO", None)
    p = subprocess.run([sys.executable, script], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return ""
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_the_notes_file_is_not_matched_as_a_snapshot():
    """The two names must not share a prefix. They did in the first draft,
    which made the notes file — the only part a machine cannot rebuild —
    returnable as a snapshot and eventually deletable as a stale one."""
    assert not shared.CARRYOVER_NOTES.startswith(shared.CARRYOVER_PREFIX)


def test_precompact_saves_and_session_start_restores_it(repo):
    """The round trip, through both real hooks and a real repo."""
    (repo / "config.py").write_text("dirtied\n")
    notes = os.path.join(str(repo / ".git"), shared.CARRYOVER_NOTES)
    with open(notes, "w") as f:
        f.write("## Phase 1 in flight\nNext: open the PR.\n")

    assert run_hook(PRECOMPACT, {
        "session_id": "sess-a", "cwd": str(repo),
        "hook_event_name": "PreCompact", "trigger": "auto",
        "message_count": 412}, repo) == ""      # saving is silent

    assert os.path.exists(shared.carryover_path(str(repo / ".git"), "sess-a"))

    out = run_hook(SESSION_START, {
        "session_id": "sess-a", "cwd": str(repo),
        "hook_event_name": "SessionStart", "source": "compact"}, repo)
    assert "Phase 1 in flight" in out           # the hand-written part
    assert "config.py" in out                   # the derived part
    assert str(repo) in out


def test_a_cleared_session_with_a_new_id_still_finds_the_snapshot(repo):
    """`/clear` may mint a NEW session id, orphaning a snapshot keyed by the
    old one. The bounded newest-snapshot fallback is what recovers it, and
    without this test that path is never exercised."""
    run_hook(PRECOMPACT, {"session_id": "before-clear", "cwd": str(repo),
                          "hook_event_name": "PreCompact"}, repo)
    out = run_hook(SESSION_START, {
        "session_id": "after-clear-different", "cwd": str(repo),
        "hook_event_name": "SessionStart", "source": "clear"}, repo)
    assert "CARRIED-OVER CONTEXT" in out


def test_only_the_sources_that_discard_context_restore(repo):
    """`resume` already has the real transcript and `startup` is genuinely
    new; re-injecting beside either is noise arguing with the record."""
    run_hook(PRECOMPACT, {"session_id": "s", "cwd": str(repo),
                          "hook_event_name": "PreCompact"}, repo)
    for source in ("startup", "resume", "fork"):
        assert run_hook(SESSION_START, {
            "session_id": "s", "cwd": str(repo),
            "hook_event_name": "SessionStart", "source": source}, repo) == "", source


def test_carry_over_says_nothing_when_nothing_was_carried(repo):
    """A hook that emits an empty banner trains the operator to skip it."""
    assert run_hook(SESSION_START, {
        "session_id": "never-saved", "cwd": str(repo),
        "hook_event_name": "SessionStart", "source": "clear"}, repo) == ""


def test_neither_carry_over_hook_fails_on_junk_input(repo):
    """Same invariant the other two hooks hold: never fail the operation
    being hooked. For PreCompact that is stronger than politeness — exit 2
    on that event BLOCKS the compaction the operator asked for."""
    for script in (PRECOMPACT, SESSION_START):
        for payload in ("not json at all", "[]", "null", '{"cwd": 42}'):
            p = subprocess.run([sys.executable, script], input=payload,
                               capture_output=True, text=True, timeout=30,
                               env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)})
            assert p.returncode == 0, (script, payload, p.stderr)


def test_both_carry_over_hooks_import_the_shared_paths(repo):
    """The same structural assertion the first pair gets: no hand-copied
    path constant, or the two halves can drift apart."""
    for script in (PRECOMPACT, SESSION_START):
        source = open(script).read()
        assert "from _shared_tree import" in source, script
        assert shared.CARRYOVER_PREFIX not in source, (
            f"{os.path.basename(script)} hard-codes the snapshot prefix "
            "instead of importing it")


# ── The notes file: the guard's ONE file exemption ───────────────────
# CLAUDE.md tells every session to maintain `<primary .git>/cim-session-
# notes.md` by hand, and the guard denied it — not by decision, but because
# `nearest_existing_dir` reduces a file path to its containing directory
# before the check, so the filename never reached it. The only sanctioned
# route left was CIM_SOLO, which switches the guard off clone-wide; that is
# a global bypass as the standing workflow for a routine act.
#
# Every test below asserts the exemption is exactly ONE file wide.


def run_file_guard(file_path, cwd, project_dir):
    """The guard's verdict on a Write. None when it allowed."""
    payload = json.dumps({"tool_name": "Write", "cwd": str(cwd),
                          "tool_input": {"file_path": str(file_path)}})
    e = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    e.pop("CIM_SOLO", None)
    p = subprocess.run([sys.executable, GUARD], input=payload,
                       capture_output=True, text=True, env=e, timeout=30)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_the_session_notes_file_is_writable_from_a_worktree(repo, tmp_path):
    """The case the exemption exists for: a session working in its own
    worktree — which is every session — updating the shared task board."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    notes = repo / ".git" / shared.CARRYOVER_NOTES

    assert run_file_guard(notes, wt, repo) is None


def test_the_notes_file_is_writable_before_it_exists(repo):
    """The first session to write it creates it. An exemption that only
    covered an existing file would deny exactly the first write."""
    notes = repo / ".git" / shared.CARRYOVER_NOTES
    assert not notes.exists()

    assert run_file_guard(notes, repo, repo) is None


def test_nothing_else_in_the_git_dir_is_exempt(repo):
    """The exemption is one FILE, not the git dir. `.git/config` is where
    every session pushes from and `.git/hooks` fires on the next `worktree
    add` — the command rules already defend both, and a directory-wide
    exemption would let `Write` walk in the door `git` is held at."""
    for name in ("config", "HEAD", "hooks/pre-commit",
                 shared.CARRYOVER_NOTES + ".bak",
                 shared.CARRYOVER_PREFIX + "abc123"):
        assert run_file_guard(repo / ".git" / name, repo, repo) == "deny", name


def test_the_notes_name_is_not_exempt_outside_the_git_dir(repo):
    """A file of the same name in the WORKING tree is an ordinary source
    file, and the primary tree is exactly what the guard protects."""
    assert run_file_guard(repo / shared.CARRYOVER_NOTES, repo, repo) == "deny"


def test_a_symlink_wearing_the_notes_name_is_not_exempt(repo):
    """The hole a realpath-both-sides comparison would open. The write
    FOLLOWS the link, so `ln -s ../config.py <git dir>/cim-session-notes.md`
    would carry the exemption to a source file — and both sides would
    resolve equal, so a naive comparison calls it a match."""
    link = repo / ".git" / shared.CARRYOVER_NOTES
    os.symlink(str(repo / "config.py"), str(link))

    assert run_file_guard(link, repo, repo) == "deny"


def test_the_exemption_resolves_a_dotdot_chain_rather_than_matching_text(repo):
    """Judged by where the path LANDS, not how it is spelled — the same
    property `primary_git_dir` has. A textual prefix test would refuse this
    spelling of the very file it means to allow."""
    indirect = repo / ".git" / "hooks" / ".." / shared.CARRYOVER_NOTES

    assert run_file_guard(indirect, repo, repo) is None


def test_the_guard_imports_the_notes_name_rather_than_restating_it(repo):
    """Same structural assertion the carry-over hooks get. A hard-coded
    copy here and a rename in `_shared_tree` would silently re-deny the
    file — failing open in the direction that trains a solo-mode habit.

    Asserted over string LITERALS rather than raw source text, docstrings
    excluded. A substring scan cannot tell "the code compares against this
    name" from "a docstring names the file it is about", and the prose is
    the part a reader needs most here. Comments never reach the AST at
    all, so they are exempt for free."""
    source = open(GUARD).read()
    tree = ast.parse(source)
    docstrings = {
        ast.get_docstring(n, clean=False)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef))
    }
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docstrings]

    assert "from _shared_tree import" in source
    assert shared.CARRYOVER_NOTES not in literals, (
        "guard-shared-worktree.py hard-codes the notes filename instead of "
        "importing CARRYOVER_NOTES")
