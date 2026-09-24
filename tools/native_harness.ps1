#Requires -Version 5.1
# The Windows half of the UI harness host: install Cura, seed its config,
# move the display, stage the plugin and the driver, start the simulator the
# plugin talks to, launch Cura and leave both running so tests/harness/runner.py
# can drive them.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools/native_harness.ps1 `
#       -CuraVersion 5.13.0 -Scenario scenario [options]
#
#   -CuraVersion VER  5.7.0 and up (the asset names hold across 5.7-5.13)
#   -Scenario NAME    what the runner runs once this returns: a runner mode
#                     (scenario | scenario1..11 | suite | firstinstall |
#                     migration | discover) or a suite group name, which is
#                     run as "suite <group>". "-" means the default scenario.
#   -Arch auto|x64    which installer to fetch. The Windows release publishes
#                     win64-X64 only, so anything else is refused.
#   -WorkDir DIR      scratch root (default %RUNNER_TEMP%\mpf-native)
#   -StageOnly        do everything except launching Cura
#
# Environment: HARNESS_GEOMETRY (1920x1080), HARNESS_WINDOW (1840x1040),
# MPF_WORK_DIR, RUNNER_TEMP. Every path this script owns lives under the
# work dir; nothing is written into the repository.
#
# The runner reaches the driver through <work-dir>\rpc, handed over as
# HARNESS_RPC_DIR: one variable, so the two sides cannot disagree about the
# path. The boot check also accepts the driver's port file at the driver's
# own default, and both places are cleared before the launch. The command to
# run is printed at the end, and the same variables are written to
# <work-dir>\harness_env.ps1.
#
# ASCII only: PowerShell 5.1 reads a BOM-less file as cp1252, and one high
# byte would corrupt the parse of everything after it.

[CmdletBinding()]
param(
    [string]$CuraVersion = '5.13.0',
    [string]$Scenario = '-',
    [string]$Arch = 'auto',
    [string]$WorkDir = '',
    [switch]$StageOnly
)

$ErrorActionPreference = 'Stop'
# Invoke-WebRequest and the choco bootstrap both need this before anything
# else runs; the default protocol on 5.1 is still TLS 1.0.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$script:LogFile = $null

function Write-Log([string]$Message) {
    Write-Host $Message
    if ($script:LogFile) { Add-Content -LiteralPath $script:LogFile -Value $Message -Encoding ASCII }
}

function Write-Warn([string]$Message) {
    # stderr, like the shell leg: visible interactively and in a CI log,
    # without a second copy on stdout.
    [Console]::Error.WriteLine($Message)
    if ($script:LogFile) { Add-Content -LiteralPath $script:LogFile -Value $Message -Encoding ASCII }
}

function Fail([string]$Message) {
    Write-Warn "native_harness: $Message"
    exit 1
}

# --- arguments and the tree ----------------------------------------------
# The argument checks come before the host check: a mistyped version is
# wrong on every host, and reporting it as one is the useful answer.
if ($CuraVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    Fail "'$CuraVersion' is not a version like 5.13.0"
}
$verParts = $CuraVersion.Split('.')
$verMajor = [int]$verParts[0]
$verMinor = [int]$verParts[1]
if ($verMajor -ne 5 -or $verMinor -lt 7 -or $verMinor -gt 13) {
    Fail "Cura $CuraVersion is outside the range this harness ranges over (5.7.0 to 5.13.0)"
}
$mm = "$verMajor.$verMinor"
switch ($Arch) {
    'auto' { }
    'x64' { }
    'x86' { Fail "the Windows release publishes win64-X64 only - there is no x86 installer to take" }
    'arm64' { Fail "the Windows release publishes win64-X64 only - there is no arm64 installer to take; x64 runs under emulation if this host is arm64" }
    default { Fail "-Arch must be auto or x64 (got '$Arch')" }
}
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    Fail "this script is the Windows leg and this is not Windows ($([System.Environment]::OSVersion.Platform))"
}

# The repo root: this script's own place in the tree first, so nothing here
# needs git, and git only as a fallback for a copied-out script.
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not (Test-Path -LiteralPath (Join-Path $Root 'package.json'))) {
    $Root = (git rev-parse --show-toplevel 2>$null)
    if (-not $Root) { Fail "not inside a git checkout and no package.json beside this script" }
}
$Root = (Resolve-Path -LiteralPath $Root).Path
Set-Location -LiteralPath $Root
$pkg = Get-Content -LiteralPath (Join-Path $Root 'package.json') -Raw
if ($pkg -notmatch '"package_version"\s*:\s*"([^"]+)"') { Fail "package.json carries no package_version" }
$PluginVersion = $Matches[1]

# One geometry for the whole run (the contract the Linux leg keeps): the
# screen SIZE, the capture size and the window pin all resolve from here,
# and the runner consumes them through the environment. The window pins
# SMALLER than the screen - headroom, so the screen-fits assertion has
# something to defend.
$Geometry = $env:HARNESS_GEOMETRY
if (-not $Geometry) { $Geometry = '1920x1080' }
$WindowPin = $env:HARNESS_WINDOW
if (-not $WindowPin) { $WindowPin = '1840x1040' }
$wantW = 0
$wantH = 0
if ($Geometry -match '^([0-9]+)x([0-9]+)$') {
    $wantW = [int]$Matches[1]
    $wantH = [int]$Matches[2]
} else {
    Fail "HARNESS_GEOMETRY '$Geometry' is not WxH"
}

