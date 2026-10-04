#!/usr/bin/env bash
# Auto-Shorts VPS bootstrap: fresh install AND updater in one idempotent script.
#
# Usage:
#   bash scripts/auto_shorts/setup_vps.sh [install-dir]
#
# install-dir defaults to ~/autoclip-shorts. Safe to re-run: it pulls the
# latest code, refreshes deps, and reinstalls the cron line without ever
# touching your filled-in keys.
#
# What it does, in order:
#   1. apt-installs python3/venv/ffmpeg/git/flock when missing
#   2. clones feat/auto-shorts-vps, or `git pull --ff-only` if present
#   3. creates venv, installs requirements + editable `autoclip` package
#   4. creates .env.auto_shorts from env.example only if missing (chmod 600)
#   5. runs `autoclip doctor` + one `--dry-run` preflight (keys required)
#   6. installs the daily flock cron line idempotently
set -euo pipefail

REPO_URL="${AUTO_SH_REPO_URL:-https://github.com/adarshj322/autoclip-shorts.git}"
BRANCH="${AUTO_SH_BRANCH:-feat/auto-shorts-vps}"
INSTALL_DIR="${1:-$HOME/autoclip-shorts}"
VENV="$INSTALL_DIR/venv"
ENV_FILE="$INSTALL_DIR/.env.auto_shorts"
CRON_MARK="# auto-shorts-vps daily"

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# ------------------------------------------------------------------ 1. system
need_apt=()
for bin in python3 ffmpeg git flock; do
    command -v "$bin" >/dev/null 2>&1 || need_apt+=("$bin")
done
# 'flock' ships with util-linux; python3-venv provides ensurepip/venv
if [ "${#need_apt[@]}" -gt 0 ]; then
    log "installing missing system packages: ${need_apt[*]}"
    sudo apt update
    sudo apt install -y python3 python3-venv ffmpeg git util-linux cron
fi

# ------------------------------------------------------------------ 2. code
if [ -d "$INSTALL_DIR/.git" ]; then
    log "updating code in $INSTALL_DIR ($BRANCH)"
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
    log "cloning $REPO_URL ($BRANCH) into $INSTALL_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ------------------------------------------------------------------ 3. venv
if [ ! -x "$VENV/bin/python" ]; then
    log "creating venv"
    python3 -m venv "$VENV"
fi
log "installing python deps (may take a few minutes on first run)"
"$VENV/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
"$VENV/bin/pip" install --quiet -e "$INSTALL_DIR"
[ -x "$VENV/bin/autoclip" ] || die "venv install failed: $VENV/bin/autoclip missing"

# ------------------------------------------------------------------ 4. env
if [ ! -f "$ENV_FILE" ]; then
    log "creating $ENV_FILE from env.example (fill in your keys!)"
    cp "$INSTALL_DIR/scripts/auto_shorts/env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
else
    log "$ENV_FILE exists, leaving your keys untouched"
    chmod 600 "$ENV_FILE"
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
if [ -z "${YT_API_KEY:-}" ] || [ -z "${UPLOAD_POST_API_KEY:-}" ]; then
    log "keys are still empty in $ENV_FILE"
    log "fill them in, re-source, then re-run this script for steps 5-6:"
    log "  nano $ENV_FILE"
    exit 2
fi

# ------------------------------------------------------- 5. preflight (keys)
# Gate: doctor must pass and the dry-run must not report a CONFIG error
# (exit 2). A partial dry-run (exit 1, e.g. a transiently skipped video)
# still proceeds to cron setup; config errors stop here with the log path.
export PATH="$INSTALL_DIR/venv/bin:$PATH"
DRYRUN_LOG="$INSTALL_DIR/logs/setup-dryrun.log"
mkdir -p "$INSTALL_DIR/logs"
log "running autoclip doctor"
if ! "$VENV/bin/autoclip" doctor 2>&1 | tee "$DRYRUN_LOG"; then
    die "autoclip doctor preflight failed (full output in $DRYRUN_LOG) — cron NOT installed. Fix the error and re-run this script."
fi
log "running one dry-run (downloads/clips/exports, never publishes)"
set +e
"$VENV/bin/python" -m scripts.auto_shorts.run --dry-run --max-per-day 1 >>"$DRYRUN_LOG" 2>&1
dry_rc=$?
set -e
if [ "$dry_rc" -eq 2 ]; then
    die "dry-run preflight failed with a config error (exit 2; full output in $DRYRUN_LOG) — cron NOT installed. Fix the error and re-run this script."
fi
log "dry-run exit $dry_rc (0 = clean, 1 = partial with transient skips); proceeding to cron setup"

# ------------------------------------------------------------------ 6. cron
cron_line="0 9 * * * flock -n /tmp/auto_shorts.lock bash -c 'cd $INSTALL_DIR && set -a && source .env.auto_shorts && set +a && export PATH=\"$INSTALL_DIR/venv/bin:\$PATH\" && venv/bin/python -m scripts.auto_shorts.run' >> $INSTALL_DIR/logs/auto_shorts.log 2>&1 $CRON_MARK"
mkdir -p "$INSTALL_DIR/logs"
tmp_cron="$(mktemp)"
trap 'rm -f "$tmp_cron"' EXIT
crontab -l 2>/dev/null | grep -v "$CRON_MARK" > "$tmp_cron" || true
echo "$cron_line" >> "$tmp_cron"
crontab "$tmp_cron"
log "cron installed (daily 09:00). current auto-shorts lines:"
crontab -l | grep "$CRON_MARK" || true

log "done. Uploads go out PRIVATE; flip to public in YouTube Studio."
