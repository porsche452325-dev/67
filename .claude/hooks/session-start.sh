#!/bin/bash
# Prepares Claude Code cloud sessions for video editing work in this repo:
# Python packages for tracking/compositing/audio, plus a check that ffmpeg
# (shipped with the base image) is present.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# pip skips packages already at the pinned version, so reruns are fast.
python3 -m pip install --quiet --disable-pip-version-check --root-user-action=ignore -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "session-start: ffmpeg not found; video rendering will not work" >&2
fi
