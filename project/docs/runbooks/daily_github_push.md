# Daily GitHub Push Runbook

## Goal

One command at end-of-day to commit local changes and push current branch to GitHub.

## Script

- scripts/daily_github_push.ps1

## Default Behavior

When you run the script with no flags, it does this in order:

1. Validates repository and current branch.
2. Stages all changes (`git add -A`).
3. Creates a commit if there are changes.
   - Default message: `chore: daily sync YYYY-MM-DD HH:mm`
4. Pulls with rebase if upstream exists.
5. Pushes to GitHub.
   - If upstream is missing, pushes with `-u origin <branch>`.

If there are no local changes, it still attempts push so branch state is synced.

## Usage

Run from repository root:

~~~powershell
.\scripts\daily_github_push.ps1
~~~

Custom commit message:

~~~powershell
.\scripts\daily_github_push.ps1 -CommitMessage "chore: end-of-day progress"
~~~

Dry-run preview (no write commands executed):

~~~powershell
.\scripts\daily_github_push.ps1 -DryRun
~~~

Commit only (skip push):

~~~powershell
.\scripts\daily_github_push.ps1 -SkipPush
~~~

Skip pull/rebase before push:

~~~powershell
.\scripts\daily_github_push.ps1 -SkipPullRebase
~~~

## Notes

1. Script includes fallback Git executable path for this Windows setup if `git` is not on PATH.
2. Keep sensitive files out of git tracking (`.env` is already ignored).
3. If rebase fails due to conflicts, resolve conflicts, then run the script again.