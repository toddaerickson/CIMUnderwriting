"""`.claude/hooks/detect-primary-tree-writes.py` — the PostToolUse detector.

Every test drives the hook the way Claude Code does: real git repositories on
disk, a JSON payload on stdin, and assertions on the JSON it prints. Nothing is
mocked, because the thing under test IS the interaction with git — a mocked
`git status` would pass while the hook watched the wrong tree.

The detector's contract, in order of how badly each failure would hurt:

1. It must FIRE on a Bash-shaped write to the primary tree — the hole the
   PreToolUse guard structurally cannot cover.
2. It must be SILENT otherwise. A detector that cries wolf gets ignored, and an
   ignored detector is worse than none because it reads like coverage.
3. It must never fail a tool call, whatever git does.
"""
import json
import os
import subprocess
import sys
import time

import pytest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    ".claude", "hooks", "detect-primary-tree-writes.py")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def run_hook(project_dir, session_id="s1", env=None, cwd=None):
    """Invoke the hook exactly as the harness does. Returns (parsed_json_or_None,
    returncode) — a bare exit 0 with no stdout is the 'stay quiet' contract."""
    payload = json.dumps({
        "tool_name": "Bash",
        "cwd": str(cwd or project_dir),
        "session_id": session_id,
        "tool_input": {"command": "true"},
    })
    e = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    e.pop("CIM_SOLO", None)
    e.update(env or {})
    p = subprocess.run([sys.executable, HOOK], input=payload,
                       capture_output=True, text=True, env=e, timeout=30)
    out = p.stdout.strip()
    return (json.loads(out) if out else None), p.returncode


