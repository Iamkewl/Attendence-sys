Param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteHost,

    [Parameter(Mandatory = $true)]
    [string]$RemotePath,

    [string]$RemoteUser = "",
    [int]$RemotePort = 22,
    [string]$LocalPath = "",
    [string]$WslDistro = "Ubuntu",
    [ValidateSet("push", "pull", "bi", "reconcile")]
    [string]$Direction = "push",
    [switch]$Watch,
    [int]$IntervalSeconds = 10,
    [switch]$Delete,
    [string]$SshKeyPath = "",
    [string]$ExcludeFile = ".rsyncignore",
    [switch]$ProtectReceiverNewer,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $LocalPath) {
    $LocalPath = Split-Path -Parent $PSScriptRoot
}

if ($IntervalSeconds -lt 1) {
    throw "IntervalSeconds must be >= 1"
}

$resolvedLocalPath = [System.IO.Path]::GetFullPath($LocalPath)

if (-not (Test-Path $resolvedLocalPath)) {
    throw "Local path does not exist: $resolvedLocalPath"
}

$resolvedExcludeFile = if ([System.IO.Path]::IsPathRooted($ExcludeFile)) {
    $ExcludeFile
}
else {
    Join-Path $resolvedLocalPath $ExcludeFile
}

function Add-TrailingSlash {
    param([string]$Path)
    if ($Path.EndsWith("/")) {
        return $Path
    }
    return "$Path/"
}

function To-WslPath {
    param([string]$WindowsPath)

    if ($WindowsPath -match "^[a-zA-Z]:\\") {
        $drive = $WindowsPath.Substring(0, 1).ToLowerInvariant()
        $rest = $WindowsPath.Substring(2).Replace("\", "/")
        return "/mnt/$drive$rest"
    }

    return $WindowsPath.Replace("\", "/")
}

function Resolve-RsyncRuntime {
    param([string]$Distro)

    $native = Get-Command rsync -ErrorAction SilentlyContinue
    if ($native) {
        return @{ Mode = "native"; Command = "rsync" }
    }

    $wsl = Get-Command wsl -ErrorAction SilentlyContinue
    if ($wsl) {
        & wsl -d $Distro -- sh -lc "command -v rsync >/dev/null 2>&1"
        if ($LASTEXITCODE -eq 0) {
            return @{ Mode = "wsl"; Command = "wsl"; Distro = $Distro }
        }
    }

    throw @"
rsync is not available.

Install one of the following and re-run:
1) WSL distro + rsync: wsl --install -d Ubuntu, then sudo apt-get install -y rsync
2) Native rsync for Windows (MSYS2/cwRsync)
"@
}

function Build-RemoteSpec {
    param(
        [string]$User,
        [string]$HostValue,
        [string]$Path
    )

    if ($User) {
        return "${User}@${HostValue}:${Path}"
    }
    return "${HostValue}:${Path}"
}

