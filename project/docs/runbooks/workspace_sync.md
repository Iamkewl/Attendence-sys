# Workspace Sync Runbook

## Goal

Keep the local workspace and remote server workspace aligned so code and docs changes do not drift.

## Recommended Strategy (Default)

Use Git as source of truth, then use rsync only for fast file mirroring when needed.

1. Local workflow:
   - commit locally
   - push branch
2. Server workflow:
   - fetch
   - pull --ff-only on the same branch
3. Use rsync when you need immediate transfer of uncommitted local changes.

This avoids silent overwrite conflicts and gives an audit trail.

## rsync Script Added

Use:

- scripts/sync_workspace.ps1

The script supports:

- push (local -> server)
- pull (server -> local)
- bi (push then pull)
- reconcile (pull then push with newer-file protection)
- one-shot mode
- watch mode (periodic sync loop)
- optional delete mode
- optional dry-run mode
- WSL distro selection via -WslDistro (default: Ubuntu)

It uses .rsyncignore at repo root to avoid syncing generated artifacts and secrets.

## Prerequisites

Windows local machine:

1. OpenSSH client available (already present on most Windows 11 setups).
2. rsync runtime available via one of:
   - WSL distro with rsync installed (recommended)
   - Native rsync for Windows (MSYS2/cwRsync)

If using WSL, install a full distro and rsync:

~~~powershell
wsl --install -d Ubuntu
wsl -d Ubuntu -- sudo apt-get update
wsl -d Ubuntu -- sudo apt-get install -y rsync openssh-client
~~~

## Usage

Run from repository root.

### 1) Push local changes to server once

~~~powershell
.\scripts\sync_workspace.ps1 \
  -RemoteHost 10.0.0.25 \
  -RemoteUser dell \
  -RemotePath /srv/attendance/Attendence-sys \
  -Direction push
~~~

### 2) Pull server changes to local once

~~~powershell
.\scripts\sync_workspace.ps1 \
  -RemoteHost 10.0.0.25 \
  -RemoteUser dell \
  -RemotePath /srv/attendance/Attendence-sys \
  -Direction pull
~~~

### 3) Continuous push every 10 seconds

~~~powershell
.\scripts\sync_workspace.ps1 \
  -RemoteHost 10.0.0.25 \
  -RemoteUser dell \
  -RemotePath /srv/attendance/Attendence-sys \
  -Direction push \
  -Watch \
  -IntervalSeconds 10
~~~

### 3b) Continuous push with server-change protection

If you might edit files on server too, add receiver protection:

~~~powershell
.\scripts\sync_workspace.ps1 \
  -RemoteHost 10.0.0.25 \
  -RemoteUser dell \
  -RemotePath /srv/attendance/Attendence-sys \
  -Direction push \
  -Watch \
  -IntervalSeconds 10 \
  -ProtectReceiverNewer
~~~

This enables rsync --update, which skips overwriting files that are newer on the receiver side.

### 3c) Continuous newer-wins reconciliation (recommended for dual editing)

Use this when you edit on both local and server and want server-newer files to come back to local automatically.

~~~powershell
.\scripts\sync_workspace.ps1 \
  -RemoteHost 10.0.0.25 \
  -RemoteUser dell \
  -RemotePath /srv/attendance/Attendence-sys \
  -Direction reconcile \
  -Watch \
  -IntervalSeconds 10 \
  -WslDistro Ubuntu
~~~

### 4) Safe preview (no writes)

~~~powershell
.\scripts\sync_workspace.ps1 \
  -RemoteHost 10.0.0.25 \
  -RemoteUser dell \
  -RemotePath /srv/attendance/Attendence-sys \
  -Direction push \
  -DryRun
~~~

### 5) Exact mirror push (deletes removed local files on server)

Use with caution.

~~~powershell
.\scripts\sync_workspace.ps1 \
  -RemoteHost 10.0.0.25 \
  -RemoteUser dell \
  -RemotePath /srv/attendance/Attendence-sys \
  -Direction push \
  -Delete
~~~

## Conflict and Safety Notes

1. Prefer single-writer mode for near-real-time sync:
   - local is writer
   - server is execution target
2. If both sides are edited, use -ProtectReceiverNewer to reduce accidental overwrite risk.
3. bi mode can still produce last-writer-wins behavior when mtimes/content race.
3. For production-like stability, use push mode + Git commits.
4. Keep .env and secrets out of rsync scope (already excluded in .rsyncignore).

## Best Alternative for Constant Two-Way Sync

If you need true near-real-time two-way sync with conflict handling and continuous background operation, use one of:

1. Syncthing (best for continuous bidirectional developer sync)
2. Mutagen (best for code-heavy dev loops over SSH)

For this project, recommended operational model is:

1. Git for authoritative history and release state
2. sync_workspace.ps1 in push watch mode for rapid local -> server propagation during active development
3. pull sync before starting work if server-side hotfixes were made