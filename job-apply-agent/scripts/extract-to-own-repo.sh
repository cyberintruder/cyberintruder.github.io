#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Lift job-apply-agent out of the parent repository into its own public repo,
# preserving the commit history that touched this subtree.
#
#   1. Create an EMPTY public repo on GitHub named `job-apply-agent`
#      (no README, no .gitignore, no license — this script supplies them).
#   2. Run this script from anywhere inside the parent repository.
#
#   ./job-apply-agent/scripts/extract-to-own-repo.sh cyberintruder
# ---------------------------------------------------------------------------
set -euo pipefail

OWNER="${1:-}"
REPO="${2:-job-apply-agent}"
SUBTREE="job-apply-agent"

if [[ -z "$OWNER" ]]; then
  echo "usage: $0 <github-owner> [repo-name]" >&2
  echo "example: $0 cyberintruder" >&2
  exit 1
fi

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [[ ! -d "$SUBTREE" ]]; then
  echo "error: $SUBTREE/ not found at the repository root ($ROOT)" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> splitting $SUBTREE/ history onto a temporary branch"
git subtree split --prefix="$SUBTREE" -b "__extract-$$" >/dev/null

echo "==> cloning the split history into $WORK/$REPO"
git clone --quiet --branch "__extract-$$" --single-branch "$ROOT" "$WORK/$REPO"

cd "$WORK/$REPO"
git checkout --quiet -b main
git branch --quiet -D "__extract-$$" 2>/dev/null || true
git remote remove origin
git remote add origin "https://github.com/$OWNER/$REPO.git"

cd "$ROOT"
git branch -D "__extract-$$" >/dev/null

echo
echo "Ready. The standalone repository is at:"
echo "    $WORK/$REPO"
echo
echo "Push it with:"
echo "    cd $WORK/$REPO && git push -u origin main"
echo
echo "Note: \$TMPDIR is cleaned on exit, so copy it somewhere permanent first:"
echo "    cp -r $WORK/$REPO ~/job-apply-agent"
