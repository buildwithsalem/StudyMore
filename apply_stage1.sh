#!/usr/bin/env bash
# Stage 1 updates for StudyMore.
# Run this from the repo root (the folder that contains "src").
set -e

if [ ! -d "src" ]; then
    echo "Run this from the StudyMore repo root (the folder that has 'src' in it)."
    exit 1
fi

BACKUP="backup_before_stage1_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP"
cp -r src "$BACKUP/"
echo "Backed up src/ to $BACKUP/"

git checkout -b feature/stage1-ui-updates 2>/dev/null || git checkout feature/stage1-ui-updates

git apply --reject --whitespace=fix stage1_updates.patch

if [ -f src/app.py.rej ] || [ -f src/static/style.css.rej ]; then
    echo "Some parts of the patch didn't apply cleanly (.rej files were created)."
    echo "Your copy of the repo has likely moved on since this patch was made."
    echo "Check the .rej files under src/ and tell Claude what they say."
    exit 1
fi

echo "Patch applied cleanly on branch feature/stage1-ui-updates."
echo "Next:"
echo "  cd src && python -m unittest discover"
echo "  python -m flask --app app run   (or however you normally run it)"
echo "Then review the changes, commit, push, and open a PR like you did last time."