def context_of(result):
    return (result or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.fixture
def repo(tmp_path):
    """A committed git repo standing in for the shared primary tree."""
    r = tmp_path / "clone"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    (r / "config.py").write_text("ORIGINAL = 1\n")
    (r / "keep.txt").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def arm(repo, **kw):
    """First call establishes the baseline and must stay silent."""
    result, code = run_hook(repo, **kw)
    assert code == 0
    assert result is None, "the baseline call must not report"


# ── 1. It fires on the hole it exists for ────────────────────────────

def test_a_bash_redirect_into_the_primary_tree_is_reported(repo):
    """The exact gap: `echo > file` is invisible to the PreToolUse guard,
    which only inspects commands whose command word is `git`."""
    arm(repo)
    subprocess.run(f"echo pwned > {repo}/config.py", shell=True, check=True)

    result, code = run_hook(repo)
    assert code == 0
    ctx = context_of(result)
    assert "PRIMARY-TREE DRIFT DETECTED" in ctx
    assert "config.py" in ctx


def test_an_interpreter_fed_by_a_heredoc_is_reported(repo):
    """The vector actually hit in practice: a relative path inside a heredoc
    script resolving against the primary tree, because `cd` does not persist
    between tool calls."""
    arm(repo)
    subprocess.run(
        f"cd {repo} && python3 - <<'PY'\n"
        "import pathlib; pathlib.Path('config.py').write_text('drift')\n"
        "PY",
        shell=True, check=True)

    assert "config.py" in context_of(run_hook(repo)[0])


def test_a_new_untracked_file_is_reported(repo):
    arm(repo)
    (repo / "stray.py").write_text("x\n")
    assert "stray.py" in context_of(run_hook(repo)[0])


def test_a_deletion_is_reported(repo):
    arm(repo)
    (repo / "keep.txt").unlink()
    assert "keep.txt" in context_of(run_hook(repo)[0])


def test_a_branch_switch_under_the_session_is_reported(repo):
    """The original collision the whole rule set exists for — another session
    switching the shared checkout out from under this one. Caught for free."""
    arm(repo)
    git(repo, "checkout", "-q", "-b", "other")

    ctx = context_of(run_hook(repo)[0])
    assert "branch changed" in ctx
    assert "'main'" in ctx and "'other'" in ctx


def test_a_commit_in_the_primary_tree_moves_head_and_is_reported(repo):
    arm(repo)
    (repo / "config.py").write_text("CHANGED = 2\n")
    git(repo, "commit", "-q", "-am", "sneaky")

    assert "HEAD moved" in context_of(run_hook(repo)[0])


def test_it_watches_the_primary_tree_from_inside_a_worktree(repo, tmp_path):
    """The normal working posture: the session lives in a linked worktree. A
    worktree's git dir maps back to the shared .git, so the primary tree is
    still what gets watched — otherwise the hook would go blind exactly when
    it is supposed to be on duty."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feature")
    arm(wt)

    (repo / "config.py").write_text("drift from the worktree session\n")
    ctx = context_of(run_hook(wt)[0])
    assert "config.py" in ctx
    # Writes inside the worktree itself are the whole point of having one.
    (wt / "config.py").write_text("legitimate work\n")
    assert context_of(run_hook(wt)[0]) == ""


# ── 2. It stays silent when it should ────────────────────────────────

def test_no_change_reports_nothing(repo):
    arm(repo)
    assert run_hook(repo)[0] is None
    assert run_hook(repo)[0] is None


def test_a_drift_is_reported_once_not_on_every_later_command(repo):
    """A detector that repeats itself on every subsequent call is noise, and
    noise is how a real alarm gets scrolled past."""
    arm(repo)
    (repo / "config.py").write_text("drift\n")

    assert "config.py" in context_of(run_hook(repo)[0])
    assert run_hook(repo)[0] is None, "second call must not repeat the report"
    assert run_hook(repo)[0] is None


def test_a_path_returning_to_clean_re_arms_it(repo):
    """The baseline must track the tree back to clean, or the NEXT real write
    to that same path would compare against a stale 'already dirty' baseline
    and be missed entirely."""
    arm(repo)
    (repo / "config.py").write_text("drift\n")
    assert "config.py" in context_of(run_hook(repo)[0])

    git(repo, "checkout", "--", "config.py")
    assert run_hook(repo)[0] is None            # cleaned; nothing to say

    (repo / "config.py").write_text("drift again\n")
    assert "config.py" in context_of(run_hook(repo)[0])


def test_pre_existing_dirt_is_not_blamed_on_this_session(repo):
    """Another session's in-progress work is the normal state of the shared
    tree. Only what appears AFTER the baseline is reported."""
    (repo / "config.py").write_text("another session's WIP\n")
    arm(repo)

    assert run_hook(repo)[0] is None
    (repo / "stray.py").write_text("mine\n")
    ctx = context_of(run_hook(repo)[0])
    assert "stray.py" in ctx
    assert "config.py" not in ctx


def test_staging_an_already_dirty_file_is_not_a_new_write(repo):
    """`git add` moves a path between porcelain columns without writing to the
    working tree. Comparing raw status text rather than paths would flag it."""
    arm(repo)
    (repo / "config.py").write_text("drift\n")
    assert "config.py" in context_of(run_hook(repo)[0])

    git(repo, "add", "config.py")
    assert run_hook(repo)[0] is None


def test_sessions_do_not_share_a_baseline(repo):
    """Each session compares against its OWN starting point; a second session
    must not inherit the first's and go blind to its own drift."""
    arm(repo, session_id="alpha")
    (repo / "config.py").write_text("drift\n")
    assert "config.py" in context_of(run_hook(repo, session_id="alpha")[0])

    arm(repo, session_id="beta")                 # beta baselines the dirty tree
    (repo / "stray.py").write_text("x\n")
    assert "stray.py" in context_of(run_hook(repo, session_id="beta")[0])


@pytest.mark.parametrize("mode", ["env", "marker"])
def test_solo_mode_disables_it(repo, mode):
    """Solo work dirties the primary tree by design — reporting that is noise,
    and it must be silenced by the SAME two switches the PreToolUse guard
    honors, or the escape hatch is only half an escape."""
    env = {"CIM_SOLO": "1"} if mode == "env" else {}
    if mode == "marker":
        arm(repo)
        (repo / ".git" / "cim-solo").write_text("")
    else:
        arm(repo, env=env)
    (repo / "config.py").write_text("deliberate solo edit\n")

    assert run_hook(repo, env=env)[0] is None


# ── 3. It never fails a tool call ────────────────────────────────────

def test_outside_a_repo_it_says_nothing(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    result, code = run_hook(plain)
    assert code == 0 and result is None


def test_malformed_payload_exits_clean(repo):
    p = subprocess.run([sys.executable, HOOK], input="not json",
                       capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
                       timeout=30)
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_an_unreadable_baseline_re_arms_instead_of_crashing(repo):
    arm(repo)
    git_dir = repo / ".git"
    baseline = next(p for p in git_dir.iterdir()
                    if p.name.startswith("cim-tree-baseline-"))
    baseline.write_text("{ this is not json")

    result, code = run_hook(repo)
    assert code == 0 and result is None          # re-armed, silently
    (repo / "stray.py").write_text("x\n")
    assert "stray.py" in context_of(run_hook(repo)[0])


def test_the_report_is_bounded(repo):
    """An unbounded listing could paste a generated tree into the transcript."""
    arm(repo)
    for i in range(40):
        (repo / f"f{i:03d}.txt").write_text("x\n")

    ctx = context_of(run_hook(repo)[0])
    assert "and 20 more" in ctx
    assert ctx.count("f0") <= 25


# ── 4. Review repairs (PR #25) ───────────────────────────────────────

def test_a_second_write_to_an_ALREADY_dirty_path_is_reported(repo):
    """The worst of the review findings. Foreign dirty state is the NORMAL
    condition of a shared tree, so the path you then write to is routinely
    already listed — and tracking paths alone reports nothing, in exactly the
    situation the hook exists for."""
    (repo / "config.py").write_text("another session's WIP\n")
    arm(repo)
    assert run_hook(repo)[0] is None            # quiet: nothing new yet

    (repo / "config.py").write_text("MY stray write on top of it\n")
    ctx = context_of(run_hook(repo)[0])
    assert "config.py" in ctx
    assert "written again" in ctx


def test_a_rewrite_with_identical_content_and_size_still_moves_mtime(repo):
    """Byte-identical rewrites are still writes; stat's nanosecond mtime is
    what makes them visible."""
    (repo / "config.py").write_text("same\n")
    arm(repo)
    time.sleep(0.01)
    (repo / "config.py").write_text("same\n")

    assert "config.py" in context_of(run_hook(repo)[0])


def test_a_non_string_session_id_does_not_crash_the_hook(repo):
    """The one absolute invariant: never fail a tool call. `cwd` was
    type-checked and `session_id` was not."""
    payload = json.dumps({"tool_name": "Bash", "cwd": str(repo),
                          "session_id": 12345, "tool_input": {"command": "true"}})
    p = subprocess.run([sys.executable, HOOK], input=payload,
                       capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
                       timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stderr.strip() == ""


def test_an_unreadable_tree_reports_degraded_once_not_silence(repo, tmp_path):
    """Going quiet on failure is indistinguishable from reporting a clean
    tree — the single thing a detector must never be."""
    arm(repo)
    # A git dir whose checkout root cannot be resolved.
    broken = tmp_path / "broken.git"
    broken.mkdir()

    payload = json.dumps({"tool_name": "Bash", "cwd": str(tmp_path),
                          "session_id": "s1", "tool_input": {"command": "true"}})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    # Point the hook at a git dir with no resolvable work tree by making the
    # repo's own .git unreadable as a work tree: simulate via a bare clone.
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(bare)],
                   check=True)
    env["CLAUDE_PROJECT_DIR"] = str(bare)
    p = subprocess.run([sys.executable, HOOK], input=payload,
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0
    out = json.loads(p.stdout) if p.stdout.strip() else None
    assert "MONITORING DEGRADED" in context_of(out)

    # ... and only once.
    p2 = subprocess.run([sys.executable, HOOK], input=payload,
                        capture_output=True, text=True, env=env, timeout=30)
    assert p2.stdout.strip() == ""


def test_a_baseline_from_an_older_format_re_arms_rather_than_misreads(repo):
    """A v1 baseline held a `paths` list; comparing it field-by-field against
    the v2 shape would report nonsense."""
    arm(repo)
    baseline = next(p for p in (repo / ".git").iterdir()
                    if p.name.startswith("cim-tree-baseline-"))
    baseline.write_text(json.dumps({"paths": ["config.py"], "head": "abc",
                                    "branch": "main"}))

    assert run_hook(repo)[0] is None            # re-armed silently
    (repo / "stray.py").write_text("x\n")
    assert "stray.py" in context_of(run_hook(repo)[0])


def test_gitignored_writes_are_invisible_and_the_docs_say_so(repo):
    """A KNOWN, DELIBERATE limitation, pinned so it can't quietly become an
    accidental one: `git status` is the eye, so .gitignore hides writes from
    it. Reporting ignored paths would bury every real finding under .venv and
    staticfiles churn. The claim in the docstring must match."""
    (repo / ".gitignore").write_text("secret.env\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore")
    arm(repo)

    (repo / "secret.env").write_text("TOKEN=1\n")
    assert run_hook(repo)[0] is None            # invisible, by design

    source = open(HOOK).read()
    assert "cannot see writes to GITIGNORED paths" in source
    assert "catches every write vector" not in source, (
        "the docstring must not claim total coverage it does not have")
