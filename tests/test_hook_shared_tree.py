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
