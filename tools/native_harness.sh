#!/usr/bin/env bash
# The macOS half of the UI harness host: install Cura, seed its config,
# move the display, stage the plugin and the driver, start the simulator
# the plugin talks to, launch Cura and leave both running so
# tests/harness/runner.py can drive them.
#
# Usage:
#   tools/native_harness.sh macos <cura-version> <scenario> [options]
#
#   <cura-version>   5.7.0 and up (the asset names hold across 5.7-5.13)
#   <scenario>       what the runner runs once this returns: a runner mode
#                    (scenario | scenario1..11 | suite | firstinstall |
#                    migration | discover) or a suite group name, which is
#                    run as "suite <group>". "-" means the default scenario.
#   --arch auto|arm64|x64
#                    which dmg to install. auto prefers arm64 and falls
#                    back to x64, which needs Rosetta on an arm64 host.
#   --work-dir DIR   scratch root (default /tmp/mpf-native)
#   --stage-only     do everything except launching Cura
#
# Environment: HARNESS_GEOMETRY (1920x1080), HARNESS_WINDOW (1840x1040),
# MPF_WORK_DIR, RUNNER_TEMP. Every path this script owns lives under the
# work dir; nothing is written into the repository.
#
# The runner reaches the driver through <work-dir>/rpc, handed over as
# HARNESS_RPC_DIR: one variable, so the two sides cannot disagree about
# the path. The boot check also accepts the driver's port file at the old
# fixed /tmp/mpf (a checkout whose driver predates the variable), and
# both places are cleared before the launch. The command to run is
# printed at the end, and the same exports are written to
# <work-dir>/harness_env.sh.
#
# ASCII only: this file is read by tools that assume it.
set -Eeuo pipefail

note() { echo "native_harness: $*"; }
warn() { echo "native_harness: $*" >&2; }
die() { echo "native_harness: $*" >&2; exit 1; }
# A `set -e` death is otherwise silent: the step fails with a bare exit
# code and the log stops mid-sentence, which is how the first live macOS
# run died (the killer line had to be inferred from the timing). -E
# extends this into the function bodies and the background subshells,
# where nearly all of the work happens; $BASH_COMMAND names the command
# that failed, so a silent stop is diagnosable from the job log alone.
trap 'echo "native_harness: command failed at line $LINENO: $BASH_COMMAND (status $?)" >&2' ERR

usage() {
    # The header, up to the first non-comment line: the range used to be a
    # fixed line count, which silently truncated --help as the header grew
    # past it.
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}

OS_ARG=""
CURA_VERSION=""
SCENARIO=""
ARCH_REQUEST="${CURA_MACOS_ARCH:-auto}"
WORK_DIR_ARG=""
STAGE_ONLY="no"

POS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --arch) [ "$#" -ge 2 ] || die "--arch needs a value"; ARCH_REQUEST="$2"; shift 2 ;;
        --arch=*) ARCH_REQUEST="${1#--arch=}"; shift ;;
        --work-dir) [ "$#" -ge 2 ] || die "--work-dir needs a value"; WORK_DIR_ARG="$2"; shift 2 ;;
        --work-dir=*) WORK_DIR_ARG="${1#--work-dir=}"; shift ;;
        --stage-only) STAGE_ONLY="yes"; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option: $1 (try --help)" ;;
        *) POS+=("$1"); shift ;;
    esac
done
[ "${#POS[@]}" -eq 3 ] || { usage >&2; exit 2; }
OS_ARG="${POS[0]}"
CURA_VERSION="${POS[1]}"
SCENARIO="${POS[2]}"

case "$OS_ARG" in
    macos|macOS|MacOS|macos-latest|darwin|Darwin) ;;
    windows|Windows|windows-latest) die "this is the macOS leg; run tools/native_harness.ps1 for $OS_ARG" ;;
    *) die "unknown operating system '$OS_ARG' (macos or windows)" ;;
esac
# The remaining checks come before the host check: a mistyped version is
# wrong on every host, and reporting it as one is the useful answer.
case "$ARCH_REQUEST" in
    auto|arm64|x64) ;;
    *) die "--arch must be auto, arm64 or x64 (got '$ARCH_REQUEST')" ;;
esac
printf '%s' "$CURA_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' ||
    die "'$CURA_VERSION' is not a version like 5.13.0"
REQ_MM="$(printf '%s' "$CURA_VERSION" | cut -d. -f1,2)"
REQ_MAJOR="${REQ_MM%%.*}"
REQ_MINOR="${REQ_MM##*.}"
if [ "$REQ_MAJOR" -ne 5 ] || [ "$REQ_MINOR" -lt 7 ] || [ "$REQ_MINOR" -gt 13 ]; then
    die "Cura $CURA_VERSION is outside the range this harness ranges over (5.7.0 to 5.13.0)"
fi
[ "$(uname -s)" = "Darwin" ] || die "this script is the macOS leg and this is not macOS ($(uname -s))"

# The plugin package is version-named; read it from package.json (no
# interpreter needed: the runner's Python is not this script's problem).
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git checkout"
cd "$ROOT"
PLUGIN_VERSION="$(sed -n 's/.*"package_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' package.json | head -1)"
[ -n "$PLUGIN_VERSION" ] || die "package.json carries no package_version"

# One geometry for the whole run (the round-2 contract the Linux leg
# keeps): the screen SIZE, the capture size and the window pin all
# resolve from here, and the runner consumes them through the
# environment. The window pins SMALLER than the screen - headroom, so
# the screen-fits assertion has something to defend.
HARNESS_GEOMETRY="${HARNESS_GEOMETRY:-1920x1080}"
HARNESS_WINDOW="${HARNESS_WINDOW:-1840x1040}"
WANT_W="${HARNESS_GEOMETRY%%x*}"
WANT_H="${HARNESS_GEOMETRY##*x}"

WORK_DIR="${WORK_DIR_ARG:-${MPF_WORK_DIR:-/tmp/mpf-native}}"
mkdir -p "$WORK_DIR"
# The runner reads the driver's port and token from here.
HARNESS_RPC_DIR="$WORK_DIR/rpc"
LEGACY_RPC_DIR="/tmp/mpf"
export HARNESS_RPC_DIR
# The gallery root: every still and every video the runner writes goes
# under HARNESS_RUN_DIR, so this is the path the gate's upload has to
# find. It comes from the gate's own variable, RUN_DIR_NAME, resolved by
# the shared rule in tools/ui_test_paths.sh (an absolute name passes
# through, a relative one nests under the work dir) - one rule, one
# module, both platforms. An explicit HARNESS_RUN_DIR wins: that is what
# the runner reads, and a caller that set it means it.
if [ -n "${HARNESS_RUN_DIR:-}" ]; then
    ARTIFACT_DIR="$HARNESS_RUN_DIR"
elif [ -n "${RUN_DIR_NAME:-}" ]; then
    ARTIFACT_DIR="$(sh "$ROOT/tools/ui_test_paths.sh" resolve "$WORK_DIR" "$RUN_DIR_NAME")" ||
        die "could not resolve RUN_DIR_NAME '$RUN_DIR_NAME'"
else
    ARTIFACT_DIR="$WORK_DIR/ui-artifacts/run-$(date -u '+%Y-%m-%d-%H%M%S')"
fi
LOG="$WORK_DIR/native_harness.log"
# The installer is ~170 MB and regenerable from its pinned URL, so it
# goes to the runner's temp dir - never next to the evidence, never in
# an upload.
INSTALLER_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/cura-installers"
mkdir -p "$INSTALLER_DIR"

APP="/Applications/UltiMaker-Cura.app"
STASH="$INSTALLER_DIR/cura-disabled-plugins"

# One run per machine at a time: a second run restages the config tree
# and kills the first run's Cura mid-scenario. A holder that died
# without releasing leaves a stale lock - its pid fails the liveness
# check and the lock is reclaimed.
LOCK_DIR="$WORK_DIR/.native_harness.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
        rm -rf "$LOCK_DIR"
        mkdir "$LOCK_DIR"
    else
        die "another run holds $LOCK_DIR (pid ${holder:-unknown})"
    fi
fi
echo "$$" >"$LOCK_DIR/pid"
# The lock goes with the shell; Cura itself is meant to outlive it.
trap 'rm -rf "$LOCK_DIR"' EXIT

