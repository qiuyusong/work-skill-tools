# Setup

## 1) Configure In Skill Directory

Default config file:

`config/timereport-config.json`

Project mapping format:

`projects: [{"name":"your-project-name","path":"your-local-repo-path"}]`

Recommended quick setup:

```bash
# Interactive update
python scripts/configure_timereport.py --interactive

# Or use shortcut on Windows
configure-ecp-timereport.cmd
```

Required-status check before submit:

```bash
python scripts/configure_timereport.py --show-required-status
```

If any required field is missing, the agent should ask the user for only the missing values, write them with `configure_timereport.py`, rerun `--show-required-status`, and only then continue to `fill_timereport.py --submit`.

After the first successful save, the config file stores a `device_binding` fingerprint for the current machine.

You can update values by CLI:

```bash
python scripts/configure_timereport.py \
  --projects "project-a=/path/to/project-a;project-b=/path/to/project-b" \
  --ecp-url "https://econtact.ai3.cloud/ecp" \
  --username "your-username" \
  --password "your-password" \
  --nearby-days 7 \
  --fuzzy-descriptions "优化相关代码;调整了相关业务逻辑;配合前端调整相关接口" \
  --hours 8
```

Manual activity examples:

```bash
# Leave entry: no task association
python scripts/fill_timereport.py --date 2026-03-13 --activity-type 休假 --activity-detail 特休假 --submit

# Meeting entry: keep task association
python scripts/fill_timereport.py --date 2026-03-16 --activity-type 会议 --activity-detail 顾问会议 --submit
```

Task ID is auto-resolved during submit:

1. Call `Ecp.Aile.getOnlineUser.data` to get current `userId`.
2. Call `Ecp.TimeReport.getAllRelevantObjs.data` with `{"userId":"..."}`.
3. Pick current-month task from `taskItems`.

## 2) Holiday Dependency

Workday detection follows mainland holiday rules via `chinesecalendar`:

```bash
python -m pip install chinesecalendar
```

## 3) Automation Prompt Template

Use this prompt in scheduled automations:

```text
Use $ecp-timereport-autofill to fill today's ECP timereport.
Run: python <skill-root>/scripts/fill_timereport.py --submit --branch release
If submission fails, report the error and keep the generated JSON report path for troubleshooting.
```

Rules baked into the script:

- Multiple commits on the same day only use the earliest commit summary.
- If a workday has no direct commit, only nearby dates with multiple commits can be used as fallback.
- Nearby fallback commits skip records already submitted in the current month and pick the next unused one.
- If no nearby multi-commit record remains, the day stays unfilled until a later run unless it is month-end fuzzy fill.
- Leave entries use `休假-xxx` and do not associate a task.
- Meeting entries use `会议-xxx` and keep the current-month task association.
- If `device_binding.fingerprint` is missing or mismatched, the script clears business config values and stops, so the caller can ask the user to refill `projects`, `ecp.username`, and `ecp.password`.

## 4) Legacy Env Vars (Optional Fallback)

`fill_timereport.py` supports these environment variables:

- `ECP_BASE_URL`
- `ECP_USERNAME`
- `ECP_PASSWORD`
- `ECP_TIMEREPORT_REPOS`
- `TIMEREPORT_TOTAL_HOURS`
- `TIMEREPORT_GIT_AUTHOR`
- `TIMEREPORT_OUTPUT_DIR`
- `ECP_ACTIVITY_TYPE`