function Build-SshCommand {
    param(
        [string]$Mode,
        [int]$Port,
        [string]$KeyPath
    )

    $parts = @("ssh", "-p", "$Port", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

    if ($KeyPath) {
        $effectiveKeyPath = if ($Mode -eq "wsl") { To-WslPath ([System.IO.Path]::GetFullPath($KeyPath)) } else { [System.IO.Path]::GetFullPath($KeyPath) }
        $parts += @("-i", $effectiveKeyPath)
    }

    return ($parts -join " ")
}

function Invoke-RsyncCall {
    param(
        [string]$Mode,
        [string]$WslRuntimeDistro,
        [string]$Source,
        [string]$Destination,
        [string]$SshCommand,
        [bool]$UseDelete,
        [bool]$UseUpdate,
        [bool]$UseDryRun,
        [string]$ExcludePath
    )

    $args = @(
        "-az",
        "--human-readable",
        "--partial",
        "--mkpath",
        "--info=stats2,progress2",
        "-e", $SshCommand
    )

    if ($UseDelete) {
        $args += "--delete"
    }

    if ($UseUpdate) {
        $args += "--update"
    }

    if ($UseDryRun) {
        $args += "--dry-run"
    }

    if (Test-Path $ExcludePath) {
        $effectiveExclude = if ($Mode -eq "wsl") { To-WslPath ([System.IO.Path]::GetFullPath($ExcludePath)) } else { [System.IO.Path]::GetFullPath($ExcludePath) }
        $args += "--exclude-from=$effectiveExclude"
    }

    $args += @($Source, $Destination)

    Write-Host "Running rsync: $Source -> $Destination"

    if ($Mode -eq "wsl") {
        & wsl -d $WslRuntimeDistro -- rsync @args
    }
    else {
        & rsync @args
    }

    if ($LASTEXITCODE -ne 0) {
        throw "rsync failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Sync {
    param(
        [string]$Mode,
        [string]$WslRuntimeDistro,
        [string]$DirectionValue,
        [string]$Local,
        [string]$Remote,
        [string]$SshCommand,
        [bool]$UseDelete,
        [bool]$UseUpdate,
        [bool]$UseDryRun,
        [string]$ExcludePath
    )

    $localEffective = if ($Mode -eq "wsl") { To-WslPath $Local } else { $Local.Replace("\", "/") }
    $localWithSlash = Add-TrailingSlash $localEffective
    $remoteWithSlash = Add-TrailingSlash $Remote
    $effectiveUseUpdate = $UseUpdate -or ($DirectionValue -eq "reconcile")

    switch ($DirectionValue) {
        "push" {
            Invoke-RsyncCall -Mode $Mode -WslRuntimeDistro $WslRuntimeDistro -Source $localWithSlash -Destination $remoteWithSlash -SshCommand $SshCommand -UseDelete:$UseDelete -UseUpdate:$effectiveUseUpdate -UseDryRun:$UseDryRun -ExcludePath $ExcludePath
        }
        "pull" {
            Invoke-RsyncCall -Mode $Mode -WslRuntimeDistro $WslRuntimeDistro -Source $remoteWithSlash -Destination $localWithSlash -SshCommand $SshCommand -UseDelete:$UseDelete -UseUpdate:$effectiveUseUpdate -UseDryRun:$UseDryRun -ExcludePath $ExcludePath
        }
        "bi" {
            Invoke-RsyncCall -Mode $Mode -WslRuntimeDistro $WslRuntimeDistro -Source $localWithSlash -Destination $remoteWithSlash -SshCommand $SshCommand -UseDelete:$UseDelete -UseUpdate:$effectiveUseUpdate -UseDryRun:$UseDryRun -ExcludePath $ExcludePath
            Invoke-RsyncCall -Mode $Mode -WslRuntimeDistro $WslRuntimeDistro -Source $remoteWithSlash -Destination $localWithSlash -SshCommand $SshCommand -UseDelete:$false -UseUpdate:$effectiveUseUpdate -UseDryRun:$UseDryRun -ExcludePath $ExcludePath
        }
        "reconcile" {
            # Reconcile mode prioritizes importing newer server-side edits before pushing local updates.
            Invoke-RsyncCall -Mode $Mode -WslRuntimeDistro $WslRuntimeDistro -Source $remoteWithSlash -Destination $localWithSlash -SshCommand $SshCommand -UseDelete:$false -UseUpdate:$effectiveUseUpdate -UseDryRun:$UseDryRun -ExcludePath $ExcludePath
            Invoke-RsyncCall -Mode $Mode -WslRuntimeDistro $WslRuntimeDistro -Source $localWithSlash -Destination $remoteWithSlash -SshCommand $SshCommand -UseDelete:$UseDelete -UseUpdate:$effectiveUseUpdate -UseDryRun:$UseDryRun -ExcludePath $ExcludePath
        }
    }
}

$runtime = Resolve-RsyncRuntime -Distro $WslDistro
$remoteSpec = Build-RemoteSpec -User $RemoteUser -HostValue $RemoteHost -Path $RemotePath
$sshCommand = Build-SshCommand -Mode $runtime.Mode -Port $RemotePort -KeyPath $SshKeyPath

Write-Host "Sync mode: $Direction"
Write-Host "Rsync runtime: $($runtime.Mode)"
if ($runtime.Mode -eq "wsl") {
    Write-Host "WSL distro: $($runtime.Distro)"
}
Write-Host "Local path: $resolvedLocalPath"
Write-Host "Remote path: $remoteSpec"
if (Test-Path $resolvedExcludeFile) {
    Write-Host "Exclude file: $resolvedExcludeFile"
}
else {
    Write-Warning "Exclude file not found, syncing all files: $resolvedExcludeFile"
}
if ($ProtectReceiverNewer -or $Direction -eq "reconcile") {
    Write-Host "Safety: --update enabled (newer receiver files are not overwritten)."
}

if ($Watch) {
    Write-Host "Watch mode enabled. Interval: $IntervalSeconds second(s). Press Ctrl+C to stop."
    while ($true) {
        Invoke-Sync -Mode $runtime.Mode -WslRuntimeDistro $runtime.Distro -DirectionValue $Direction -Local $resolvedLocalPath -Remote $remoteSpec -SshCommand $sshCommand -UseDelete:$Delete -UseUpdate:$ProtectReceiverNewer -UseDryRun:$DryRun -ExcludePath $resolvedExcludeFile
        Start-Sleep -Seconds $IntervalSeconds
    }
}
else {
    Invoke-Sync -Mode $runtime.Mode -WslRuntimeDistro $runtime.Distro -DirectionValue $Direction -Local $resolvedLocalPath -Remote $remoteSpec -SshCommand $sshCommand -UseDelete:$Delete -UseUpdate:$ProtectReceiverNewer -UseDryRun:$DryRun -ExcludePath $resolvedExcludeFile
}

Write-Host "Sync completed successfully."