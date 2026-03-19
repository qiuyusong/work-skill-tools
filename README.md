# work-skill-tools

Codex skills published for reuse.

## Layout

- `skills/<skill-name>`: individual installable skill folders
- `skills/manifest.json`: registry used for one-command installs
- `scripts/install_repo_skills.py`: thin wrapper around `npx skills add`

## Install one skill

Use the `skills` CLI directly:

```bash
npx skills add qiuyusong/work-skill-tools --skill ecp-timereport-autofill
```

Windows PowerShell:

```powershell
npx skills add qiuyusong/work-skill-tools --skill ecp-timereport-autofill
```

## Install all skills

Use the `skills` CLI:

```powershell
npx skills add qiuyusong/work-skill-tools --all
```

Install just one skill:

```powershell
npx skills add qiuyusong/work-skill-tools --skill ecp-timereport-autofill
```

For a non-interactive global install to Codex only:

```powershell
npx skills add qiuyusong/work-skill-tools --skill ecp-timereport-autofill --agent codex -g -y
```

## Publish more skills later

1. Add a new skill folder under `skills/<skill-name>`.
2. Register it in `skills/manifest.json`.
3. Push to GitHub.

`npx skills add qiuyusong/work-skill-tools --all` will then include the new skill automatically.
