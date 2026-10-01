# Auto-Shorts VPS wrapper

Daily cron job: find trending AI long-form videos -> cut 1 Short via
AutoClip -> upload to YouTube as **private** (manual review, then flip to
public in YouTube Studio).

New code is isolated to `scripts/auto_shorts/`; the core pipeline is
untouched.

## Setup

```bash
python -m venv /opt/auto-shorts/venv
/opt/auto-shorts/venv/bin/pip install -e /path/to/autoclip
cp scripts/auto_shorts/env.example /opt/auto-shorts/.env
# fill in keys in /opt/auto-shorts/.env (never commit keys)
```

Load the env file before running (example with cron below sources it via
a wrapper, or export the vars in the crontab environment).

## Environment

Copy `env.example`. Orchestrator keys (validated, exit 2 when missing):

- `YT_API_KEY`, `LLM_PROVIDER`, `UPLOAD_POST_API_KEY`, `UPLOAD_POST_USER`
- `AUTOCLIP_DATA_DIR` (default `~/.local/share/AutoClip`)
- `AUTO_SH_MAX_PER_DAY` (default `3`), `AUTO_SH_PRIVACY`
  (`private|unlisted`), `AUTO_SH_KEEP_DAYS` (default `7`)

`API_*` passthrough: `API_DASHSCOPE_API_KEY`, `API_MODEL_NAME`,
`AUTOCLIP_YT_SUBTITLE_LANGS` (and friends like `AUTOCLIP_YT_CLIENT`) are
**not** part of the orchestrator `Config` on purpose — they stay as plain
process environment for the `autoclip` CLI subprocesses (`run`, `export`,
`publish`), which read them directly. Just export them alongside the
orchestrator keys; `run.py` passes the environment through untouched.

## Usage

```bash
python -m scripts.auto_shorts.run --dry-run --max-per-day 1
python -m scripts.auto_shorts.run --max-per-day 3 --keep-days 7 \
  --workdir ~/.local/share/AutoClip
```

Flags: `--dry-run` (download/clip/export, stop before publish),
`--max-per-day`, `--keep-days`, `--workdir` (base dir for
`auto_shorts_ledger.json` + `work/<video_id>/`; defaults to `data_dir`).

Stdout is a single JSON summary for cron logs:

```json
{"ok": true, "dry_run": false,
 "processed": [{"video_id": "...", "project_id": "...",
                "request_id": "...", "status": "published"}],
 "skipped": [{"video_id": "...", "reason": "..."}]}
```

Exit codes: `0` ok (or lock already held -> `already_running`), `1`
partial failure / preflight / runtime error, `2` config error.

Optional channel lists (one channel name per line, `#` comments allowed)
in the base dir: `allowlist_channels.txt` (when present, only these
channels are processed), `blocklist.txt` (always skipped).

## Cron

See `cron.example`. Canonical line (single instance via `flock -n`):

```cron
0 9 * * * flock -n /tmp/auto_shorts.lock bash -c 'cd /path/to/autoclip && /opt/auto-shorts/venv/bin/python -m scripts.auto_shorts.run' >> /opt/auto-shorts/logs/auto_shorts.log 2>&1
```

The `cd /path/to/autoclip &&` prefix sets CWD to the repo root so
`-m scripts.auto_shorts.run` resolves (cron runs with CWD=$HOME).
Rotate `logs/` with logrotate; back up the ledger + `.env` with `tar`
(keys are never committed).

systemd alternative: a `.service` (`Type=oneshot`,
`WorkingDirectory=/path/to/autoclip`, same `bash -c 'cd ... && ...'`
command or `ExecStart` with the repo root as working dir) paired
with a `.timer` (`OnCalendar=daily`, `Persistent=true`) instead of cron.

## Publishing: private first, public by hand

Uploads are `private` (or `unlisted`) only — there is no auto-public
path. After each run, review the Short, then flip it in
**YouTube Studio > Content > Visibility > Public** by hand.

## Reused-content warning

You do not own the source videos. Reposting other creators' content
risks copyright strikes and YouTube's **reused-content** demonetization
policy. Prefer transformative edits, always keep the `Source: <url>`
credit the pipeline adds to the description, respect takedowns via
`blocklist.txt`, and use `allowlist_channels.txt` to limit sources to
channels you have rights or permission for.

## Tests

No `conftest.py` in this plan, so run with `PYTHONPATH=.`:

```bash
PYTHONPATH=. pytest scripts/auto_shorts/tests/ -q
```