if (-not $WorkDir) {
    $WorkDir = $env:MPF_WORK_DIR
    if (-not $WorkDir) {
        $tempRoot = $env:RUNNER_TEMP
        if (-not $tempRoot) { $tempRoot = [IO.Path]::GetTempPath() }
        $WorkDir = Join-Path $tempRoot 'mpf-native'
    }
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$WorkDir = (Resolve-Path -LiteralPath $WorkDir).Path
# The runner reads the driver's port and token from here.
$RpcDir = Join-Path $WorkDir 'rpc'
$LegacyRpcDir = '/tmp/mpf'
# The gallery root: every still and every video the runner writes goes
# under HARNESS_RUN_DIR, so this is the path the gate's upload has to find.
# It comes from the gate's own variable, RUN_DIR_NAME, resolved by the same
# rule tools/ui_test_paths.sh gives the Linux leg (an absolute name passes
# through, a relative one nests under the work dir). An explicit
# HARNESS_RUN_DIR wins: that is what the runner reads, and a caller that set
# it means it.
if ($env:HARNESS_RUN_DIR) {
    $ArtifactDir = $env:HARNESS_RUN_DIR
} elseif ($env:RUN_DIR_NAME) {
    if ($env:RUN_DIR_NAME -match '(^|[\\/])\.\.([\\/]|$)') {
        Fail "RUN_DIR_NAME must not name a parent directory: $($env:RUN_DIR_NAME)"
    }
    if ($env:RUN_DIR_NAME -match '^(/|[A-Za-z]:[\\/]|\\\\)') {
        $ArtifactDir = $env:RUN_DIR_NAME
    } else {
        $ArtifactDir = Join-Path (Join-Path $WorkDir 'ui-artifacts') $env:RUN_DIR_NAME
    }
} else {
    $ArtifactDir = Join-Path $WorkDir ('ui-artifacts\run-' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd-HHmmss'))
}
$script:LogFile = Join-Path $WorkDir 'native_harness.log'
# The installer is ~160 MB and regenerable from its pinned URL, so it goes
# to the runner's temp dir - never next to the evidence, never in an upload.
$tempRoot = $env:RUNNER_TEMP
if (-not $tempRoot) { $tempRoot = [IO.Path]::GetTempPath() }
$InstallerDir = Join-Path $tempRoot 'cura-installers'
New-Item -ItemType Directory -Force -Path $InstallerDir | Out-Null
New-Item -ItemType Directory -Force -Path $RpcDir | Out-Null
New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
$env:HARNESS_RPC_DIR = $RpcDir

# One run per machine at a time: a second run restages the config tree and
# kills the first run's Cura mid-scenario. A holder that died without
# releasing leaves a stale lock - its pid fails the liveness check and the
# lock is reclaimed.
$LockFile = Join-Path $WorkDir '.native_harness.lock'
if (Test-Path -LiteralPath $LockFile) {
    $holder = 0
    $raw = (Get-Content -LiteralPath $LockFile -First 1 -ErrorAction SilentlyContinue)
    if ($raw) { [void][int]::TryParse("$raw".Trim(), [ref]$holder) }
    if ($holder -gt 0 -and (Get-Process -Id $holder -ErrorAction SilentlyContinue)) {
        Fail "another run holds $LockFile (pid $holder)"
    }
    Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
}
"$PID" | Set-Content -LiteralPath $LockFile -Encoding ASCII

try {

$osText = [System.Environment]::OSVersion.VersionString
Write-Log "=== Windows native harness, $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) ==="
Write-Log "os            : $osText ($env:PROCESSOR_ARCHITECTURE)"
Write-Log "powershell    : $($PSVersionTable.PSVersion)"
Write-Log "cura version  : $CuraVersion"
Write-Log "plugin version: $PluginVersion"
Write-Log "scenario      : $Scenario"
Write-Log "work dir      : $WorkDir"
Write-Log "rpc dir       : $RpcDir"
Write-Log "geometry      : $Geometry (window pin $WindowPin)"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Log "elevated      : $admin"
if (-not $admin) {
    Write-Warn "native_harness: NOT ELEVATED - both Cura installers are per-machine, so an install that needs elevation can leave nothing behind; that alone would explain a silent install with no binary"
}

# --- 1. the installer -----------------------------------------------------
$Base = "https://github.com/Ultimaker/Cura/releases/download/$CuraVersion"
$ExeName = "UltiMaker-Cura-$CuraVersion-win64-X64.exe"
$MsiName = "UltiMaker-Cura-$CuraVersion-win64-X64.msi"
$ExeUrl = "$Base/$ExeName"
$MsiUrl = "$Base/$MsiName"
$ExePath = Join-Path $InstallerDir $ExeName
$MsiPath = Join-Path $InstallerDir $MsiName

# Is this file published? A HEAD on the public download URL answers that
# from the host the installer comes from, with no REST budget: the release
# listing is a GitHub API call, rate-limited per source IP, and one 403
# there used to cost a whole platform leg. --fail stays off - the status
# code IS the answer.
function Probe-Asset([string]$Url) {
    # Same rule as Get-CurlFile below: once a native command's stderr is
    # redirected it is an error record, and 'Stop' ends the script on the
    # first one - here it would kill the run before the retry loop and the
    # "refusing to guess" verdict it exists for. -s keeps curl quiet on
    # success; the redirect is only for the failure text, which must not
    # become the answer.
    $ErrorActionPreference = 'Continue'
    $code = ''
    foreach ($attempt in 1, 2, 3) {
        if ($attempt -gt 1) {
            Write-Log "  retrying in $((5 * ($attempt - 1)))s (last status: $code)"
            Start-Sleep -Seconds (5 * ($attempt - 1))
        }
        $raw = & curl.exe -sSI -o NUL -w '%{http_code}' --max-time 60 $Url 2>$null
        $code = "$raw".Trim()
        Write-Log "  $(Split-Path -Leaf $Url): HTTP $code (attempt $attempt)"
        if ($code -eq '200' -or $code -eq '302' -or $code -eq '404') { break }
    }
    if ($code -eq '200' -or $code -eq '302') { return 'published' }
    if ($code -eq '404') { return 'absent' }
    return 'unknown'
}

# A native command's stderr is a terminating error under
# $ErrorActionPreference = 'Stop' in PowerShell 5.1, and curl writes
# there (its progress meter, and its error text when a transfer
# fails). The download would die as a NativeCommandError before
# curl's own verdict was ever read - which is exactly how the first
# Windows leg ended. The preference is relaxed inside this function,
# where the assignment is function-scoped, so the caller's 'Stop'
# still stands, and the verdict is $LASTEXITCODE. -sS keeps the
# progress meter off the stream while leaving real errors on it.
function Get-CurlFile([string]$Url, [string]$Path) {
    $ErrorActionPreference = 'Continue'
    # The retry sequence is bounded as a whole, not per attempt: four
    # attempts at --max-time 900 could outlive the setup step's own
    # 30-minute timeout, which reports a step timeout and no reason.
    & curl.exe -sS -L --fail --retry 3 --retry-delay 5 --connect-timeout 30 `
        --max-time 300 --retry-max-time 600 -o $Path $Url 2>&1 |
        ForEach-Object { Write-Log "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "  curl exited $LASTEXITCODE fetching $(Split-Path -Leaf $Url)"
        return $false
    }
    return (Test-Path -LiteralPath $Path)
}

Write-Log ""
Write-Log "--- is each Windows installer published for ${CuraVersion}? ---"
$exeState = Probe-Asset $ExeUrl
$msiState = 'not probed'
$useExe = $false
$useMsi = $false
if ($exeState -eq 'published') {
    $useExe = $true
} elseif ($exeState -eq 'unknown') {
    Fail "the published-asset probe got no usable status for $ExeName (HTTP $exeState) - refusing to guess whether the URL exists"
} else {
    Write-Log "  the NSIS .exe is not published for this version; the MSI is the only installer left"
    $msiState = Probe-Asset $MsiUrl
    if ($msiState -eq 'published') { $useMsi = $true }
    elseif ($msiState -eq 'unknown') { Fail "the published-asset probe got no usable status for $MsiName" }
    else { Fail "neither $ExeName nor $MsiName is published for Cura $CuraVersion" }
}
if ($useExe) {
    if (-not (Get-CurlFile $ExeUrl $ExePath)) { Fail "could not download $ExeUrl" }
    $f = Get-Item -LiteralPath $ExePath
    Write-Log ("downloaded: {0} ({1:N1} MB)" -f $f.FullName, ($f.Length / 1MB))
    $sig = Get-AuthenticodeSignature -LiteralPath $ExePath
    Write-Log "signature : $($sig.Status) / signer $($sig.SignerCertificate.Subject)"
}

# --- 2. install -----------------------------------------------------------
# Both Windows installers are silent: NSIS takes /S, the WiX MSI takes
# msiexec /qn /norestart with APPLICATIONFOLDER pinning the target so the
# outcome is verifiable. A binary that appears nowhere after both is a
# failure to report with the exact reason, not something to retry blindly.
function Find-CuraBinary {
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $found = foreach ($root in $roots) {
        Get-ChildItem -LiteralPath $root -Filter '*cura*.exe' -Recurse -Depth 4 -File -ErrorAction SilentlyContinue
    }
    @($found) |
        Where-Object { $_.Name -notmatch '(?i)uninstall|curaengine' } |
        Sort-Object @{ Expression = { if ($_.Name -eq 'UltiMaker-Cura.exe') { 0 } else { 1 } } }, FullName |
        Select-Object -First 1
}

function Wait-CuraBinary([int]$LimitSecs) {
    # Start-Process waits only for the process it started, and both
    # installers finish through helpers that outlive it, so the wait polls
    # for the outcome instead of trusting the handle.
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $LimitSecs) {
        $b = Find-CuraBinary
        if ($b) { return $b }
        Start-Sleep -Seconds 5
    }
    return $null
}

function Msiexec-Meaning($Code) {
    switch ("$Code") {
        '0' { 'success' }
        '3010' { 'success, a restart is required' }
        '1641' { 'success, the installer asked for a restart' }
        '1603' { 'fatal error during installation - the msi log names it' }
        '1618' { 'another installation is already in progress' }
        '1619' { 'the package could not be opened' }
        '1620' { 'the package is not a valid installation package' }
        '1622' { 'the log file could not be opened' }
        '1625' { 'installation forbidden by system policy' }
        '1638' { 'another version of this product is already installed' }
        '1925' { 'insufficient privileges - elevation is required' }
        default { 'not a code this mapping knows' }
    }
}

Write-Log ""
Write-Log "--- installing Cura $CuraVersion ---"
$binary = Find-CuraBinary
if ($binary) {
    Write-Log "an existing install is on disk already: $($binary.FullName)"
    Write-Log "it is NOT reinstalled - a leftover binary whose version does not match would be the harness's problem, not this script's"
}

$exeResult = 'not attempted'
$msiResult = 'not attempted'
$msiLog = Join-Path $WorkDir 'msi-install.log'
if (-not $binary) {
    $exeResult = 'not attempted'
    if (-not $useExe) {
        $exeResult = 'the NSIS .exe is not published for this version'
    } else {
        $exeAbs = (Resolve-Path -LiteralPath $ExePath).Path
        Write-Log "NSIS : Start-Process '$exeAbs' /S"
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $p = $null
        try {
            $p = Start-Process -FilePath $exeAbs -ArgumentList '/S' -Wait -PassThru -ErrorAction Stop
        } catch {
            Write-Warn "the NSIS installer did not start: $($_.Exception.Message)"
        }
        $sw.Stop()
        if ($p) {
            $exeResult = "exit $($p.ExitCode) after $([int]$sw.Elapsed.TotalSeconds)s"
            Write-Log "NSIS exit code: $($p.ExitCode) (elapsed $([int]$sw.Elapsed.TotalSeconds)s)"
            if ($p.ExitCode -ne 0) {
                Write-Log "a non-zero exit here means the silent install did not run to completion"
            }
            $budget = 30
            if ($p.ExitCode -eq 0 -or $p.ExitCode -eq 1641 -or $p.ExitCode -eq 3010) { $budget = 240 }
            $binary = Wait-CuraBinary $budget
        } else {
            $exeResult = 'the process never started, so nothing was installed'
        }
    }

    if (-not $binary) {
        Write-Log "the NSIS attempt produced no binary ($exeResult); trying the MSI"
        if (-not $useMsi) {
            $msiState = Probe-Asset $MsiUrl
            if ($msiState -eq 'published') {
                $useMsi = $true
            } elseif ($msiState -eq 'unknown') {
                Fail "the published-asset probe got no usable status for $MsiName"
            }
        }
        if (-not $useMsi) {
            Fail "no Cura binary after the NSIS install (NSIS: $exeResult) and the MSI is not published for $CuraVersion"
        }
        if (-not (Test-Path -LiteralPath $MsiPath)) {
            if (-not (Get-CurlFile $MsiUrl $MsiPath)) { Fail "could not download $MsiUrl" }
        }
        $msiAbs = (Resolve-Path -LiteralPath $MsiPath).Path
        $msiLog = Join-Path $WorkDir 'msi-install.log'
        $target = Join-Path $env:ProgramFiles "UltiMaker Cura $CuraVersion"
        $msiArgs = "/i `"$msiAbs`" /qn /norestart /L*v `"$msiLog`" APPLICATIONFOLDER=`"$target`""
        Write-Log "MSI  : msiexec.exe $msiArgs"
        Write-Log "MSI target (APPLICATIONFOLDER): $target"
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $mp = $null
        try {
            $mp = Start-Process -FilePath msiexec.exe -ArgumentList $msiArgs -Wait -PassThru -ErrorAction Stop
        } catch {
            Write-Warn "msiexec did not start: $($_.Exception.Message)"
        }
        $sw.Stop()
        $msiResult = 'the msiexec process never started'
        if ($mp) {
            $msiResult = "exit $($mp.ExitCode) ($(Msiexec-Meaning $mp.ExitCode)) after $([int]$sw.Elapsed.TotalSeconds)s"
            Write-Log "MSI exit code: $msiResult"
            # The verbose log is the only place an MSI names its own cause.
            if (Test-Path -LiteralPath $msiLog) {
                $bad = @(Select-String -LiteralPath $msiLog -ErrorAction SilentlyContinue -Pattern 'Return value 3|Error [0-9]+|Internal Error|MainEngineThread is returning' |
                    Select-Object -Last 10)
                if ($bad.Count -gt 0) {
                    Write-Log "--- the msi log lines that name a failure ---"
                    $bad | ForEach-Object { Write-Log "  $($_.Line.Trim())" }
                }
            } else {
                Write-Log "no msi log at $msiLog - msiexec never reached the point of writing one"
            }
            $budget = 30
            if ($mp.ExitCode -eq 0 -or $mp.ExitCode -eq 1641 -or $mp.ExitCode -eq 3010) { $budget = 240 }
            $binary = Wait-CuraBinary $budget
        }
    }

    if (-not $binary) {
        Write-Warn "no Cura binary was installed: NSIS $exeResult; MSI $msiResult; elevated=$admin"
        Write-Warn "searched for *cura*.exe (depth 4) under $($env:ProgramFiles), ${env:ProgramFiles(x86)} and $($env:LOCALAPPDATA)"
        Write-Warn "msi log: $(if (Test-Path -LiteralPath $msiLog) { $msiLog } else { 'never written' })"
        Fail "the install did not produce a Cura executable"
    }
}
$binary = (Get-Item -LiteralPath $binary.FullName)
$curaDir = Split-Path -Parent $binary.FullName
Write-Log "installed binary: $($binary.FullName) (version $($binary.VersionInfo.FileVersion))"
Write-Log "install dir     : $curaDir"

# --- 3. tools this host does not necessarily have -------------------------
function Refresh-Path {
    # An installer writes the machine PATH for later processes, not for this
    # one, so the two roots are read back and this process's copy rebuilt -
    # APPENDED to, not replaced by: the job's own PATH is in neither
    # registry value, and the python this job runs on comes from the
    # toolcache on $env:GITHUB_PATH, so substituting took the interpreter
    # away and left the seed step calling a name that no longer resolved.
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user;$env:Path"
}

# The WindowsApps alias stub answers --version on stderr, and that is also
# the redirect which turns a native command's stderr into a terminating
# error under 'Stop' - so the probe has to survive the very answer this
# branch exists to catch. Function-scoped, so the caller's preference
# stands.
function Get-PythonVersion([string]$Exe) {
    $ErrorActionPreference = 'Continue'
    return ((& $Exe --version 2>&1) -join ' ').Trim()
}

# The simulator's dependency and its readiness probe. Both answer by state,
# and both relax the preference for the same reason: pip's progress and a
# missing module's traceback arrive on stderr, which the redirect turns into
# a terminating error under 'Stop'. Relaxed inside the functions only.
#
# Both take the interpreter explicitly and read it from the same site
# directory the launch puts on PYTHONPATH. A bare 'python' resolves through
# PATH to a different interpreter than the one that serves, and a bare pip
# install reported success into a site that interpreter did not search - the
# module was installed, the exit code was 0, and every leg still died on the
# simulator's first import.
function Test-PyModule([string]$Exe, [string]$Module, [string]$Site = '') {
    $ErrorActionPreference = 'Continue'
    $prev = $env:PYTHONPATH
    if ($Site) { $env:PYTHONPATH = $Site }
    try {
        & $Exe -c "import $Module" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        if ($Site) {
            if ($null -ne $prev) { $env:PYTHONPATH = $prev }
            else { Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue }
        }
    }
}

# Unquiet on purpose: on failure pip's last lines carry the reason, and on
# success they show what was written where. The exit code is logged either
# way rather than inferred.
function Install-PyModule([string]$Exe, [string]$Module, [string]$Site) {
    $ErrorActionPreference = 'Continue'
    & $Exe -m pip install --disable-pip-version-check --target $Site $Module 2>&1 |
        Select-Object -Last 4 | ForEach-Object { Write-Log "  $_" }
    $rc = $LASTEXITCODE
    Write-Log "pip install $Module -> exit $rc (target $Site)"
    return ($rc -eq 0)
}

function Test-Simulator([int]$Port) {
    # A .NET request rather than Invoke-WebRequest: a refused connection is
    # the expected answer here, and this turns it into $false without the
    # cmdlet's error records.
    try {
        $req = [System.Net.WebRequest]::Create("http://127.0.0.1:$Port/ledger")
        $req.Timeout = 2000
        $res = $req.GetResponse()
        $res.Close()
        return $true
    } catch {
        return $false
    }
}

Write-Log ""
Write-Log "--- the tools the rest of the run needs ---"
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Log "chocolatey: absent - installing it (the installer everything else comes from)"
    try {
        $chocoScript = (New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1')
        Invoke-Expression $chocoScript
        Refresh-Path
    } catch {
        Write-Warn "the chocolatey bootstrap failed: $($_.Exception.Message)"
    }
}
if (Get-Command choco -ErrorAction SilentlyContinue) {
    Write-Log "chocolatey: $((Get-Command choco).Source)"
} else {
    Write-Warn "chocolatey: still absent - anything missing from this image stays missing"
}

function Provision-Choco([string]$Command, [string]$Package) {
    $c = Get-Command $Command -ErrorAction SilentlyContinue
    if ($c) {
        Write-Log "${Command}: $($c.Source)"
        return $c
    }
    if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
        Write-Warn "$Command : ABSENT and there is no install path (no chocolatey on this image)"
        return $null
    }
    Write-Log "$Command : absent - provisioning it (choco install $Package)"
    # Chocolatey and the installers it drives both write to stderr; under
    # 'Stop' the first such line would end the script mid-install, as a
    # NativeCommandError instead of the warnings below. The verdict is
    # $LASTEXITCODE, which the pipeline leaves alone.
    $ErrorActionPreference = 'Continue'
    & choco install $Package -y --no-progress 2>&1 | Select-Object -Last 6 | ForEach-Object { Write-Log "  $_" }
    Write-Log "choco install $Package exit: $LASTEXITCODE"
    Refresh-Path
    $c = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $c) { Write-Warn "choco install $Package did not put $Command on PATH (exit $LASTEXITCODE)" }
    return $c
}

# python is what runs tests/harness/runner.py. A WindowsApps alias stub
# answers Get-Command but no --version, and must not count as present.
$pythonOk = $false
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $pyVer = Get-PythonVersion 'python'
    if ($pyVer) {
        Write-Log "python: $pyVer ($($py.Source))"
        $pythonOk = $true
    } else {
        Write-Log "python at $($py.Source) answers no --version (a WindowsApps alias stub) - provisioning a real one"
    }
}
if (-not $pythonOk) {
    $null = Provision-Choco 'python' 'python3'
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $pyVer = Get-PythonVersion 'python'
        if ($pyVer) {
            Write-Log "python: $pyVer ($($py.Source))"
            $pythonOk = $true
        }
    }
}
if (-not $pythonOk) { Write-Warn "no working python: tests/harness/runner.py cannot run on this host" }

# ffmpeg is the harness's recorder and its still capture (runner.py).
$null = Provision-Choco 'ffmpeg' 'ffmpeg'
$null = Provision-Choco '7z' '7zip'

# --- 4. software OpenGL: Mesa llvmpipe ------------------------------------
# No third-party library is assumed present: the rasteriser is installed at
# a pinned version with the digest verified before anything uses it. Cura
# calls AA_UseDesktopOpenGL, so the GL it loads is the system opengl32.dll
# and nothing else - QT_OPENGL_DLL is blanked by Cura's own cura_app.py
# before Qt reads it - which is why Mesa is deployed there.
$MesaVersion = '26.1.8'
$MesaSha256 = '4c6d32e653e0ff9ad07796e40c0bcfabf2764d849e3ce4f3b1590112c87e42f9'
$MesaAsset = "mesa3d-$MesaVersion-release-msvc.7z"
$MesaUrl = "https://github.com/pal1000/mesa-dist-win/releases/download/$MesaVersion/$MesaAsset"
$MesaRoot = Join-Path $tempRoot "mesa-llvmpipe-$MesaVersion"
$MesaDir = Join-Path $MesaRoot 'x64'
$MesaArchive = Join-Path $MesaRoot $MesaAsset
$MesaFiles = @('opengl32.dll', 'libgallium_wgl.dll', 'dxil.dll')
$mesaOk = $false
$mesaWhy = 'not attempted'

# Both extractors report progress and warnings on stderr, which the redirect
# below turns into error records: under 'Stop' the extraction would end the
# script instead of reaching the member check that decides whether Mesa is
# usable. The verdict is what is on disk, so the preference is relaxed
# inside this function only.
function Expand-MesaArchive([string]$Tool, [string]$Archive, [string]$Target) {
    $ErrorActionPreference = 'Continue'
    if ($Tool -eq '7z') {
        & 7z x -y ("-o" + $Target) $Archive 'x64/opengl32.dll' 'x64/libgallium_wgl.dll' 'x64/dxil.dll' 2>&1 |
            Select-Object -Last 4 | ForEach-Object { Write-Log "  $_" }
    } else {
        & tar.exe -xf $Archive -C $Target 'x64/opengl32.dll' 'x64/libgallium_wgl.dll' 'x64/dxil.dll' 2>&1 |
            Select-Object -Last 4 | ForEach-Object { Write-Log "  $_" }
    }
}

Write-Log ""
Write-Log "--- Mesa llvmpipe $MesaVersion (the software OpenGL Cura needs) ---"
New-Item -ItemType Directory -Force -Path $MesaRoot | Out-Null
if (-not (Test-Path -LiteralPath $MesaArchive)) {
    if (-not (Get-CurlFile $MesaUrl $MesaArchive)) { Write-Warn "Mesa download failed: the archive is not on disk (the curl exit code is logged above)" }
}
$mesaHash = ''
if (Test-Path -LiteralPath $MesaArchive) {
    $mesaHash = (Get-FileHash -LiteralPath $MesaArchive -Algorithm SHA256).Hash.ToLower()
    Write-Log ("archive: {0:N0} bytes, sha256 {1}" -f (Get-Item -LiteralPath $MesaArchive).Length, $mesaHash)
}
if ($mesaHash -eq $MesaSha256) {
    $extractTool = $null
    if (Get-Command 7z -ErrorAction SilentlyContinue) {
        $extractTool = '7z'
        Expand-MesaArchive '7z' $MesaArchive $MesaRoot
    } elseif (Get-Command tar.exe -ErrorAction SilentlyContinue) {
        # bsdtar reads 7z through libarchive; the members are named by path.
        $extractTool = 'tar.exe'
        Expand-MesaArchive 'tar.exe' $MesaArchive $MesaRoot
    }
    $missing = @($MesaFiles[0..1] | Where-Object { -not (Test-Path -LiteralPath (Join-Path $MesaDir $_)) })
    if ($extractTool -and $missing.Count -eq 0) {
        $mesaOk = $true
        $mesaWhy = "extracted with $extractTool"
    } else {
        $mesaWhy = "the archive could not be unpacked (extractor '$extractTool', missing: $($missing -join ', '))"
    }
} elseif ($mesaHash) {
    $mesaWhy = 'the archive hash does not match the pin - it was not unpacked'
} else {
    $mesaWhy = 'the download produced no archive'
}

$glVars = [ordered]@{
    'QT_OPENGL'               = 'software'
    # opengl, not software: the scene graph's software backend renders to a
    # CPU buffer with no GL context, and Cura's renderer has nothing to
    # initialise against there. llvmpipe is the GL that makes it work.
    'QSG_RHI_BACKEND'         = 'opengl'
    'QT_QUICK_BACKEND'        = 'opengl'
    'QT_LOGGING_RULES'        = 'qt.qpa.gl=true'
    'QT_FORCE_STDERR_LOGGING' = '1'
    'GALLIUM_DRIVER'          = 'llvmpipe'
    'LIBGL_ALWAYS_SOFTWARE'   = '1'
}
if ($mesaOk) {
    # PATH, because Mesa's loader resolves libgallium_wgl.dll by bare name,
    # and beside the executable, because that is where a loader looks first.
    $env:Path = "$MesaDir;$env:Path"
    foreach ($f in $MesaFiles) {
        $src = Join-Path $MesaDir $f
        if (-not (Test-Path -LiteralPath $src)) { continue }
        try {
            Copy-Item -LiteralPath $src -Destination (Join-Path $curaDir $f) -Force -ErrorAction Stop
        } catch {
            Write-Warn "could not place $f beside Cura's executable: $($_.Exception.Message)"
        }
    }
    # System32 is the file Cura's desktop GL path actually loads: it calls
    # AA_UseDesktopOpenGL and blanks QT_OPENGL_DLL before PyQt6 is imported,
    # so neither the environment nor a copy beside the executable is consulted.
    # That file is owned by TrustedInstaller, and a plain copy is refused
    # (the first live Windows leg got "Access to the path is denied" and Cura
    # then hung on GDI Generic OpenGL 1.1 with a dialog); ownership and a
    # write grant come first, then the bytes, verified by hash rather than
    # assumed.
    $sysGl = Join-Path $env:SystemRoot 'System32\opengl32.dll'
    $mesaGl = Join-Path $MesaDir 'opengl32.dll'
    if (Test-Path -LiteralPath $sysGl) {
        $mesaHash = (Get-FileHash -LiteralPath $mesaGl -Algorithm SHA256).Hash.ToLower()
        $deployedHash = ''
        try {
            & takeown.exe /f "$sysGl" 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Log "  $_" }
            Write-Log "takeown exit: $LASTEXITCODE"
            & icacls.exe "$sysGl" /grant '*S-1-5-32-544:F' 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Log "  $_" }
            Write-Log "icacls exit: $LASTEXITCODE"
            Copy-Item -LiteralPath $mesaGl -Destination $sysGl -Force -ErrorAction Stop
            Write-Log "placed system-wide: $sysGl"
            foreach ($f in @('libgallium_wgl.dll', 'dxil.dll')) {
                $src = Join-Path $MesaDir $f
                if (-not (Test-Path -LiteralPath $src)) { continue }
                try {
                    Copy-Item -LiteralPath $src -Destination (Join-Path $env:SystemRoot ('System32\' + $f)) -Force -ErrorAction Stop
                    Write-Log "placed system-wide: $env:SystemRoot\System32\$f"
                } catch {
                    Write-Warn "could not place System32\$f ($($_.Exception.Message)) - Mesa resolves it beside Cura's executable or on PATH instead"
                }
            }
            $deployedHash = (Get-FileHash -LiteralPath $sysGl -Algorithm SHA256).Hash.ToLower()
            if ($deployedHash -eq $mesaHash) {
                $mesaWhy = "$mesaWhy; Mesa is deployed as System32\opengl32.dll (sha256 verified)"
            } else {
                $mesaWhy = "$mesaWhy; the System32 replacement is NOT Mesa (sha256 $deployedHash)"
            }
        } catch {
            Write-Warn "System32\opengl32.dll could not be replaced ($($_.Exception.Message)) - Mesa is beside Cura's executable and on PATH instead"
            $mesaWhy = "$mesaWhy; System32 deployment refused: $($_.Exception.Message)"
        }
    }
    Write-Log "Mesa: $mesaWhy"
} else {
    Write-Warn "Mesa llvmpipe is NOT available: $mesaWhy"
    Write-Warn "Cura will fall back to whatever opengl32.dll this image has, which has failed to probe on a GPU-less runner"
}

# --- 5. the display -------------------------------------------------------
# The harness resolves ONE screen size and pins the window INSIDE it, and
# the capture is the whole display: a display smaller than the pinned
# geometry breaks both at once, so it is read, moved and read again. The
# reads never go through [System.Windows.Forms.Screen]::PrimaryScreen - it
# is cached for the process lifetime and went on reporting the old size
# after the display really had moved.
Write-Log ""
Write-Log "--- display geometry (the harness pins one screen size) ---"
Add-Type -AssemblyName System.Windows.Forms | Out-Null

function Get-DisplayNow {
    # The fallback below is a cmdlet whose failure is terminating under
    # 'Stop', and this function's answer is a size or 'unreadable' - never
    # the error. Relaxed here so the fallback can be tried rather than
    # ending the script on the image where it is not installed.
    $ErrorActionPreference = 'Continue'
    $vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
    if (($vs.Width -gt 0) -and ($vs.Height -gt 0)) {
        return [pscustomobject]@{ Width = $vs.Width; Height = $vs.Height; Source = 'SystemInformation.VirtualScreen' }
    }
    $g = Get-Command Get-DisplayResolution -ErrorAction SilentlyContinue
    if ($g) {
        # Get-DisplayResolution can arrive as single characters ("1 0 2 4 x
        # 7 6 8"); whitespace is stripped so how they are joined cannot
        # matter, and this is only ever the fallback.
        $line = (((Get-DisplayResolution 2>$null) -join '') -replace '\s', '')
        if ($line -match '([0-9]+)[^0-9]+([0-9]+)') {
            return [pscustomobject]@{ Width = [int]$Matches[1]; Height = [int]$Matches[2]; Source = 'Get-DisplayResolution' }
        }
    }
    return [pscustomobject]@{ Width = 0; Height = 0; Source = 'unreadable' }
}

# The one Windows API call with no managed equivalent that this script
# needs: an explicit display mode, for an image where the cmdlet is absent
# or refuses. Structs stay TOP-LEVEL: a nested type is addressed by its CLR
# name, so the dotted spelling returns nothing and every [ref] then fails
# to convert - silently, which is what an empty DEVMODE once looked like.
$nativeSrc = @'
using System;
using System.Text;
using System.Runtime.InteropServices;

[StructLayout(LayoutKind.Sequential)]
public struct MpfRect { public int Left; public int Top; public int Right; public int Bottom; }

[StructLayout(LayoutKind.Sequential)]
public struct MpfPoint { public int X; public int Y; }

public delegate bool MpfEnumProc(IntPtr hwnd, IntPtr lparam);

// Windows defines DEVMODE as 220 bytes: the Unicode layout, both fixed
// character arrays as ByValTStr of CCHDEVICENAME/CCHFORMNAME wide
// characters, and the mode union as four words. DevModeOk() measures the
// result, and the call is only made when it matches.
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode, Pack = 8)]
public struct MpfDevMode {
  [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
  public ushort dmSpecVersion;
  public ushort dmDriverVersion;
  public ushort dmSize;
  public ushort dmDriverExtra;
  public uint dmFields;
  public int dmUnionWord0;
  public int dmUnionWord1;
  public int dmUnionWord2;
  public int dmUnionWord3;
  public short dmColor;
  public short dmDuplex;
  public short dmYResolution;
  public short dmTTOption;
  public short dmCollate;
  [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
  public ushort dmLogPixels;
  public uint dmBitsPerPel;
  public uint dmPelsWidth;
  public uint dmPelsHeight;
  public uint dmDisplayFlags;
  public uint dmDisplayFrequency;
  public uint dmICMMethod;
  public uint dmICMIntent;
  public uint dmMediaType;
  public uint dmDitherType;
  public uint dmReserved1;
  public uint dmReserved2;
  public uint dmPanningWidth;
  public uint dmPanningHeight;
}

public static class MpfNative {
  private static IntPtr pickHandle = IntPtr.Zero;
  private static int pickScore = -1;
  private static uint pickPid = 0;
  private static string pickHint = "";

  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool GetWindowRect(IntPtr hwnd, out MpfRect rect);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool IsIconic(IntPtr hwnd);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool SetForegroundWindow(IntPtr hwnd);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool ShowWindow(IntPtr hwnd, int cmd);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern IntPtr WindowFromPoint(MpfPoint point);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool EnumWindows(MpfEnumProc callback, IntPtr lparam);
  [DllImport("user32.dll", EntryPoint = "GetWindowTextLengthW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern int GetWindowTextLength(IntPtr hwnd);
  [DllImport("user32.dll", EntryPoint = "GetWindowTextW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int max);
  [DllImport("user32.dll", EntryPoint = "GetClassNameW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern int GetClassName(IntPtr hwnd, StringBuilder text, int max);
  // A window that is not composited has no extended frame bounds; the call
  // then fails and GetWindowRect is the right answer.
  [DllImport("dwmapi.dll")]
  public static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out MpfRect value, int size);
  [DllImport("user32.dll", EntryPoint = "EnumDisplaySettingsW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool EnumDisplaySettings(string device, int mode, ref MpfDevMode devmode);
  [DllImport("user32.dll", EntryPoint = "ChangeDisplaySettingsW", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern int ChangeDisplaySettings(ref MpfDevMode devmode, int flags);

  public static string DevModeLayout() {
    Type t = typeof(MpfDevMode);
    return string.Format("size={0} dmSize@{1} dmFields@{2} dmPelsWidth@{3} dmPelsHeight@{4}",
      Marshal.SizeOf(t), Marshal.OffsetOf(t, "dmSize"), Marshal.OffsetOf(t, "dmFields"),
      Marshal.OffsetOf(t, "dmPelsWidth"), Marshal.OffsetOf(t, "dmPelsHeight"));
  }

  public static bool DevModeOk() {
    Type t = typeof(MpfDevMode);
    return Marshal.SizeOf(t) == 220 && (int)Marshal.OffsetOf(t, "dmSize") == 68 &&
      (int)Marshal.OffsetOf(t, "dmFields") == 72 && (int)Marshal.OffsetOf(t, "dmPelsWidth") == 172 &&
      (int)Marshal.OffsetOf(t, "dmPelsHeight") == 176;
  }

  public static string DevModeText(MpfDevMode dm) {
    return string.Format("{0}x{1} {2}bpp {3}Hz fields=0x{4:X}", dm.dmPelsWidth, dm.dmPelsHeight,
      dm.dmBitsPerPel, dm.dmDisplayFrequency, dm.dmFields);
  }

  public static string WindowTitle(IntPtr hwnd) {
    int n = GetWindowTextLength(hwnd);
    StringBuilder sb = new StringBuilder(n + 2);
    GetWindowText(hwnd, sb, sb.Capacity);
    return sb.ToString();
  }

  public static string WindowClass(IntPtr hwnd) {
    StringBuilder sb = new StringBuilder(256);
    GetClassName(hwnd, sb, sb.Capacity);
    return sb.ToString();
  }

  public static uint WindowPid(IntPtr hwnd) { uint pid; GetWindowThreadProcessId(hwnd, out pid); return pid; }

  public static bool FrameRect(IntPtr hwnd, out MpfRect rect) {
    rect = new MpfRect();
    if (hwnd == IntPtr.Zero) { return false; }
    try {
      return DwmGetWindowAttribute(hwnd, 9, out rect, Marshal.SizeOf(typeof(MpfRect))) == 0;
    } catch {
      rect = new MpfRect();
      return false;
    }
  }

  private static bool PickCallback(IntPtr hwnd, IntPtr lparam) {
    if (!IsWindowVisible(hwnd)) { return true; }
    string title = WindowTitle(hwnd);
    if (title.Length == 0) { return true; }
    int score = 0;
    if (WindowPid(hwnd) == pickPid) { score += 10; }
    if (pickHint.Length > 0 && title.IndexOf(pickHint, StringComparison.OrdinalIgnoreCase) >= 0) { score += 100; }
    if (title.IndexOf("Cura", StringComparison.OrdinalIgnoreCase) >= 0) { score += 20; }
    if (score > pickScore) { pickScore = score; pickHandle = hwnd; }
    return true;
  }

  // The handle a launched pid reports is not always the app's window (a
  // launcher can own it), so the top-level window is chosen by score
  // instead of taken on trust.
  public static IntPtr WindowPick(uint pid, string titleHint) {
    pickHandle = IntPtr.Zero;
    pickScore = -1;
    pickPid = pid;
    pickHint = (titleHint == null) ? "" : titleHint;
    EnumWindows(PickCallback, IntPtr.Zero);
    return pickHandle;
  }
}
'@

$nativeOk = $false
try {
    Add-Type -TypeDefinition $nativeSrc -ErrorAction Stop
    $nativeOk = $true
} catch {
    Write-Warn "the native helper did not compile, so the display cannot be moved by the API route and the window cannot be measured: $($_.Exception.Message)"
}

$scr = Get-DisplayNow
Write-Log ("display before: {0}x{1} (from {2})" -f $scr.Width, $scr.Height, $scr.Source)
Write-Log "the harness geometry this run is pinned to: $Geometry"
$displayWhy = 'not attempted'
$atGeometry = (($scr.Width -eq $wantW) -and ($scr.Height -eq $wantH))
if (-not $atGeometry) {
    $setCmd = Get-Command Set-DisplayResolution -ErrorAction SilentlyContinue
    if ($setCmd) {
        try {
            Set-DisplayResolution -Width $wantW -Height $wantH -Force -ErrorAction Stop
            Start-Sleep -Seconds 3
            $scr = Get-DisplayNow
            Write-Log ("display after: {0}x{1} (from {2})" -f $scr.Width, $scr.Height, $scr.Source)
            if (($scr.Width -eq $wantW) -and ($scr.Height -eq $wantH)) {
                $atGeometry = $true
                $displayWhy = 'moved by Set-DisplayResolution'
            } else {
                $displayWhy = "Set-DisplayResolution returned but the screen reports $($scr.Width)x$($scr.Height)"
            }
        } catch {
            $displayWhy = "Set-DisplayResolution failed: $($_.Exception.Message)"
            Write-Warn $displayWhy
        }
    } else {
        $displayWhy = 'Set-DisplayResolution is not on this image'
    }
} else {
    $displayWhy = 'already at the pinned geometry'
}

if (-not $atGeometry) {
    if (-not $nativeOk) {
        $displayWhy = "$displayWhy, and the native helper did not compile, so no mode can be set"
    } else {
        # The DEVMODE route: enumerate the adapter's modes, take the first
        # that is the wanted size at 32 bpp or better, and ask for it.
        $dmNow = New-Object MpfDevMode
        if ($null -eq $dmNow) {
            $displayWhy = "$displayWhy, and the DEVMODE type did not resolve in PowerShell"
        } elseif (-not [MpfNative]::DevModeOk()) {
            # Handing the API a struct of the wrong size is worse than not
            # calling it: Windows defines DEVMODE as 220 bytes.
            $displayWhy = "$displayWhy, and the DEVMODE this helper marshals is not the Windows layout ($([MpfNative]::DevModeLayout()))"
        } else {
            $found = $null
            $modes = 0
            $i = 0
            while ($i -lt 4096) {
                $cand = New-Object MpfDevMode
                # EnumDisplaySettings requires dmSize to hold sizeof(DEVMODE)
                # on entry and returns FALSE at once when it is zero, so a
                # zeroed struct reports an adapter with no modes at all.
                $cand.dmSize = 220
                $cand.dmDriverExtra = 0
                if (-not [MpfNative]::EnumDisplaySettings($null, $i, [ref]$cand)) { break }
                $modes++
                if (($cand.dmPelsWidth -eq $wantW) -and ($cand.dmPelsHeight -eq $wantH) -and ($cand.dmBitsPerPel -ge 32)) {
                    $found = $cand
                    break
                }
                $i++
            }
            Write-Log "display modes this adapter reports: $modes (looking for ${wantW}x${wantH} at 32 bpp)"
            if (-not $found) {
                $displayWhy = "$displayWhy, and this adapter has no ${wantW}x${wantH} mode"
            } else {
                # DM_BITSPERPEL | DM_PELSWIDTH | DM_PELSHEIGHT |
                # DM_DISPLAYFREQUENCY, as the spike's verified call set them:
                # ChangeDisplaySettings applies only the members its dmFields
                # names, so without this the call can succeed as a no-op.
                $found.dmFields = 0x00040000 -bor 0x00080000 -bor 0x00100000 -bor 0x00400000
                $rc = [MpfNative]::ChangeDisplaySettings([ref]$found, 0)
                Write-Log ("ChangeDisplaySettings({0}) returned {1} (0 = DISP_CHANGE_SUCCESSFUL)" -f [MpfNative]::DevModeText($found), $rc)
                Start-Sleep -Seconds 3
                $scr = Get-DisplayNow
                Write-Log ("display after: {0}x{1} (from {2})" -f $scr.Width, $scr.Height, $scr.Source)
                if (($scr.Width -eq $wantW) -and ($scr.Height -eq $wantH)) {
                    $atGeometry = $true
                    $displayWhy = 'moved by ChangeDisplaySettings'
                } else {
                    $displayWhy = "$displayWhy, and ChangeDisplaySettings returned $rc"
                }
            }
        }
    }
}
if ($atGeometry) {
    Write-Log "display at the pinned geometry: $displayWhy"
} else {
    Write-Warn "native_harness: this host's display is $($scr.Width)x$($scr.Height), not the pinned $Geometry ($displayWhy)"
    Write-Warn "the capture comes out at this host's size and the pinned window cannot fit it - the window is shrunk below instead, so the frame is complete but smaller than the Linux leg's"
}

# --- 6. seed Cura's config ------------------------------------------------
# runner.py's first gate asserts the Welcome wizard absent, and Cura puts it
# up exactly when there is no active machine (shouldShowWelcomeDialog is
# "activeMachine is None" on every version this harness ranges over). The
# machine comes from the SHARED fixture the Linux gate seeds from - same
# files, same build volume, one source of truth - and the file-format
# versions are read out of the installed build, never assumed: a container
# written with the wrong version is refused outright and the machine then
# vanishes, which would fail every scenario at once.
Write-Log ""
Write-Log "--- seeding Cura's config ---"

function Find-Under([string]$Root, [string]$Name, [string]$ParentName) {
    $hit = Get-ChildItem -LiteralPath $Root -Recurse -Depth 6 -File -Filter $Name -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -eq $ParentName } | Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return ''
}

function Get-VersionConst([string]$Path, [string]$Name) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return '' }
    $m = Select-String -LiteralPath $Path -Pattern ("^\s*" + $Name + "\s*=\s*([0-9]+)") -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return ''
}

$prefVer = Get-VersionConst (Find-Under $curaDir 'Preferences.py' 'UM') 'Version'
$csVer = Get-VersionConst (Find-Under $curaDir 'ContainerStack.py' 'Settings') 'Version'
$icVer = Get-VersionConst (Find-Under $curaDir 'InstanceContainer.py' 'Settings') 'Version'
$setVer = Get-VersionConst (Find-Under $curaDir 'CuraApplication.py' 'cura') 'SettingVersion'
Write-Log "file-format versions read out of the installed build: preferences=$prefVer stack=$csVer instance=$icVer setting=$setVer"
if (-not $prefVer -or -not $csVer -or -not $icVer -or -not $setVer) {
    Fail "the installed build did not give up its file-format versions, so nothing can be seeded and Cura would boot on the wizard"
}

$ConfigDir = Join-Path (Join-Path $env:APPDATA 'cura') $mm
$FixtureDir = Join-Path $Root "tests\harness\config\cura\$mm"
if (-not (Test-Path -LiteralPath (Join-Path $FixtureDir 'machine_instances'))) {
    # The fixture is written for 5.13 (its own directory name); another
    # version takes the same files, since every version line is rewritten
    # from the installed build below.
    $FixtureDir = Join-Path $Root 'tests\harness\config\cura\5.13'
}
if (-not (Test-Path -LiteralPath (Join-Path $FixtureDir 'machine_instances'))) {
    Fail "the shared machine seed fixture is missing (looked for cura\$mm and cura\5.13 under $Root\tests\harness\config)"
}
# The plugin's own seeded state - its per-machine json, its settings and the
# migration flags - rides the config-root fixture the Linux gate seeds. Here
# the config root and the storage root are one directory (%APPDATA%\cura\<v>),
# which is why a single target serves both halves of that fixture.
$PrefFixtureDir = Join-Path $Root "tests\harness\config\config\cura\$mm"
if (-not (Test-Path -LiteralPath (Join-Path $PrefFixtureDir 'cura.cfg'))) {
    $PrefFixtureDir = Join-Path $Root 'tests\harness\config\config\cura\5.13'
}
if (-not (Test-Path -LiteralPath (Join-Path $PrefFixtureDir 'cura.cfg'))) {
    Fail "the shared preferences seed fixture is missing (looked for config\cura\$mm and config\cura\5.13 under $Root\tests\harness\config)"
}
# A fresh tree every run: a config left by an earlier run carries the
# plugin's own state, and the harness is meant to boot from a known state
# rather than from whatever the last run wrote.
if (Test-Path -LiteralPath $ConfigDir) { Remove-Item -LiteralPath $ConfigDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
Copy-Item -Path (Join-Path $PrefFixtureDir '*') -Destination $ConfigDir -Recurse -Force
Copy-Item -Path (Join-Path $FixtureDir '*') -Destination $ConfigDir -Recurse -Force

$seedFiles = 0
$machine = ''
foreach ($seedPath in @(Get-ChildItem -LiteralPath $ConfigDir -Recurse -File -Filter '*.cfg' -ErrorAction SilentlyContinue)) {
    $leaf = $seedPath.Directory.Name
    if ($seedPath.Name -eq 'cura.cfg') { continue }
    if ($leaf -eq 'machine_instances' -or $leaf -eq 'extruders') { $seedVer = $csVer } else { $seedVer = $icVer }
    $text = Get-Content -LiteralPath $seedPath.FullName -Raw
    $text = $text -replace '(?m)^version = .*$', "version = $seedVer"
    $text = $text -replace '(?m)^setting_version = .*$', "setting_version = $setVer"
    Set-Content -LiteralPath $seedPath.FullName -Value $text -Encoding ASCII -NoNewline
    if ($leaf -eq 'machine_instances' -and $seedPath.Name -like '*.global.cfg') {
        if ($text -match '(?m)^id = (.+)$') { $machine = $Matches[1].Trim() }
        if (-not $machine) { $machine = ($seedPath.Name -replace '\.global\.cfg$', '').Replace('+', ' ') }
    }
    $seedFiles++
}
if (-not $machine) { Fail "the machine seed fixture carries no machine_instances/*.global.cfg" }
# cura.cfg comes from the fixture, so only the lines this run owns are
# rewritten and the plugin state and section set the Linux gate seeds stand.
# Nothing is dropped: this platform has no local-network consent prompt, so a
# disabled_plugins line is only ever the fixture's own.
$CfgPath = Join-Path $ConfigDir 'cura.cfg'
$cfgOut = New-Object System.Collections.Generic.List[string]
$cfgSec = ''
$sawCuraSection = $false
$sawActive = $false
foreach ($line in @(Get-Content -LiteralPath $CfgPath)) {
    if ($line -match '^\[') {
        $cfgSec = $line.Trim()
        if ($cfgSec -eq '[cura]') { $sawCuraSection = $true }
        $cfgOut.Add($line)
        continue
    }
    if ($cfgSec -eq '[general]' -and $line -match '^version = ') { $cfgOut.Add("version = $prefVer"); continue }
    if ($cfgSec -eq '[cura]' -and $line -match '^active_machine = ') {
        $sawActive = $true
        $cfgOut.Add("active_machine = $machine")
        continue
    }
    $cfgOut.Add($line)
}
if (-not $sawActive) {
    # A second [cura] section would make Cura's parser reject the whole file,
    # and no active machine is the wizard, so this is fatal.
    if ($sawCuraSection) {
        Fail "the preferences seed carries a [cura] section with no active_machine, and appending another would duplicate the section"
    }
    $cfgOut.Add('')
    $cfgOut.Add('[cura]')
    $cfgOut.Add("active_machine = $machine")
}
Set-Content -LiteralPath $CfgPath -Value $cfgOut -Encoding ASCII

$seedVolume = '<unset>'
$dcFiles = @(Get-ChildItem -Path (Join-Path $ConfigDir 'definition_changes') -Filter '*.inst.cfg' -File -ErrorAction SilentlyContinue)
foreach ($dc in $dcFiles) {
    $vals = @()
    foreach ($key in 'machine_width', 'machine_depth', 'machine_height') {
        $m = Select-String -LiteralPath $dc.FullName -Pattern ("^" + $key + " = (.+)$") -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $vals += $m.Matches[0].Groups[1].Value.Trim() }
    }
    # The machine's own container, not the extruder's - the extruder
    # carries no build volume.
    if ($vals.Count -eq 3) {
        $seedVolume = $vals -join 'x'
        break
    }
}
Write-Log "machine seed: machine '$machine' from $FixtureDir"
Write-Log "  $seedFiles container files + cura.cfg (from $PrefFixtureDir) under $ConfigDir"
Write-Log "  build volume $seedVolume | setting_version $setVer"

# The two-boot legs boot from a state the committed fixture is NOT: the
# first-install leg's first boot must be a machine that has never run the
# plugin, and the migration leg's first boot must still carry the v1 blob.
# The transform is the container leg's own (tests\harness\seed_variants.py),
# applied to this platform's config dir, and it fails a leg that would
# otherwise boot the fixture and prove nothing.
$seedVariant = ''
if ($Scenario -eq 'firstinstall') { $seedVariant = 'clean' }
elseif ($Scenario -eq 'migration') { $seedVariant = 'premigration' }
if ($seedVariant) {
    if (-not $pythonOk) { Fail "the $seedVariant seed needs python (no working python on this host)" }
    & python (Join-Path $Root 'tests\harness\seed_variants.py') $seedVariant $ConfigDir
    if ($LASTEXITCODE -ne 0) { Fail "could not apply the $seedVariant seed" }
}

# --- 7. stage the plugin and the driver -----------------------------------
# Both halves are what the harness drives: the built plugin under test, and
# tests/harness/driver copied in as HarnessDriver (the directory name IS the
# plugin id the runner addresses).
Write-Log ""
Write-Log "--- staging plugins ---"
$Package = Join-Path $Root "dist\MoonrakerPrintFollower-v$PluginVersion.curapackage"
if (-not (Test-Path -LiteralPath $Package)) { Fail "$Package is missing (run make package)" }
$PluginDir = Join-Path $ConfigDir 'plugins'
foreach ($name in 'MoonrakerPrintFollower', 'HarnessDriver') {
    $p = Join-Path $PluginDir $name
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $PluginDir | Out-Null
$Staging = Join-Path $WorkDir 'pkg_stage'
if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Staging | Out-Null
# Expand-Archive reads .zip only, so the package is read under that name.
$ZipCopy = Join-Path $WorkDir 'MoonrakerPrintFollower.curapackage.zip'
Copy-Item -LiteralPath $Package -Destination $ZipCopy -Force
Expand-Archive -LiteralPath $ZipCopy -DestinationPath $Staging -Force
$srcPlugin = Join-Path $Staging 'files\plugins\MoonrakerPrintFollower'
if (-not (Test-Path -LiteralPath $srcPlugin)) { Fail "the package carried no files\plugins\MoonrakerPrintFollower" }
Copy-Item -LiteralPath $srcPlugin -Destination $PluginDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Root 'tests\harness\driver') -Destination (Join-Path $PluginDir 'HarnessDriver') -Recurse -Force
Get-ChildItem -LiteralPath $PluginDir -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
foreach ($name in 'MoonrakerPrintFollower', 'HarnessDriver') {
    if (-not (Test-Path -LiteralPath (Join-Path $PluginDir "$name\plugin.json"))) {
        Fail "the staged $name has no plugin.json - Cura would load nothing"
    }
}
Write-Log "staged $PluginDir\MoonrakerPrintFollower and $PluginDir\HarnessDriver"

# --- 8. the plugin's network peer -----------------------------------------
# The seeded machine records point at 127.0.0.1:7125, and the runner's own
# /harness/* calls go to the same port. The simulator has to be up BEFORE
# Cura boots - the plugin connects during its own startup, and the runner's
# boot gate waits for the discovery chain that needs it. tools/ui_test.sh
# starts it on the Linux leg; nothing did here, and every native leg died on
# the runner's first simulator call with the port refused.
$SimPort = 7125
if (-not $pythonOk) { Fail "the simulator needs python, and this host has no working interpreter" }
if ($Scenario -eq 'real') {
    Write-Log "real mode: no simulator - the seeded record points at the real host"
} else {
    # A stale instance from an earlier run would keep serving old code. The
    # match is on the command line, which is the only place the script's name
    # appears.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*simulator_serve.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    # A directory this run owns, on the simulator's PYTHONPATH. Installing
    # into it rather than into whatever environment pip resolves keeps the
    # dependency beside the run and inside the interpreter that serves.
    $SimSite = Join-Path $WorkDir 'pysite'
    if (-not (Test-PyModule $py.Source 'tornado' $SimSite)) {
        Write-Log "tornado is absent - installing it into $SimSite (the simulator's only dependency)"
        if (-not (Install-PyModule $py.Source 'tornado' $SimSite)) {
            Fail "tornado could not be installed with $($py.Source) - the simulator cannot start"
        }
    }
    # The interpreter that serves is the one that has to import it, so the
    # import is re-checked against that exact interpreter with PYTHONPATH set
    # before anything is launched.
    if (-not (Test-PyModule $py.Source 'tornado' $SimSite)) {
        Fail "$($py.Source) cannot import tornado with PYTHONPATH=$SimSite - the simulator cannot start"
    }
    # The checkout's own simulator, not a staged copy: it is the version
    # under test and this host has the tree.
    $simScript = Join-Path $Root 'tests\harness\simulator_serve.py'
    $simDir = Join-Path $Root 'tests\harness'
    $simLog = Join-Path $WorkDir 'simulator.log'
    $simErrLog = Join-Path $WorkDir 'simulator.err.log'
    # PYTHONPATH is set for the launch only: the simulator needs $SimSite,
    # and Cura - launched further down with the same inherited environment -
    # must not see it.
    $prevPyPath = $env:PYTHONPATH
    if (Test-Path -LiteralPath $SimSite) {
        if ($prevPyPath) { $env:PYTHONPATH = "$SimSite;$prevPyPath" }
        else { $env:PYTHONPATH = $SimSite }
    }
    try {
        $simProc = Start-Process -FilePath $py.Source -ArgumentList @($simScript, "$SimPort") `
            -WorkingDirectory $simDir -PassThru -RedirectStandardOutput $simLog -RedirectStandardError $simErrLog
    } finally {
        if ($null -ne $prevPyPath) { $env:PYTHONPATH = $prevPyPath }
        else { Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue }
    }
    Write-Log "simulator: $($py.Source) $simScript $SimPort (pid $($simProc.Id)), PYTHONPATH $SimSite, log $simLog"
    $simUp = $false
    foreach ($i in 1..50) {
        if (Test-Simulator $SimPort) { $simUp = $true; break }
        Start-Sleep -Milliseconds 200
    }
    # Readiness by check, not by luck, and a failure here is fatal: every
    # scenario would otherwise run against a dead peer and fail somewhere far
    # from the cause. The log goes to the job's output with the verdict - the
    # file itself is not part of the uploaded evidence.
    if (-not $simUp) {
        Write-Warn "--- $simLog (tail) ---"
        if (Test-Path -LiteralPath $simLog) { Get-Content -LiteralPath $simLog -Tail 20 | ForEach-Object { Write-Warn "  $_" } }
        if (Test-Path -LiteralPath $simErrLog) { Get-Content -LiteralPath $simErrLog -Tail 20 | ForEach-Object { Write-Warn "  $_" } }
        Fail "the simulator never answered on 127.0.0.1:$SimPort"
    }
    Write-Log "simulator up on 127.0.0.1:$SimPort"
}

# --- 9. launch ------------------------------------------------------------
Write-Log ""
Write-Log "--- launching Cura ---"
# A stale instance shares the config tree and the port file, and fights over
# the per-machine record - it goes before the new boot.
Get-Process -Name 'UltiMaker-Cura' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
# The driver's port and token files are per-boot: a stale one would let the
# wait below pass before Cura is up.
foreach ($dir in @($RpcDir, $LegacyRpcDir)) {
    foreach ($f in 'harness_port.txt', 'harness_token.txt') {
        $p = Join-Path $dir $f
        if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
    }
}
if ($StageOnly) {
    Write-Log "native_harness: -StageOnly: everything is staged and the display is set; Cura was not launched"
    exit 0
}

# The launch env the harness needs. Start-Process has no -Environment before
# PowerShell 7, so this process's own environment block IS the mechanism -
# Cura and the driver both inherit it, and the driver reads
# HARNESS_RPC_DIR from it.
foreach ($k in @($glVars.Keys)) {
    Set-Item -Path ("env:" + $k) -Value $glVars[$k]
    Write-Log "GL env: $k=$($glVars[$k])"
}
$env:HARNESS_GEOMETRY = $Geometry
$env:HARNESS_WINDOW = $WindowPin
Write-Log "harness env: HARNESS_RPC_DIR=$env:HARNESS_RPC_DIR HARNESS_GEOMETRY=$Geometry HARNESS_WINDOW=$WindowPin"

$curaOut = Join-Path $WorkDir 'cura_stdout.log'
$curaErr = Join-Path $WorkDir 'cura_stderr.log'
$p = Start-Process -FilePath $binary.FullName -WorkingDirectory $curaDir -PassThru `
    -RedirectStandardOutput $curaOut -RedirectStandardError $curaErr
Write-Log "launched $($binary.FullName) (pid $($p.Id)), output in $curaOut / $curaErr"

# The driver's port file is the ready marker: it appears once Cura has
# loaded the staged plugin, so it is the one signal that says the runner has
# something to talk to. A fresh boot of a software rasteriser is slow, so
# the budget is the Linux leg's 300 s.
$rpcFile = ''
$started = Get-Date
$exited = $false
for ($tick = 1; $tick -le 300; $tick++) {
    foreach ($dir in @($RpcDir, $LegacyRpcDir)) {
        $candidate = Join-Path $dir 'harness_port.txt'
        if ((Test-Path -LiteralPath $candidate) -and ((Get-Item -LiteralPath $candidate).Length -gt 0)) {
            $rpcFile = $candidate
            break
        }
    }
    if ($rpcFile) { break }
    $p.Refresh()
    if ($p.HasExited) { $exited = $true; break }
    if ($tick % 30 -eq 0) {
        Write-Log "still booting ($([int]((Get-Date) - $started).TotalSeconds)s)"
    }
    Start-Sleep -Seconds 1
}
if (-not $rpcFile) {
    if ($exited) {
        Write-Warn "native_harness: Cura exited during the boot (code $($p.ExitCode)) - the boot crashed"
    } else {
        Write-Warn "native_harness: the driver never came up - Cura did not load the staged plugin within the deadline"
    }
    # Cura may still hold the handles, so the reads are guarded: a sharing
    # violation must not cost the reason the boot failed.
    Write-Warn "--- $curaErr (tail) ---"
    if (Test-Path -LiteralPath $curaErr) {
        Get-Content -LiteralPath $curaErr -Tail 40 -ErrorAction SilentlyContinue | ForEach-Object { Write-Warn "  $_" }
    }
    Write-Warn "--- $curaOut (tail) ---"
    if (Test-Path -LiteralPath $curaOut) {
        Get-Content -LiteralPath $curaOut -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Warn "  $_" }
    }
    exit 1
}
Write-Log "driver up: $rpcFile (after $([int]((Get-Date) - $started).TotalSeconds)s)"

# --- 10. keep the window inside the capture area --------------------------
# The capture is the whole display, so a window hanging off the edge is
# CLIPPED and nothing in the recording says so. If it cannot fit at its
# pinned size it is SHRUNK to fit: a complete smaller window is usable
# evidence where a clipped one is not. Cura enforces a minimum window width,
# so shrinking may not fully succeed - that is reported, not retried.
Add-Type -AssemblyName System.Drawing | Out-Null

function Get-WindowRect([IntPtr]$Handle) {
    $r = New-Object MpfRect
    if ($null -eq $r) { return $null }
    if ([MpfNative]::FrameRect($Handle, [ref]$r) -and (($r.Right - $r.Left) -gt 0) -and (($r.Bottom - $r.Top) -gt 0)) {
        return @{ X = $r.Left; Y = $r.Top; W = $r.Right - $r.Left; H = $r.Bottom - $r.Top; Source = 'DwmGetWindowAttribute' }
    }
    if ([MpfNative]::GetWindowRect($Handle, [ref]$r)) {
        return @{ X = $r.Left; Y = $r.Top; W = $r.Right - $r.Left; H = $r.Bottom - $r.Top; Source = 'GetWindowRect' }
    }
    return $null
}

$scr = Get-DisplayNow
$capW = $scr.Width
$capH = $scr.Height
if (-not $nativeOk) {
    Write-Warn "the window is not checked or moved: the native helper did not compile"
} elseif (-not $capW -or -not $capH) {
    Write-Warn "the window is not checked or moved: the capture area could not be measured, and a bound invented from nothing would be worse than none"
} else {
    $hwnd = [IntPtr]::Zero
    for ($k = 0; $k -lt 12; $k++) {
        $p.Refresh()
        $hwnd = $p.MainWindowHandle
        if ($hwnd -ne [IntPtr]::Zero) { break }
        Start-Sleep -Seconds 2
    }
    $titleHint = 'UltiMaker Cura'
    $picked = [MpfNative]::WindowPick([uint32]$p.Id, $titleHint)
    if ($picked -ne [IntPtr]::Zero) { $hwnd = $picked }
    if ($hwnd -eq [IntPtr]::Zero) {
        Write-Warn "no window handle for pid $($p.Id) - the window cannot be checked against the $capW x $capH capture area"
    } else {
        $rect = Get-WindowRect $hwnd
        if ($null -eq $rect) {
            Write-Warn "no rectangle could be read from window $hwnd (win32 error $([System.Runtime.InteropServices.Marshal]::GetLastWin32Error())) - the window is not moved on a rectangle that does not exist"
        }
        if ($null -ne $rect) {
            Write-Log ("window: '{0}' class '{1}' pid {2}, rectangle {3},{4} {5}x{6} ({7}), pin $WindowPin" -f `
                [MpfNative]::WindowTitle($hwnd), [MpfNative]::WindowClass($hwnd), [MpfNative]::WindowPid($hwnd),
                $rect.X, $rect.Y, $rect.W, $rect.H, $rect.Source)
            $winX = $rect.X
            $winY = $rect.Y
            $winW = $rect.W
            $winH = $rect.H
            $inBounds = (($winW -gt 0) -and ($winH -gt 0) -and ($winX -ge 0) -and ($winY -ge 0) -and
                         (($winX + $winW) -le $capW) -and (($winY + $winH) -le $capH))
            if ($inBounds) {
                Write-Log "window in bounds: $winX,$winY ${winW}x${winH} inside ${capW}x${capH}"
            } elseif (($winW -gt $capW) -or ($winH -gt $capH)) {
                $nw = [Math]::Min($winW, $capW)
                $nh = [Math]::Min($winH, $capH)
                Write-Log "the window (${winW}x${winH}) is larger than the capture area; resizing to ${nw}x${nh} at 0,0"
                # SWP_NOZORDER | SWP_NOACTIVATE: resize and move only, so the
                # stacking and the focus stay as they were.
                [void][MpfNative]::SetWindowPos($hwnd, [IntPtr]::Zero, 0, 0, $nw, $nh, 0x0014)
                Start-Sleep -Seconds 2
                $rect2 = Get-WindowRect $hwnd
                $winX = $rect2.X
                $winY = $rect2.Y
                $winW = $rect2.W
                $winH = $rect2.H
                # The DWM frame carries an invisible border, so a resize to the
                # exact display size still overflows by it. The second trim uses
                # the overflow this move actually measured rather than a guess.
                if ((($winX + $winW) -gt $capW) -or (($winY + $winH) -gt $capH)) {
                    $overW = [Math]::Max(0, ($winX + $winW) - $capW)
                    $overH = [Math]::Max(0, ($winY + $winH) - $capH)
                    $nw2 = [Math]::Max(1, $winW - $overW)
                    $nh2 = [Math]::Max(1, $winH - $overH)
                    Write-Log "the frame border overflowed by ${overW}x${overH}: resizing again to ${nw2}x${nh2}"
                    [void][MpfNative]::SetWindowPos($hwnd, [IntPtr]::Zero, 0, 0, $nw2, $nh2, 0x0014)
                    Start-Sleep -Seconds 2
                    $rect2 = Get-WindowRect $hwnd
                    $winX = $rect2.X
                    $winY = $rect2.Y
                    $winW = $rect2.W
                    $winH = $rect2.H
                }
                $inBounds = (($winW -gt 0) -and ($winH -gt 0) -and ($winX -ge 0) -and ($winY -ge 0) -and
                             (($winX + $winW) -le $capW) -and (($winY + $winH) -le $capH))
                if ($inBounds) {
                    Write-Log "window shrunk into the capture area: $winX,$winY ${winW}x${winH}"
                } else {
                    Write-Warn "the window is still $winX,$winY ${winW}x${winH} against a ${capW}x${capH} capture area - Cura enforces a minimum window size, so the recording may clip it; reported rather than retried"
                }
            } else {
                $nx = [Math]::Max(0, [Math]::Min($winX, $capW - $winW))
                $ny = [Math]::Max(0, [Math]::Min($winY, $capH - $winH))
                Write-Log "the window is outside the capture area; moving $winX,$winY -> $nx,$ny (size kept at ${winW}x${winH})"
                # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE: move only, so the
                # pinned size keeps its subject and the stacking is untouched.
                [void][MpfNative]::SetWindowPos($hwnd, [IntPtr]::Zero, $nx, $ny, 0, 0, 0x0015)
                Start-Sleep -Seconds 2
                $rect2 = Get-WindowRect $hwnd
                $winX = $rect2.X
                $winY = $rect2.Y
                $winW = $rect2.W
                $winH = $rect2.H
                $inBounds = (($winW -gt 0) -and ($winH -gt 0) -and ($winX -ge 0) -and ($winY -ge 0) -and
                             (($winX + $winW) -le $capW) -and (($winY + $winH) -le $capH))
                if ($inBounds) {
                    Write-Log "window moved inside the capture area: $winX,$winY ${winW}x${winH}"
                } else {
                    Write-Warn "the window is outside the capture area at $winX,$winY ${winW}x${winH} and the move did not bring it back"
                }
            }
            # Visible and unoccluded, checked because the failure is silent: with
            # something else over the window the recording is of that something.
            if ([MpfNative]::IsIconic($hwnd) -or -not ([MpfNative]::GetForegroundWindow() -eq $hwnd)) {
                Write-Log "the window is minimised or behind something; bringing it forward"
                if ([MpfNative]::IsIconic($hwnd)) { [void][MpfNative]::ShowWindow($hwnd, 9) }
                [void][MpfNative]::SetForegroundWindow($hwnd)
                Start-Sleep -Seconds 3
                $rect3 = Get-WindowRect $hwnd
                if ($rect3) {
                    $winX = $rect3.X
                    $winY = $rect3.Y
                    $winW = $rect3.W
                    $winH = $rect3.H
                }
            }
            $probe = New-Object MpfPoint
            $topPid = 0
            if ($null -ne $probe) {
                $probe.X = [int]($winX + ($winW / 2))
                $probe.Y = [int]($winY + ($winH / 2))
                $topPid = [MpfNative]::WindowPid([MpfNative]::WindowFromPoint($probe))
            }
            $visible = ([MpfNative]::IsWindowVisible($hwnd) -and -not [MpfNative]::IsIconic($hwnd))
            if ($visible -and ($topPid -eq $p.Id)) {
                Write-Log "window visible at $winX,$winY ${winW}x${winH} and unoccluded (the topmost window at its centre is Cura's)"
            } else {
                Write-Warn "window visibility could not be confirmed (visible=$visible, topmost window at the centre belongs to pid $topPid, Cura is pid $($p.Id)) - the full-display recording may be of something else"
            }
        }
    }
}

