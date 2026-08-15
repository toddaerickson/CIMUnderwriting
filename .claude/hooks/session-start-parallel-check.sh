#!/usr/bin/env bash
# SessionStart hook for CIM_Analyst: parallel-session check (MANDATORY).
#
# Multiple concurrent Claude sessions can share this one clone. This hook makes
# a new session land knowing (a) whether other sessions' worktrees exist,
# (b) whether the primary tree has foreign in-progress edits, and (c) that the
# primary working tree is mutation-guarded — work happens in an isolated
# worktree, not here.
#
# Detection is read-only and best-effort; the ENFORCEMENT lives in the
# PreToolUse guard (.claude/hooks/guard-shared-worktree.py), which denies any
# file or git mutation targeting the primary tree. Rule set imported from
# fcs-call-reports (see CLAUDE.md "Simultaneous sessions").
#
# Always exits 0 — a status hook must never block a session.

set -u

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

echo "=== CIM_Analyst parallel-session check ==="

# Solo-mode escape hatch — surface it LOUDLY so it is never left on silently.
_gitdir=$(git rev-parse --absolute-git-dir 2>/dev/null)
if [ -n "${CIM_SOLO:-}" ] || { [ -n "$_gitdir" ] && [ -f "$_gitdir/cim-solo" ]; }; then
  echo
  echo "!!  WORKTREE GUARD OFF (solo mode) — primary-clone mutations are NOT blocked."
  [ -n "${CIM_SOLO:-}" ] && echo "      • via CIM_SOLO env (unset it to re-enable)"
  [ -n "$_gitdir" ] && [ -f "$_gitdir/cim-solo" ] && echo "      • via marker $_gitdir/cim-solo (rm it to re-enable)"
fi

# A linked worktree's git-dir lives under <shared>.git/worktrees/<name>.
case "$_gitdir" in
  */worktrees/*)
    echo
    echo "This session is INSIDE an isolated worktree ($(pwd)) — mutations are"
    echo "allowed here. The shared primary tree remains guarded."
    ;;
esac

echo
echo "Branch here: $(git branch --show-current 2>/dev/null || echo '(detached)')"
echo "  (snapshot only — another session can switch a shared checkout mid-task;"
echo "   re-assert with 'git branch --show-current' before any commit)"

# Linked worktrees = other sessions' isolated work areas.
wt=$(git worktree list 2>/dev/null | tail -n +2)
if [ -n "$wt" ]; then
  echo
  echo "Linked worktrees (possible concurrent sessions — do not touch their dirs):"
  echo "$wt" | sed 's/^/  /'
fi

# Dirty state in this tree. In the shared primary, any of it may be ANOTHER
# session's in-progress work — treat as foreign; never add/commit over it.
dirty=$(git status --short 2>/dev/null | head -8)
if [ -n "$dirty" ]; then
  echo
  echo "Uncommitted changes here (in the primary these may be another session's WIP):"
  echo "$dirty" | sed 's/^/  /'
fi

echo
echo "MANDATORY: do NOT mutate the primary working tree. The PreToolUse guard"
echo "denies file edits and git mutations here. Before any file-mutating work,"
echo "create an isolated worktree and do everything there:"
echo "  git worktree add .claude/worktrees/<slug> -b <branch> origin/main"
echo "Commit early (refs are durable; the worktree dir is not). Merge via gh;"
echo "clean up LOCALLY per CLAUDE.md rule 3 (worktree remove, then a"
echo "best-effort branch -d) and never pass 'gh pr merge --delete-branch';"
echo "never 'git checkout' the primary tree to ship."
echo "==="

exit 0
