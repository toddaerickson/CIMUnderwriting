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
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     ".claude", "hooks")
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