# --- 11. hand over to the runner -----------------------------------------
# The mode the -Scenario argument names: a runner mode is used as it stands,
# anything else is a suite group.
$RunnerMode = 'suite'
$RunnerGroup = ''
if ($Scenario -eq '-' -or $Scenario -eq '') {
    $RunnerMode = 'scenario'
} elseif ($Scenario -match '^(scenario|suite|firstinstall|migration|discover|real)$' -or $Scenario -match '^scenario([0-9]|1[01])$') {
    $RunnerMode = $Scenario
} else {
    $RunnerGroup = $Scenario
}
$EnvFile = Join-Path $WorkDir 'harness_env.ps1'
@(
    '# dot-source this before running tests/harness/runner.py',
    "`$env:HARNESS_RPC_DIR = '$RpcDir'",
    "`$env:HARNESS_GEOMETRY = '$Geometry'",
    "`$env:HARNESS_WINDOW = '$WindowPin'",
    "`$env:HARNESS_RUN_DIR = '$ArtifactDir'",
    # The two-boot legs relaunch the app between their boots, and the
    # runner is the only process left to do it: the binary and the
    # directory it must run in, read back from the launch above.
    "`$env:HARNESS_CURA_BIN = '$($binary.FullName)'",
    "`$env:HARNESS_CURA_CWD = '$curaDir'",
    # Where Cura keeps its own log: the runner harvests it next to the
    # evidence (there is no ui_test.sh on this host to do it), and a
    # driver that never answered is read out of that file.
    "`$env:HARNESS_CURA_CONFIG = '$ConfigDir'",
    "`$env:MPF_WORK_DIR = '$WorkDir'",
    "`$env:CURA_VERSION = '$CuraVersion'",
    "`$env:PLUGIN_VERSION = '$PluginVersion'",
    "`$env:HARNESS_MODE = '$RunnerMode'"
) | Set-Content -LiteralPath $EnvFile -Encoding ASCII
# And the environment the app itself was launched with, which Start-Process
# only ever got because it inherited this process's block: a relaunched
# second boot without the GL variables (or without Mesa's directory on
# PATH) is an app that cannot render.
foreach ($k in @($glVars.Keys)) {
    Add-Content -LiteralPath $EnvFile -Encoding ASCII -Value "`$env:$k = '$($glVars[$k])'"
}
if ($mesaOk) {
    Add-Content -LiteralPath $EnvFile -Encoding ASCII -Value "`$env:Path = '$MesaDir;' + `$env:Path"
}
if ($RunnerGroup) {
    Add-Content -LiteralPath $EnvFile -Encoding ASCII -Value "`$env:SCENARIO_GROUP = '$RunnerGroup'"
}
$runner = Join-Path $Root 'tests\harness\runner.py'
Write-Log ""
Write-Log "native_harness: Cura $CuraVersion is running with the plugin and the driver staged"
Write-Log "native_harness: config: $ConfigDir | driver port file: $rpcFile"
Write-Log "native_harness: gallery goes to: $ArtifactDir\index.html"
Write-Log "native_harness: run the harness with:"
if ($RunnerGroup) {
    Write-Log "  . '$EnvFile'; python '$runner' $RunnerMode '$RunnerGroup'"
} else {
    Write-Log "  . '$EnvFile'; python '$runner' $RunnerMode"
}

} finally {
    Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
}