{
    echo "=== macOS native harness, $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
    echo "os            : $(sw_vers -productVersion 2>/dev/null || echo '<unreadable>') ($(uname -m))"
    echo "cura version  : $CURA_VERSION"
    echo "plugin version: $PLUGIN_VERSION"
    echo "scenario      : $SCENARIO"
    echo "work dir      : $WORK_DIR"
    echo "rpc dir       : $HARNESS_RPC_DIR"
    echo "geometry      : $HARNESS_GEOMETRY (window pin $HARNESS_WINDOW)"

    # --- 1. the installer ------------------------------------------------
    BASE="https://github.com/Ultimaker/Cura/releases/download/${CURA_VERSION}"
    ARM64_ASSET="UltiMaker-Cura-${CURA_VERSION}-macos-ARM64.dmg"
    X64_ASSET="UltiMaker-Cura-${CURA_VERSION}-macos-X64.dmg"
    # The arch policy needs one bit per arch - is this file published? -
    # and a HEAD on the public download URL answers exactly that, from
    # the same host the installer comes from, consuming no REST budget.
    # The API listing it replaces is rate-limited per source IP and one
    # 403 there used to cost the whole leg. --fail stays off: the status
    # code IS the answer.
    echo
    echo "--- is each arch published for $CURA_VERSION? ---"
    PROBE_STATE=""
    probe_asset() {
        local asset="$1" attempt=0 code=""
        for wait_s in 0 5 15; do
            if [ "$wait_s" -gt 0 ]; then
                echo "  $asset: retrying in ${wait_s}s (last status: ${code:-none})"
                sleep "$wait_s"
            fi
            attempt=$((attempt + 1))
            code="$(curl -sSI -o /dev/null -w '%{http_code}' --max-time 60 "$BASE/$asset" 2>/dev/null || true)"
            echo "  $asset: HTTP ${code:-none} (attempt ${attempt})"
            case "$code" in
                200|302) PROBE_STATE=published; return 0 ;;
                404) PROBE_STATE=absent; return 0 ;;
            esac
        done
        PROBE_STATE=unknown
        return 1
    }
    ARM64_STATE=unknown
    X64_STATE=unknown
    if probe_asset "$ARM64_ASSET"; then ARM64_STATE="$PROBE_STATE"; fi
    if probe_asset "$X64_ASSET"; then X64_STATE="$PROBE_STATE"; fi
    echo "arm64 dmg published: $ARM64_STATE"
    echo "x64 dmg published  : $X64_STATE"
    if [ "$ARM64_STATE" = unknown ] || [ "$X64_STATE" = unknown ]; then
        die "the published-asset probe got no usable status (arm64=$ARM64_STATE x64=$X64_STATE)"
    fi
    ARCH_TAG=""
    case "$ARCH_REQUEST" in
        arm64)
            [ "$ARM64_STATE" = published ] && ARCH_TAG=ARM64 ;;
        x64)
            [ "$X64_STATE" = published ] && ARCH_TAG=X64 ;;
        *)
            if [ "$ARM64_STATE" = published ]; then
                ARCH_TAG=ARM64
            elif [ "$X64_STATE" = published ]; then
                ARCH_TAG=X64
            fi ;;
    esac
    [ -n "$ARCH_TAG" ] || die "$ARCH_REQUEST is unpublished for $CURA_VERSION"
    echo "chosen: $ARCH_TAG ($ARCH_REQUEST)"

    DMG="$INSTALLER_DIR/UltiMaker-Cura-${CURA_VERSION}-macos-${ARCH_TAG}.dmg"
    if [ -f "$DMG" ]; then
        echo "installer already downloaded: $DMG ($(du -h "$DMG" | cut -f1))"
    else
        # The whole retry sequence is bounded, not just one attempt: four
        # attempts at --max-time 900 could outlive the setup step's own
        # 30-minute timeout, which reports a step timeout and no reason.
        # The dmg measures ~20 s here, so 300 s an attempt and 600 s of
        # retrying is a wide margin with a named failure at the end.
        curl -L --fail --retry 3 --retry-delay 5 --connect-timeout 30 \
            --max-time 300 --retry-max-time 600 \
            -w "http_code=%{http_code} bytes=%{size_download}\n" \
            -o "$DMG" "$BASE/UltiMaker-Cura-${CURA_VERSION}-macos-${ARCH_TAG}.dmg" ||
            die "the dmg download failed"
        echo "downloaded: $DMG ($(du -h "$DMG" | cut -f1))"
    fi

    # An x64 build on an arm64 host executes only through Rosetta.
    if [ "$ARCH_TAG" = X64 ] && [ "$(uname -m)" = arm64 ]; then
        if arch -x86_64 /usr/bin/true 2>/dev/null; then
            echo "rosetta: present (an x86_64 binary executed)"
        else
            note "rosetta is missing and an x64 Cura cannot execute without it - installing it"
            sudo softwareupdate --install-rosetta --agree-to-license ||
                die "softwareupdate could not install Rosetta; the x64 build cannot run here"
            arch -x86_64 /usr/bin/true 2>/dev/null || die "Rosetta still does not work after installing it"
            echo "rosetta: installed and working"
        fi
    fi

    # --- 2. install ------------------------------------------------------
    echo
    echo "--- installing Cura $CURA_VERSION ($ARCH_TAG) ---"
    MNT="$WORK_DIR/mnt"
    CDR="$INSTALLER_DIR/cura-licence-stripped.cdr"
    MOUNTED=""

    # mountpoints_of <attach-output-file>: the mount points hdiutil
    # recorded. -plist is the reliable record, but a refused licence
    # agreement interleaves the licence text with it on stdout, so the
    # XML is cut out of that mixed stream before parsing.
    mountpoints_of() {
        local raw="$1" xml="$1.xml" i=0 mp
        if ! /usr/libexec/PlistBuddy -c 'Print :system-entities' "$raw" >/dev/null 2>&1; then
            awk '/^[[:space:]]*<\?xml/{f=1} f' "$raw" >"$xml"
            raw="$xml"
        fi
        while [ "$i" -lt 8 ]; do
            mp="$(/usr/libexec/PlistBuddy -c "Print :system-entities:$i:mount-point" "$raw" 2>/dev/null)"
            [ -n "$mp" ] && printf '%s\n' "$mp"
            i=$((i + 1))
        done
    }

    # attach <label> <stdin-payload> <image> [flags...]: ONE mount
    # attempt with its exit code and the mount points it produced.
    # Bounded, because a licence viewer waiting for a click would
    # otherwise hang the run to its timeout.
    attach() {
        local label="$1" payload="$2" image="$3"
        shift 3
        local out="$WORK_DIR/attach-${label}.out" rc pid waited=0 state
        (
            if [ -n "$payload" ]; then
                printf '%s' "$payload" | sudo hdiutil attach -plist -nobrowse \
                    -noverify -noautoopen -readonly "$@" "$image"
            else
                sudo hdiutil attach -plist -nobrowse -noverify -noautoopen \
                    -readonly "$@" "$image"
            fi
        ) >"$out" 2>&1 &
        pid=$!
        while [ "$waited" -lt 60 ]; do
            # `ps` exits 1 for a pid that is gone, and under `set -e` +
            # pipefail that killed this script silently on the first
            # iteration after the attach finished — the poll's own exit
            # condition read as a fatal error. The empty state below IS
            # the answer; the pipeline's status must not be.
            state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d ' \n' || true)"
            if [ -z "$state" ] || [ "${state#Z}" != "$state" ]; then
                break
            fi
            sleep 2
            waited=$((waited + 2))
        done
        if [ "$waited" -ge 60 ]; then
            warn "attach '$label' is still running after 60s - killing it (a licence viewer waiting for a click looks like this)"
            kill -9 "$pid" 2>/dev/null || true
            # sudo, because the attach runs under sudo: killing another
            # user's process is EPERM otherwise, and the `|| true` below
            # would hide a backstop that never fired.
            sudo pkill -9 -f 'hdiutil attac[h]' 2>/dev/null || true
        fi
        # `rc=0` first: `wait ... || true` followed by `rc=$?` would
        # report the status of `true`, not of the attach, and the line
        # this feeds is evidence about whether the mount worked.
        rc=0
        wait "$pid" 2>/dev/null || rc=$?
        # `head -1` closes the pipe, and a producer still writing into it
        # takes SIGPIPE — under pipefail that is a fatal status for the
        # assignment. Reading the first line is the intent either way.
        MOUNTED="$(mountpoints_of "$out" | head -1 || true)"
        # mount(8) reports the resolved mount point, and this work dir sits
        # under /tmp, which is a symlink to /private/tmp on macOS - so the
        # plist parse is tried first, and the fallback asks for the path
        # the kernel would print rather than the one we asked for.
        MNT_REAL="$MNT"
        if [ -d "$MNT" ]; then
            MNT_REAL="$(cd "$MNT" && pwd -P)"
        fi
        if [ -z "$MOUNTED" ] && mount | grep -qF " on $MNT_REAL "; then
            MOUNTED="$MNT"
        fi
        if [ -n "$MOUNTED" ]; then
            echo "  attach '$label': exit $rc after ${waited}s, mounted at $MOUNTED"
        else
            echo "  attach '$label': exit $rc after ${waited}s, nothing mounted"
            sed -n '1,10p' "$out" | sed 's/^/    | /'
        fi
        return 0
    }

    detach_mounted() {
        [ -n "$MOUNTED" ] || return 0
        sudo hdiutil detach "$MOUNTED" >/dev/null 2>&1 || sudo hdiutil detach -force "$MOUNTED" >/dev/null 2>&1 || true
        MOUNTED=""
    }

    mkdir -p "$MNT"
    # The Cura dmg presents a licence agreement on some runners. Rung 1
    # uses the documented flag when this hdiutil has one, rungs 2 and 3
    # answer the prompt on stdin, and rung 4 removes the agreement from
    # the image altogether (a UDTO copy has no resource fork to carry
    # one).
    ACCEPT_FLAG=""
    if hdiutil help attach 2>&1 | grep -q -- '-acceptlicense'; then
        ACCEPT_FLAG=-acceptlicense
        echo "-acceptlicense: supported here"
    fi
    if [ -n "$ACCEPT_FLAG" ]; then
        attach accept-flag $'y\n' "$DMG" "$ACCEPT_FLAG" -mountpoint "$MNT"
    fi
    [ -z "$MOUNTED" ] && attach piped-y $'y\ny\ny\n' "$DMG" -mountpoint "$MNT"
    [ -z "$MOUNTED" ] && attach pager-form "$(yes qy 2>/dev/null | head -5)" "$DMG" -mountpoint "$MNT"
    if [ -z "$MOUNTED" ]; then
        echo "=== last rung: strip the licence agreement out of the image ==="
        rm -f "$CDR" "$CDR.cdr"
        sudo hdiutil convert -quiet -format UDTO -o "$CDR" "$DMG" 2>&1 | sed -n '1,10p' || true
        CDR_FILE=""
        if [ -f "$CDR" ]; then CDR_FILE="$CDR"; elif [ -f "$CDR.cdr" ]; then CDR_FILE="$CDR.cdr"; fi
        if [ -n "$CDR_FILE" ]; then
            attach licence-stripped "" "$CDR_FILE" -mountpoint "$MNT"
        fi
    fi
    [ -n "$MOUNTED" ] || die "the dmg would not mount (attempts: see above)"
    APP_SRC="$(find "$MOUNTED" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)"
    if [ -z "$APP_SRC" ]; then
        APP_SRC="$(find "$MOUNTED" -maxdepth 3 -name '*.app' -print -quit 2>/dev/null)"
    fi
    [ -n "$APP_SRC" ] || { detach_mounted; die "no .app inside the dmg"; }
    echo "copying $(basename "$APP_SRC") -> $APP"
    sudo rm -rf "$APP"
    sudo ditto "$APP_SRC" "$APP" || { detach_mounted; die "ditto could not copy the bundle to $APP"; }
    detach_mounted
    rm -f "$CDR" "$CDR.cdr"
    BIN_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist" 2>/dev/null || true)"
    [ -n "$BIN_NAME" ] || BIN_NAME="UltiMaker-Cura"
    BIN="$APP/Contents/MacOS/$BIN_NAME"
    [ -x "$BIN" ] || die "no executable at $BIN after the install"
    echo "installed: $BIN"

    # --- 3. the tools this leg runs --------------------------------------
    echo
    echo "--- tools ---"
    HAVE_BREW="no"
    if command -v brew >/dev/null 2>&1; then
        HAVE_BREW="yes"
        echo "brew: $(command -v brew)"
    else
        note "Homebrew is absent - bootstrapping it (the install path for displayplacer and ffmpeg)"
        # The official installer, non-interactive. It needs sudo, which is
        # passwordless on hosted runners.
        if NONINTERACTIVE=1 /bin/bash -c \
            "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
            >"$WORK_DIR/brew-install.log" 2>&1; then
            if [ -x /opt/homebrew/bin/brew ]; then
                eval "$(/opt/homebrew/bin/brew shellenv)"
            elif [ -x /usr/local/bin/brew ]; then
                eval "$(/usr/local/bin/brew shellenv)"
            fi
        fi
        if command -v brew >/dev/null 2>&1; then
            HAVE_BREW="yes"
            echo "brew: installed by us at $(command -v brew)"
        else
            warn "brew could not be installed (see $WORK_DIR/brew-install.log) - tools it would provide cannot be provisioned"
        fi
    fi
    provision() {
        # provision <command> <brew formula>: probes, then installs when
        # the image lacks it. Returns the command path on stdout.
        local cmd="$1" formula="$2" path
        path="$(command -v "$cmd" 2>/dev/null || true)"
        if [ -n "$path" ]; then
            echo "$path"
            return 0
        fi
        [ "$HAVE_BREW" = yes ] || { warn "$cmd is absent and there is no brew to install it with"; return 1; }
        # On stderr, because this function's stdout is its return value: on
        # the cold image the caller captured the note's text along with the
        # path, and every later use of it was bash trying to execute a
        # two-line string - "displayplacer: No such file or directory",
        # exit 127, and the display never moved.
        note "$cmd is absent - installing it (brew install $formula)" >&2
        HOMEBREW_NO_AUTO_UPDATE=1 brew install "$formula" >/dev/null 2>&1 || true
        path="$(command -v "$cmd" 2>/dev/null || true)"
        [ -n "$path" ] && echo "$path"
        return 0
    }
    # displayplacer moves the display; ffmpeg is the harness's recorder
    # and still capture on every platform.
    DP="$(provision displayplacer displayplacer || true)"
    FFMPEG="$(provision ffmpeg ffmpeg || true)"
    echo "displayplacer: ${DP:-ABSENT}"
    echo "ffmpeg: ${FFMPEG:-ABSENT}"

    # --- 4. the display ---------------------------------------------------
    echo
    echo "--- display ---"
    DP_LIST="$WORK_DIR/displayplacer-list.txt"
    read_res() {
        # The mode list is re-read here, never taken from the capture made
        # before the move: that file was the read-back's only source, and a
        # read-back of a file written before the change answers with the
        # pre-change mode - the log said "display after: 1024x768" while the
        # recording came out at the pinned 1920x1080, and the window section
        # below then shrank Cura's window to fit a display that was not
        # there.
        #
        # Both reads end in `head -1`, which closes the pipe while the
        # producer is still writing: system_profiler's output easily
        # overruns the pipe buffer, and the SIGPIPE status is fatal to
        # the assignment under pipefail. Reading the first line is the
        # intent either way.
        if [ -n "$DP" ]; then
            "$DP" list >"$DP_LIST" 2>&1 || true
        fi
        local out=""
        if [ -n "$DP" ] && [ -s "$DP_LIST" ]; then
            out="$(sed -n 's/.*Resolution: *\([0-9][0-9]*\) *x *\([0-9][0-9]*\).*/\1x\2/p' "$DP_LIST" | head -1 || true)"
            [ -n "$out" ] && { echo "$out"; return 0; }
        fi
        out="$(system_profiler SPDisplaysDataType 2>&1 |
            sed -n 's/^ *Resolution: *\([0-9][0-9]*\) *x *\([0-9][0-9]*\).*/\1x\2/p' | head -1 || true)"
        echo "$out"
    }
    read_id() {
        # Stops at its first hit: a second online display would otherwise
        # contribute its id to the same field, and awk ends the read itself
        # where `head -1` would hand the producer a SIGPIPE that pipefail
        # makes fatal to the assignment.
        awk '/Persistent screen id:/ { sub(/^.*Persistent screen id: */, ""); print; exit }' \
            "$DP_LIST" 2>/dev/null || true
    }
    if [ -n "$DP" ]; then
        "$DP" list >"$DP_LIST" 2>&1 || true
    else
        : >"$DP_LIST"
    fi
    DISPLAY_BEFORE="$(read_res)"
    echo "display before: ${DISPLAY_BEFORE:-<unreadable>}"
    DISPLAY_WHY="not attempted"
    if [ "$DISPLAY_BEFORE" = "$HARNESS_GEOMETRY" ]; then
        DISPLAY_WHY="already at the pinned geometry"
    elif [ -z "$DP" ]; then
        DISPLAY_WHY="no displayplacer, so the display cannot be moved"
        warn "the display is not at $HARNESS_GEOMETRY and cannot be moved: the recording will be another size and the window pin may not fit"
    else
        # The mode list is polled before it is parsed. This section runs
        # seconds into the job, and a hosted runner's display can still be
        # coming up then: the first live macOS run read an empty list and
        # reported it as "no persistent screen id" while the same image had
        # moved the display minutes later in the spike. An empty read is not
        # the same answer as a display that refuses to move.
        DPID=""
        for _ in $(seq 1 60); do
            DPID="$(read_id)"
            [ -n "$DPID" ] && break
            sleep 2
            "$DP" list >"$DP_LIST" 2>&1 || true
        done
        DPHZ="$(sed -n 's/.*hz:\([0-9][0-9]*\).*/\1/p' "$DP_LIST" | head -1 || true)"
        [ -n "$DPHZ" ] || DPHZ="$(sed -n 's/.*Hertz: *\([0-9][0-9]*\).*/\1/p' "$DP_LIST" | head -1 || true)"
        [ -n "$DPHZ" ] || DPHZ=60
        # The colour depth must be one this display actually offers:
        # asking for one it does not is an outright refusal ("could not
        # find res:1920x1080 hz:60 color_depth:8"), so it is read from
        # the mode list for the target resolution, and the current mode's
        # depth is only the fallback.
        DPDEPTH="$(sed -n "s/.*res:${WANT_W}x${WANT_H} hz:[0-9][0-9]* color_depth:\([0-9][0-9]*\).*/\1/p" "$DP_LIST" | head -1 || true)"
        [ -n "$DPDEPTH" ] || DPDEPTH="$(sed -n 's/^Color Depth: *\([0-9][0-9]*\).*/\1/p' "$DP_LIST" | head -1 || true)"
        [ -n "$DPDEPTH" ] || DPDEPTH=7
        # displayplacer defaults to the only active screen when no id is
        # given, so the id-less form is the second attempt rather than a
        # dead end: it is what moves the display when the list cannot be
        # parsed at all.
        DP_SPEC="res:${WANT_W}x${WANT_H} hz:${DPHZ} color_depth:${DPDEPTH}"
        [ -n "$DPID" ] && DP_SPEC="id:$DPID $DP_SPEC"
        echo "setting ${WANT_W}x${WANT_H} at ${DPHZ} Hz color_depth:$DPDEPTH on ${DPID:-the only active screen} (was ${DISPLAY_BEFORE:-unreadable})"
        DP_OUT="$("$DP" "$DP_SPEC" 2>&1)" && DP_RC=0 || DP_RC=$?
        [ -n "$DP_OUT" ] && echo "displayplacer output: $DP_OUT"
        if [ "$DP_RC" -ne 0 ] && [ -n "$DPID" ]; then
            echo "displayplacer refused the id (exit $DP_RC); retrying without one"
            DP_OUT="$("$DP" "res:${WANT_W}x${WANT_H} hz:${DPHZ} color_depth:${DPDEPTH}" 2>&1)" && DP_RC=0 || DP_RC=$?
            [ -n "$DP_OUT" ] && echo "displayplacer output: $DP_OUT"
        fi
        # A refusal must not be reported as a success - the set used
        # to exit 1 while the log said it had moved the display.
        if [ "$DP_RC" -eq 0 ]; then
            DISPLAY_WHY="displayplacer set res:${WANT_W}x${WANT_H} color_depth:$DPDEPTH"
            sleep 3
        else
            DISPLAY_WHY="displayplacer REFUSED res:${WANT_W}x${WANT_H} (exit $DP_RC): ${DP_OUT:-no output}"
            warn "$DISPLAY_WHY"
        fi
    fi
    DISPLAY_AFTER="$(read_res)"
    echo "display after: ${DISPLAY_AFTER:-<unreadable>} ($DISPLAY_WHY)"
    if [ -z "$DISPLAY_AFTER" ]; then
        # An unreadable display is the difference between a recording of the
        # pinned size and one of something else, so the raw reads are kept:
        # without them the only fact a failed run leaves behind is that the
        # read found nothing, which is not a diagnosis.
        echo "--- raw display reads ---"
        echo "displayplacer list ($DP_LIST):"
        sed -n '1,40p' "$DP_LIST" 2>/dev/null || true
        echo "system_profiler SPDisplaysDataType:"
        system_profiler SPDisplaysDataType 2>&1 | sed -n '1,40p' || true
    fi

    # What GL the guest can reach, printed once per leg: the app's renderer
    # here is Apple's software one, because a VM exposes no GPU to the
    # guest's GL stack. That single fact is what makes Cura's own probe take
    # the macOS-only 2.x fallback (see `--- 8b ---`), and it is why these
    # legs capture nothing rather than judging a screen that cannot present.
    echo "--- gpu ---"
    sysctl -n kern.hv_vmm_present 2>/dev/null | sed 's/^/hypervisor present: /' || true
    system_profiler SPDisplaysDataType 2>&1 | sed -n '1,20p' || true

    # --- 5. seed Cura's config -------------------------------------------
    # Two jobs, one writer.
    # (a) The Welcome wizard comes up exactly when there is no active
    # machine (shouldShowWelcomeDialog is "activeMachine is None", with
    # no "seen" flag to set), and runner.py's first gate asserts the
    # wizard absent - so a machine is what keeps it down. Cura restores
    # one only when BOTH halves are present: cura/active_machine names
    # it AND a stack with that id can be found. The preferences half is
    # cura.cfg; the stack half is machine_instances/<name>.global.cfg
    # plus every container its [containers] list names, because a name
    # Cura cannot resolve makes ContainerStack.deserialize raise and
    # then there is no machine at all. Every file-format version is read
    # out of the installed build - the one thing that moves across
    # 5.7-5.13 - and nothing is written when one cannot be read, since a
    # container file with the wrong version is rejected outright.
    # general/last_run_version is deliberately NOT written: empty means
    # "this version" to QtApplication, so What's New stays off.
    # (b) macOS raises a TCC local-network consent alert as soon as an
    # app starts mDNS discovery, and nothing on a runner can answer it:
    # it sits on top of the window and would swallow every scenario
    # click. Cura raises it from UM3NetworkPrinting, so that plugin is
    # disabled twice over - the id goes into the seeded
    # general/disabled_plugins, and its directory leaves the bundle. A
    # plugin on the disabled list is never registered, so the prompt is
    # prevented rather than answered.
    echo
    echo "--- seeding Cura's config ---"
    BUNDLE_VER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || true)"
    [ -n "$BUNDLE_VER" ] || BUNDLE_VER="$CURA_VERSION"
    MM="$(printf '%s' "$BUNDLE_VER" | awk -F. 'NF >= 2 && $2 != "" { print $1 "." $2; exit }')"
    [ -n "$MM" ] || MM="$(printf '%s' "$CURA_VERSION" | awk -F. '{ print $1 "." $2 }')"
    echo "bundle version $BUNDLE_VER -> config dir $MM"
    # `|| true` on every read below: a missing file makes the pipeline
    # fail, and under errexit that ends the script at the read - before
    # the sentence written for exactly this case ("the installed build did
    # not give up its file-format versions") can be printed.
    PREF_PY="$(find "$APP/Contents" -path '*/UM/Preferences.py' -print -quit 2>/dev/null || true)"
    CURA_PY="${PREF_PY%/UM/Preferences.py}"
    PREF_VER=""
    CS_VER=""
    IC_VER=""
    SET_VER=""
    if [ -n "$PREF_PY" ]; then
        PREF_VER="$(sed -n 's/^ *Version *= *\([0-9][0-9]*\).*/\1/p' "$PREF_PY" | head -1 || true)"
        CS_VER="$(sed -n 's/^ *Version *= *\([0-9][0-9]*\).*/\1/p' "$CURA_PY/UM/Settings/ContainerStack.py" 2>/dev/null | head -1 || true)"
        IC_VER="$(sed -n 's/^ *Version *= *\([0-9][0-9]*\).*/\1/p' "$CURA_PY/UM/Settings/InstanceContainer.py" 2>/dev/null | head -1 || true)"
        SET_VER="$(sed -n 's/^ *SettingVersion *= *\([0-9][0-9]*\).*/\1/p' "$CURA_PY/cura/CuraApplication.py" 2>/dev/null | head -1 || true)"
    fi
    echo "file-format versions read out of the installed build: preferences=${PREF_VER:-<unreadable>} stack=${CS_VER:-<unreadable>} instance=${IC_VER:-<unreadable>} setting=${SET_VER:-<unreadable>}"
    if [ -z "$PREF_VER" ] || [ -z "$CS_VER" ] || [ -z "$IC_VER" ] || [ -z "$SET_VER" ]; then
        die "the installed build did not give up its file-format versions, so nothing can be seeded and Cura would boot on the wizard"
    fi

    # Which plugins start mDNS: the content probe survives a version
    # rename, and the name seed keeps the id present even when nothing
    # matches on this build. The plugin id IS its directory name.
    SEED_ID=UM3NetworkPrinting
    MDNS_IDS=""
    # Process substitution, not a pipe: the writes below have to land in
    # this shell's variables, and a pipe would run the loop in a subshell.
    while IFS= read -r tree; do
        for d in "$tree"/*/; do
            [ -d "$d" ] || continue
            if grep -rql -iE 'zeroconf|pybonjour' --include='*.py' "$d" 2>/dev/null; then
                MDNS_IDS="$MDNS_IDS $(basename "$d")"
            fi
        done
    done < <(find "$APP/Contents" -maxdepth 4 -type d -name plugins 2>/dev/null)
    IDS=""
    for id in $SEED_ID $MDNS_IDS; do
        case " $IDS " in *" $id "*) ;; *) IDS="$IDS $id" ;; esac
    done
    IDS="${IDS# }"
    echo "plugins to disable: $IDS"

    CONFIG_DIR="$HOME/Library/Application Support/cura/$MM"
    CONFIG_DIR_FILE="$CONFIG_DIR/cura.cfg"
    # The machine comes from the SHARED fixture the Linux gate seeds from
    # (tests/harness/config/cura/), never a second copy of the same
    # numbers here: the seed has to render the identical plate on every
    # platform, and two copies would drift. The fixture's top level maps
    # onto the storage root the way ui_test.sh maps it onto the XDG data
    # root - on macOS and Windows that root IS the config root.
    FIXTURE_DIR="$ROOT/tests/harness/config/cura/$MM"
    # The fixture is written for 5.13 (its own directory name); another
    # version takes the same files, since every version line is rewritten
    # from the installed build below.
    [ -d "$FIXTURE_DIR/machine_instances" ] ||
        FIXTURE_DIR="$ROOT/tests/harness/config/cura/5.13"
    [ -d "$FIXTURE_DIR/machine_instances" ] ||
        die "the shared machine seed fixture is missing (looked for cura/$MM and cura/5.13 under $ROOT/tests/harness/config)"
    # The plugin's own seeded state - its per-machine json, its settings
    # and the migration flags - rides the config-root fixture the Linux
    # gate seeds, so both halves of that gate's state are here too. On
    # this platform the config root and the storage root are the same
    # directory, which is why one target serves both fixtures.
    PREF_FIXTURE_DIR="$ROOT/tests/harness/config/config/cura/$MM"
    [ -d "$PREF_FIXTURE_DIR" ] ||
        PREF_FIXTURE_DIR="$ROOT/tests/harness/config/config/cura/5.13"
    [ -d "$PREF_FIXTURE_DIR" ] ||
        die "the shared preferences seed fixture is missing (looked for config/cura/$MM and config/cura/5.13 under $ROOT/tests/harness/config)"
    # A fresh tree every run: a config left by an earlier run carries the
    # plugin's own state, and the harness is meant to boot from a known
    # state rather than from whatever the last run wrote.
    rm -rf "$CONFIG_DIR"
    mkdir -p "$CONFIG_DIR"
    cp -R "$PREF_FIXTURE_DIR/." "$CONFIG_DIR/" || die "could not copy the preferences seed from $PREF_FIXTURE_DIR"
    cp -R "$FIXTURE_DIR/." "$CONFIG_DIR/" || die "could not copy the machine seed from $FIXTURE_DIR"
    # The machine the fixture seeds, read out of the fixture rather than
    # repeated here: the stack's own id is what cura.cfg must name.
    MACHINE_CFG="$(find "$CONFIG_DIR/machine_instances" -name '*.global.cfg' -print -quit || true)"
    [ -n "$MACHINE_CFG" ] || die "the machine seed carries no machine_instances/*.global.cfg"
    MACHINE_FILE="$(basename "$MACHINE_CFG" .global.cfg)"
    MACHINE="$(sed -n 's/^id = //p' "$MACHINE_CFG" | head -1)"
    [ -n "$MACHINE" ] || MACHINE="$(printf '%s' "$MACHINE_FILE" | tr '+' ' ')"
    # Every container file must carry THIS build's formats: Cura's
    # registry refuses an instance container whose setting_version is not
    # the running build's (CuraContainerRegistry.addContainer), and the
    # fixture is written for the 5.13 it lives under. Nothing is trusted
    # as shipped - stacks and instances carry different versions (6 and 4
    # across 5.7-5.13), so each file gets the one its own type uses.
    SEED_FILES=0
    for seed_path in "$CONFIG_DIR"/machine_instances/*.cfg "$CONFIG_DIR"/extruders/*.cfg \
                     "$CONFIG_DIR"/definition_changes/*.cfg "$CONFIG_DIR"/user/*.cfg; do
        [ -f "$seed_path" ] || continue
        case "$seed_path" in
            */machine_instances/*|*/extruders/*) SEED_BUILD_VER="$CS_VER" ;;
            *) SEED_BUILD_VER="$IC_VER" ;;
        esac
        sed -e "s/^version = .*/version = $SEED_BUILD_VER/" \
            -e "s/^setting_version = .*/setting_version = $SET_VER/" \
            "$seed_path" >"$seed_path.tmp" || die "could not rewrite $seed_path"
        mv "$seed_path.tmp" "$seed_path"
        SEED_FILES=$((SEED_FILES + 1))
    done
    # cura.cfg comes from the fixture, so only the lines this run owns are
    # rewritten and the plugin state and section set the Linux gate seeds
    # stand. disabled_plugins is dropped wherever it is and re-emitted
    # under [general], which is where Uranium reads it.
    awk -v ver="$PREF_VER" -v ids="$IDS" -v am="$MACHINE" '
        BEGIN { sec = "" }
        /^\[/ { sec = $0; print; if (sec == "[general]") print "disabled_plugins = " ids; next }
        sec == "[general]" && /^disabled_plugins = / { next }
        sec == "[general]" && /^version = / { print "version = " ver; next }
        sec == "[cura]" && /^active_machine = / { print "active_machine = " am; next }
        { print }
    ' "$CONFIG_DIR_FILE" >"$CONFIG_DIR_FILE.tmp" || die "could not rewrite $CONFIG_DIR_FILE"
    mv "$CONFIG_DIR_FILE.tmp" "$CONFIG_DIR_FILE"
    if ! grep -q '^active_machine = ' "$CONFIG_DIR_FILE"; then
        # A second [cura] section would make Cura's parser reject the whole
        # file, and no active machine is the wizard, so this is fatal.
        if grep -q '^\[cura\]' "$CONFIG_DIR_FILE"; then
            die "the preferences seed carries a [cura] section with no active_machine, and appending another would duplicate the section"
        fi
        printf '\n[cura]\nactive_machine = %s\n' "$MACHINE" >>"$CONFIG_DIR_FILE"
    fi
    SEED_VOLUME="$(grep -h '^machine_\(width\|depth\|height\) = ' "$CONFIG_DIR"/definition_changes/*.inst.cfg 2>/dev/null |
        sed 's/.* = //' | paste -sd x - 2>/dev/null || true)"
    echo "machine seed: machine '$MACHINE' from $FIXTURE_DIR"
    echo "  $SEED_FILES container files + cura.cfg (from $PREF_FIXTURE_DIR) under $CONFIG_DIR"
    echo "  build volume ${SEED_VOLUME:-<unset>} | setting_version $SET_VER | disabled_plugins=$IDS"

    # The two-boot legs boot from a state the committed fixture is NOT:
    # the first-install leg's first boot must be a machine that has never
    # run the plugin, and the migration leg's first boot must still carry
    # the v1 blob. The transform is the container leg's own
    # (tests/harness/seed_variants.py), applied to this platform's config
    # dir, and it fails a leg that would otherwise boot the fixture and
    # prove nothing.
    case "$SCENARIO" in
        firstinstall) SEED_VARIANT=clean ;;
        migration) SEED_VARIANT=premigration ;;
        *) SEED_VARIANT="" ;;
    esac
    if [ -n "$SEED_VARIANT" ]; then
        python3 "$ROOT/tests/harness/seed_variants.py" "$SEED_VARIANT" "$CONFIG_DIR" ||
            die "could not apply the $SEED_VARIANT seed"
    fi

    mkdir -p "$STASH"
    MOVED=""
    while IFS= read -r tree; do
        for id in $IDS; do
            d="$tree/$id"
            [ -d "$d" ] || continue
            if sudo mv "$d" "$STASH/$id" 2>/dev/null; then
                MOVED="$MOVED $id"
            else
                warn "could not move $d out of the bundle - the disabled list is the only mechanism left"
            fi
        done
    done < <(find "$APP/Contents" -maxdepth 4 -type d -name plugins 2>/dev/null)
    echo "moved out of the bundle:${MOVED:- <none>}"

    # --- 6. stage the plugin and the driver -------------------------------
    # Both halves are what the harness drives: the built plugin under
    # test, and tests/harness/driver copied in as HarnessDriver (the
    # directory name IS the plugin id the runner addresses).
    echo
    echo "--- staging plugins ---"
    PACKAGE="$ROOT/dist/MoonrakerPrintFollower-v$PLUGIN_VERSION.curapackage"
    [ -f "$PACKAGE" ] || die "$PACKAGE is missing (run make package)"
    PLUGIN_DIR="$CONFIG_DIR/plugins"
    rm -rf "$PLUGIN_DIR/MoonrakerPrintFollower" "$PLUGIN_DIR/HarnessDriver"
    mkdir -p "$PLUGIN_DIR"
    rm -rf "$WORK_DIR/pkg_stage"
    mkdir -p "$WORK_DIR/pkg_stage"
    unzip -q -o "$PACKAGE" -d "$WORK_DIR/pkg_stage" 'files/plugins/*' ||
        die "could not unzip $PACKAGE"
    cp -R "$WORK_DIR/pkg_stage/files/plugins/MoonrakerPrintFollower" "$PLUGIN_DIR/" ||
        die "the package carried no files/plugins/MoonrakerPrintFollower"
    cp -R "$ROOT/tests/harness/driver" "$PLUGIN_DIR/HarnessDriver" ||
        die "tests/harness/driver could not be staged as HarnessDriver"
    find "$PLUGIN_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    [ -f "$PLUGIN_DIR/MoonrakerPrintFollower/plugin.json" ] ||
        die "the staged plugin has no plugin.json"
    [ -f "$PLUGIN_DIR/HarnessDriver/plugin.json" ] ||
        die "the staged driver has no plugin.json"
    echo "staged $PLUGIN_DIR/MoonrakerPrintFollower and $PLUGIN_DIR/HarnessDriver"

    # The driver's clicks are QTest injections (tests/harness/driver:
    # qclick), and the click-free legs passed while every clicking one died
    # with "QtTest injection unavailable". UltiMaker's build of PyQt6
    # carries only the modules Cura uses and QtTest is not one of them, so
    # `from PyQt6 import QtTest` cannot succeed; the driver's fallback then
    # looks for the wheel's binding, and no leg stages one. Qt's own
    # QtTest.framework IS in the bundle - only the binding is missing - and
    # the wheel's binding resolves its framework through @loader_path from
    # the same package dir every other binding in this bundle uses, so one
    # file restores the import path the driver tries first.
    echo
    echo "--- the QtTest binding (the driver's click path) ---"
    QT_PKG="$APP/Contents/Frameworks/PyQt6"
    QT_LIB="$QT_PKG/Qt6/lib"
    QT_TEST="$QT_PKG/QtTest.abi3.so"
    echo "package dir : $QT_PKG"
    if [ -f "$QT_TEST" ]; then
        echo "binding     : already there ($QT_TEST)"
    elif [ ! -f "$QT_PKG/QtCore.abi3.so" ]; then
        # Staging into a directory the port does not import from would look
        # like success and change nothing.
        warn "$QT_PKG carries no QtCore.abi3.so - not the PyQt6 package dir the port imports, so the binding was not staged"
    else
        # Versions are read out of the installed build, never assumed.
        # Both reads are allowed to fail (pipefail is on): an absent or
        # unreadable version has to reach the warn below, not kill the leg
        # before it can report anything.
        QT_VER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
            "$QT_LIB/QtCore.framework/Versions/A/Resources/Info.plist" 2>/dev/null | tr -d '[:space:]' || true)"
        if [ -z "$QT_VER" ]; then
            warn "the bundle's Qt version could not be read from $QT_LIB/QtCore.framework - no wheel to match the binding against"
        else
            echo "bundle Qt   : $QT_VER (PyQt6's own version tracks it; the bundle's log reports PyQt6 $QT_VER)"
            # Keyless and no pip: the version's metadata names the wheel.
            QT_WHEEL="$INSTALLER_DIR/PyQt6-$QT_VER-macos.whl"
            QT_URL="$(curl -sSL --max-time 60 "https://pypi.org/pypi/PyQt6/$QT_VER/json" 2>/dev/null |
                grep -o 'https://files\.pythonhosted\.org/[^"]*universal2\.whl' | head -1 || true)"
            if [ -z "$QT_URL" ]; then
                warn "PyPI lists no universal2 wheel for PyQt6 $QT_VER - the binding was not staged"
            elif ! curl -sSL --fail --retry 3 --retry-delay 5 --connect-timeout 30 \
                    --max-time 300 -o "$QT_WHEEL" "$QT_URL"; then
                warn "the PyQt6 $QT_VER wheel could not be downloaded from $QT_URL"
            elif ! sudo unzip -o -q -j "$QT_WHEEL" 'PyQt6/QtTest.abi3.so' -d "$QT_PKG"; then
                warn "the PyQt6 $QT_VER wheel carried no PyQt6/QtTest.abi3.so"
            else
                echo "binding     : staged $QT_TEST ($(stat -f%z "$QT_TEST") bytes)"
            fi
            rm -f "$QT_WHEEL"
        fi
    fi
    if [ -e "$QT_LIB/QtTest.framework/Versions/A/QtTest" ]; then
        echo "framework   : $QT_LIB/QtTest.framework (the binding's own rpath target)"
    else
        warn "no QtTest.framework at $QT_LIB - the binding would not resolve it"
    fi

    # --- 6b. the suite's test model --------------------------------------
    # tools/ui_test.sh stages the cube into the container's work dir, which
    # IS /tmp/mpf, so the driver's insert-model path resolved there. Here
    # there is no container: /tmp/mpf does not exist, and the probes that
    # write their census there would fail mid-scenario. The cube goes into
    # the work dir, and HARNESS_SCRATCH_DIR below tells the runner that it,
    # not /tmp/mpf, is the shared scratch.
    echo
    echo "--- the suite's test model ---"
    mkdir -p "$WORK_DIR/models"
    if cp "$ROOT/tests/harness/models/voron_cube.stl" "$WORK_DIR/models/voron_cube.stl"; then
        echo "model       : $WORK_DIR/models/voron_cube.stl ($(stat -f%z "$WORK_DIR/models/voron_cube.stl") bytes)"
    else
        warn "the suite's test model could not be staged at $WORK_DIR/models/voron_cube.stl"
    fi

    # --- 7. the plugin's network peer -------------------------------------
    # The seeded machine records point at 127.0.0.1:7125, and the runner's
    # own /harness/* calls go to the same port. The simulator has to be up
    # BEFORE Cura boots - the plugin connects during its own startup, and
    # the runner's boot gate waits for the discovery chain that needs it.
    # tools/ui_test.sh starts it on the Linux leg; nothing did here, and
    # every native leg died on the runner's first simulator call with the
    # port refused.
    SIM_PORT=7125
    # A directory this run owns, on the simulator's PYTHONPATH. Installing
    # into it rather than into whatever environment pip resolves keeps the
    # dependency beside the run and inside the interpreter that serves.
    SIM_SITE="$WORK_DIR/pysite"
    PYTHON="$(command -v python3 2>/dev/null || echo python3)"
    # The interpreter that serves is the one that has to import it, so every
    # check below runs against it with the same PYTHONPATH the launch uses.
    sim_import_ok() {
        PYTHONPATH="$SIM_SITE${PYTHONPATH:+:$PYTHONPATH}" \
            "$PYTHON" -c 'import tornado' >/dev/null 2>&1
    }
    if [ "$SCENARIO" = "real" ]; then
        note "real mode: no simulator - the seeded record points at the real host"
    else
        # A stale instance from an earlier run would keep serving old code.
        pkill -f 'simulator_serve[.]py' 2>/dev/null || true
        sleep 0.5
        if ! sim_import_ok; then
            note "tornado is absent - installing it into $SIM_SITE (the simulator's only dependency)"
            # Unquiet on purpose: on failure pip's last lines carry the
            # reason, and on success they show what was written where.
            if ! "$PYTHON" -m pip install --disable-pip-version-check \
                    --target "$SIM_SITE" tornado 2>&1 | tail -4; then
                die "tornado could not be installed with $PYTHON - the simulator cannot start"
            fi
        fi
        # Re-checked against that exact interpreter and PYTHONPATH before
        # anything is launched, rather than trusting the installer's exit.
        if ! sim_import_ok; then
            die "$PYTHON cannot import tornado with PYTHONPATH=$SIM_SITE - the simulator cannot start"
        fi
        # The checkout's own simulator, not a staged copy: it is the version
        # under test and this host has the tree.
        PYTHONPATH="$SIM_SITE${PYTHONPATH:+:$PYTHONPATH}" \
            nohup "$PYTHON" "$ROOT/tests/harness/simulator_serve.py" "$SIM_PORT" \
            >"$WORK_DIR/simulator.log" 2>&1 &
        SIM_UP="no"
        for _ in $(seq 1 50); do
            if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$SIM_PORT/ledger', timeout=2)" >/dev/null 2>&1; then
                SIM_UP="yes"
                break
            fi
            sleep 0.2
        done
        # Readiness by check, not by luck, and a failure here is fatal: every
        # scenario would otherwise run against a dead peer and fail somewhere
        # far from the cause. The log goes to the job's output with the
        # verdict - the file itself is not part of the uploaded evidence.
        if [ "$SIM_UP" != yes ]; then
            warn "--- $WORK_DIR/simulator.log (tail) ---"
            tail -20 "$WORK_DIR/simulator.log" 2>/dev/null || true
            die "the simulator never answered on 127.0.0.1:$SIM_PORT"
        fi
        note "simulator up on 127.0.0.1:$SIM_PORT (log: $WORK_DIR/simulator.log)"
    fi

    # --- 8. launch --------------------------------------------------------
    echo
    echo "--- launching Cura ---"
    # A stale instance shares the config tree and the port file, and
    # fights over the per-machine record - it goes before the new boot.
    pkill -f 'UltiMaker-Cur[a]' 2>/dev/null || true
    sleep 1
    mkdir -p "$HARNESS_RPC_DIR"
    # The driver's port and token files are per-boot: a stale one would
    # let the wait below pass before Cura is up.
    rm -f "$HARNESS_RPC_DIR/harness_port.txt" "$HARNESS_RPC_DIR/harness_token.txt"
    mkdir -p "$LEGACY_RPC_DIR" 2>/dev/null || true
    rm -f "$LEGACY_RPC_DIR/harness_port.txt" "$LEGACY_RPC_DIR/harness_token.txt" 2>/dev/null || true
    if [ "$STAGE_ONLY" = yes ]; then
        note "--stage-only: everything is staged and the display is set; Cura was not launched"
        exit 0
    fi
    # The launch env the harness needs. HARNESS_RPC_DIR is where the
    # driver writes its port and token; the runner reads the same files.
    export HARNESS_GEOMETRY HARNESS_WINDOW
    ( cd "$APP/Contents/MacOS" && nohup "$BIN" ) >"$WORK_DIR/cura_run.log" 2>&1 &
    CURA_PID=$!
    echo "launched $BIN (pid $CURA_PID), output in $WORK_DIR/cura_run.log"

    # The driver's port file is the ready marker: it appears once Cura
    # has loaded the staged plugin, so it is the one signal that says the
    # runner has something to talk to.
    RPC_FILE=""
    wait_for_boot() {
        local tick start
        start=$(date +%s)
        for tick in $(seq 1 300); do
            if [ -s "$HARNESS_RPC_DIR/harness_port.txt" ]; then
                RPC_FILE="$HARNESS_RPC_DIR/harness_port.txt"
                return 0
            fi
            if [ -s "$LEGACY_RPC_DIR/harness_port.txt" ]; then
                RPC_FILE="$LEGACY_RPC_DIR/harness_port.txt"
                return 0
            fi
            case $((tick % 30)) in
                0) note "still booting ($(( $(date +%s) - start ))s)" ;;
            esac
            sleep 1
        done
        return 1
    }
    if ! wait_for_boot; then
        warn "the driver never came up - Cura did not load the staged plugin"
        if pgrep -f 'UltiMaker-Cur[a]' >/dev/null; then
            warn "cura is still running, so the boot did not finish within the deadline"
        else
            warn "cura is not running - the boot crashed or exited"
        fi
        warn "--- $WORK_DIR/cura_run.log (tail) ---"
        tail -40 "$WORK_DIR/cura_run.log" 2>/dev/null || true
        exit 1
    fi
    note "driver up: $RPC_FILE"

    # --- 8b. the renderer the boot actually got ---------------------------
    # Recorded, never fatal. Cura's own probe (UM/View/GL/OpenGLContext.py,
    # CURA-6092) declines a software-backed 4.1 core context ON macOS ONLY
    # - the check is gated on Platform.isOSX() rather than on the premise -
    # and this runner's GL is Apple's software renderer, so the boot lands
    # on 2.1 "No profile". Forcing 4.1 back with view/opengl_version_detect
    # = force_modern does NOT fix what that costs here: on this renderer
    # the window stops presenting either way, and the forced path is the
    # one Cura calls "much slower", which measured 3.5x on the layer-view
    # unit (group-printing, 2.8 -> 9.7 min) against a 15-minute budget.
    # The legs therefore run without capture (see tests/harness/runner.py)
    # and this line is what says which renderer their steps ran on.
    GL_LINE="$(grep -h 'Detected most suitable OpenGL context version:' \
        "$CONFIG_DIR/cura.log" 2>/dev/null | tail -1 || true)"
    case "$GL_LINE" in
        *"4.1 Core profile"*)
            note "renderer: ${GL_LINE##*: }" ;;
        "")
            note "renderer: unreadable - no OpenGL context line in $CONFIG_DIR/cura.log" ;;
        *)
            note "renderer: ${GL_LINE##*: } (Cura's macOS software fallback; the legs capture nothing, so this is recorded, not judged)" ;;
    esac

    # --- 9. keep the window inside the capture area -----------------------
    # The capture is the whole display, so a window hanging off the edge
    # is CLIPPED and nothing in the recording says so. If it cannot fit
    # at its pinned size it is SHRUNK to fit: a complete smaller window
    # is usable evidence where a clipped one is not. The reads go through
    # System Events, which may itself need an Automation grant, so a
    # refusal is reported rather than assumed away.
    DISP_AFTER="$(read_res)"
    DISP_W="${DISP_AFTER%%x*}"
    DISP_H="${DISP_AFTER##*x}"
    case "${DISP_W}${DISP_H}" in
        ''|*[!0-9]*)
            DESKTOP="$(osascript -e 'tell application "Finder" to get bounds of window of desktop' 2>&1 || true)"
            DISP_W="$(printf '%s' "$DESKTOP" | cut -d, -f3 | tr -dc '0-9')"
            DISP_H="$(printf '%s' "$DESKTOP" | cut -d, -f4 | tr -dc '0-9')"
            ;;
    esac
    case "${DISP_W}${DISP_H}" in
        ''|*[!0-9]*) DISP_W=""; DISP_H="" ;;
    esac
    WPROC="$(osascript -e 'tell application "System Events" to get name of every process whose name contains "Cura"' 2>&1 |
        tr ',' '\n' | sed -n '/[Cc]ura/p' | head -1 | sed 's/^ *//;s/ *$//' || true)"
    if [ -z "$WPROC" ]; then
        warn "no window process could be read (System Events may be ungranted); the window is not checked or moved"
    elif [ -z "$DISP_W" ] || [ -z "$DISP_H" ]; then
        warn "the display this run captures could not be measured; the window is not checked against an invented bound"
    else
        read_window() {
            local wpos wsize
            wpos="$(osascript -e "tell application \"System Events\" to tell process \"$WPROC\" to get position of window 1" 2>&1 || true)"
            wsize="$(osascript -e "tell application \"System Events\" to tell process \"$WPROC\" to get size of window 1" 2>&1 || true)"
            # An osascript refusal answers with words - "Invalid index.
            # (-1719)" - and `tr -dc '0-9-'` would keep the error code as
            # if it were a coordinate, which the digits-only guard below
            # accepts. An answer with a letter in it is no answer.
            case "$wpos$wsize" in
                *[A-Za-z]*) wpos=""; wsize="" ;;
            esac
            WPX="$(printf '%s' "$wpos" | cut -d, -f1 | tr -dc '0-9-')"
            WPY="$(printf '%s' "$wpos" | cut -d, -f2 | tr -dc '0-9-')"
            WPW="$(printf '%s' "$wsize" | cut -d, -f1 | tr -dc '0-9-')"
            WPH="$(printf '%s' "$wsize" | cut -d, -f2 | tr -dc '0-9-')"
        }
        WPX=""; WPY=""; WPW=""; WPH=""
        read_window
        echo "window 1 of '$WPROC': ${WPX:-?},${WPY:-?} ${WPW:-?}x${WPH:-?} (display ${DISP_W}x${DISP_H}, pin $HARNESS_WINDOW)"
        case "$WPX$WPY$WPW$WPH" in
            ''|*[!0-9-]*)
                warn "the window rectangle could not be read; it cannot be checked against the display" ;;
            *)
                in_bounds() {
                    [ "$WPX" -ge 0 ] && [ "$WPY" -ge 0 ] &&
                        [ $((WPX + WPW)) -le "$DISP_W" ] && [ $((WPY + WPH)) -le "$DISP_H" ]
                }
                if in_bounds; then
                    note "window in bounds: $WPX,$WPY ${WPW}x${WPH} inside ${DISP_W}x${DISP_H}"
                elif [ "$WPW" -gt "$DISP_W" ] || [ "$WPH" -gt "$DISP_H" ]; then
                    NEWW="$WPW"; [ "$NEWW" -gt "$DISP_W" ] && NEWW="$DISP_W"
                    NEWH="$WPH"; [ "$NEWH" -gt "$DISP_H" ] && NEWH="$DISP_H"
                    echo "the window (${WPW}x${WPH}) is larger than the display; shrinking it to ${NEWW}x${NEWH} at 0,0"
                    osascript -e "tell application \"System Events\" to tell process \"$WPROC\" to set size of window 1 to {$NEWW, $NEWH}" >/dev/null 2>&1 || true
                    osascript -e "tell application \"System Events\" to tell process \"$WPROC\" to set position of window 1 to {0, 0}" >/dev/null 2>&1 || true
                    sleep 2
                    read_window
                    if ! in_bounds; then
                        # The frame carries a border the size read does not
                        # include, so the second trim uses the overflow this
                        # move actually measured rather than a guess.
                        OVERW=$((WPX + WPW - DISP_W)); [ "$OVERW" -lt 0 ] && OVERW=0
                        OVERH=$((WPY + WPH - DISP_H)); [ "$OVERH" -lt 0 ] && OVERH=0
                        NEWW2=$((WPW - OVERW)); [ "$NEWW2" -lt 1 ] && NEWW2=1
                        NEWH2=$((WPH - OVERH)); [ "$NEWH2" -lt 1 ] && NEWH2=1
                        echo "still overflowing by ${OVERW}x${OVERH}: trimming to ${NEWW2}x${NEWH2}"
                        osascript -e "tell application \"System Events\" to tell process \"$WPROC\" to set size of window 1 to {$NEWW2, $NEWH2}" >/dev/null 2>&1 || true
                        sleep 2
                        read_window
                    fi
                    if in_bounds; then
                        note "window shrunk into the display: $WPX,$WPY ${WPW}x${WPH}"
                    else
                        warn "the window is still larger than the display at $WPX,$WPY ${WPW}x${WPH} - Cura enforces a minimum window size, so the capture may clip it; reported rather than retried"
                    fi
                else
                    NEWX="$WPX"; NEWY="$WPY"
                    [ "$NEWX" -lt 0 ] && NEWX=0
                    [ $((NEWX + WPW)) -gt "$DISP_W" ] && NEWX=$((DISP_W - WPW))
                    [ "$NEWY" -lt 0 ] && NEWY=0
                    [ $((NEWY + WPH)) -gt "$DISP_H" ] && NEWY=$((DISP_H - WPH))
                    echo "moving the window to $NEWX,$NEWY (size kept at ${WPW}x${WPH})"
                    osascript -e "tell application \"System Events\" to tell process \"$WPROC\" to set position of window 1 to {$NEWX, $NEWY}" >/dev/null 2>&1 || true
                    sleep 2
                    read_window
                    if in_bounds; then
                        note "window moved inside the display: $WPX,$WPY ${WPW}x${WPH}"
                    else
                        warn "the window is outside the display at $WPX,$WPY ${WPW}x${WPH} and the move did not bring it back"
                    fi
                fi ;;
        esac
    fi

    # --- 10. hand over to the runner --------------------------------------
    # The mode the third argument names: a runner mode is used as it
    # stands, anything else is a suite group.
    RUNNER_MODE="suite"
    RUNNER_GROUP=""
    case "$SCENARIO" in
        -|"") RUNNER_MODE="scenario" ;;
        scenario|suite|firstinstall|migration|discover|real) RUNNER_MODE="$SCENARIO" ;;
        scenario[0-9]|scenario1[01]) RUNNER_MODE="$SCENARIO" ;;
        *) RUNNER_GROUP="$SCENARIO" ;;
    esac
    ENV_FILE="$WORK_DIR/harness_env.sh"
    {
        echo "# source this before running tests/harness/runner.py"
        echo "export HARNESS_RPC_DIR='$HARNESS_RPC_DIR'"
        echo "export HARNESS_GEOMETRY='$HARNESS_GEOMETRY'"
        echo "export HARNESS_WINDOW='$HARNESS_WINDOW'"
        echo "export HARNESS_RUN_DIR='$ARTIFACT_DIR'"
        # The two-boot legs relaunch the app between their boots, and the
        # runner is the only process left to do it: the binary and the
        # directory it must run in, both read back from the launch above.
        echo "export HARNESS_CURA_BIN='$BIN'"
        echo "export HARNESS_CURA_CWD='$(dirname "$BIN")'"
        # Where Cura keeps its own log: the runner harvests it next to the
        # evidence (there is no ui_test.sh on this host to do it), and a
        # driver that never answered is read out of that file.
        echo "export HARNESS_CURA_CONFIG='$CONFIG_DIR'"
        echo "export MPF_WORK_DIR='$WORK_DIR'"
        # The scratch the driver's probes share with the runner: the
        # container legs get theirs from the /tmp/mpf mount, and here the
        # work dir is the only path both Cura and the runner can reach.
        echo "export HARNESS_SCRATCH_DIR='$WORK_DIR'"
        echo "export CURA_VERSION='$CURA_VERSION'"
        echo "export PLUGIN_VERSION='$PLUGIN_VERSION'"
        echo "export HARNESS_MODE='$RUNNER_MODE'"
        [ -n "$RUNNER_GROUP" ] && echo "export SCENARIO_GROUP='$RUNNER_GROUP'"
        # This leg captures nothing, and says why in its own evidence.
        # A CI mac has no GPU — a VM exposes none to the guest's GL
        # stack — so Cura lands on Apple's software renderer (see
        # `--- 8b ---`) and on it the window stops presenting partway
        # through a leg: the stills go byte-identical while the menu bar
        # and the dock keep ticking, and a real click on Cura's own
        # stage header changes nothing on screen. Every step assertion
        # is answered in-process from the QML tree, so the pictures are
        # the whole of what is lost: the static verdict and the display
        # guard read them and stand down with them. The heartbeat is NOT
        # in that set — it makes a frame due by changing an item in the
        # window's own scene graph and waits for the answer, and it runs
        # here in both modes. What a missed heartbeat MEANS here is the
        # other half: with no pictures to read beside it and a renderer
        # known to present while its pixels stay stale, a miss is
        # recorded and announced as a report-only diagnostic and no
        # renderer coverage is claimed for this platform on the strength
        # of it. A Mac with a real GPU presents normally, which is why
        # this rides the leg rather than the platform: HARNESS_CAPTURE=on
        # restores the pictures here — and with them the judged verdict —
        # for a local look.
        echo "export HARNESS_CAPTURE='off'"
        # No apostrophes: this file is SOURCED, and one would close the
        # quote early and silently truncate the reason.
        echo "export HARNESS_CAPTURE_REASON='macOS runs without pictures by design. This runner has no GPU, so Cura boots the Apple software renderer and the window stops presenting partway through a leg. The pictures and the static verdict stand down with the recorder; the renderer-liveness heartbeat still runs and a miss is reported by name, but it is a report-only diagnostic here rather than a failure, and it is not renderer coverage. See TESTING.md.'"
    } >"$ENV_FILE"
    mkdir -p "$ARTIFACT_DIR"
    echo
    note "Cura $CURA_VERSION is running with the plugin and the driver staged"
    note "config: $CONFIG_DIR | driver port file: $RPC_FILE"
    note "gallery goes to: $ARTIFACT_DIR/index.html"
    note "run the harness with:"
    if [ -n "$RUNNER_GROUP" ]; then
        note "  . '$ENV_FILE' && '$PYTHON' '$ROOT/tests/harness/runner.py' $RUNNER_MODE '$RUNNER_GROUP'"
    else
        note "  . '$ENV_FILE' && '$PYTHON' '$ROOT/tests/harness/runner.py' $RUNNER_MODE"
    fi
} 2>&1 | tee "$LOG"
