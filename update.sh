#!/usr/bin/env bash
# Regenerate gaslimit.pics and push (uses the repo's deploy key via core.sshCommand).
# cron example (hourly):  7 * * * * /home/nero/python/gaslimit.pics/update.sh >> /home/nero/python/gaslimit.pics/update.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only -q
.venv/bin/python create_page.py
git add index.html entities/index.html assets/entity_data.json assets/reg_data.json
git diff --cached --quiet && exit 0
git commit -q -m "Update data $(date -u +'%Y-%m-%d %H:%M') UTC"
git push -q origin main
