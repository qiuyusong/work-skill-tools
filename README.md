# work-skill-tools

Codex skills published for reuse.

## Layout

- `skills/<skill-name>`: individual installable skill folders
- `skills/manifest.json`: registry used for one-command installs
- `scripts/install_repo_skills.py`: install one or all skills from this repo

## Install one skill

Use Codex's built-in GitHub installer directly:

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo qiuyusong/work-skill-tools \
  --path skills/ecp-timereport-autofill
```

Windows PowerShell:

```powershell
python $HOME\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py `
  --repo qiuyusong/work-skill-tools `
  --path skills/ecp-timereport-autofill
```

## Install all skills

Download and run the repo installer:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/qiuyusong/work-skill-tools/main/scripts/install_repo_skills.py -OutFile $env:TEMP\install_repo_skills.py
python $env:TEMP\install_repo_skills.py --all
```

Install just one skill through the repo installer:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/qiuyusong/work-skill-tools/main/scripts/install_repo_skills.py -OutFile $env:TEMP\install_repo_skills.py
python $env:TEMP\install_repo_skills.py --skill ecp-timereport-autofill
```

## Publish more skills later

1. Add a new skill folder under `skills/<skill-name>`.
2. Register it in `skills/manifest.json`.
3. Push to GitHub.

`install_repo_skills.py --all` will then include the new skill automatically.
