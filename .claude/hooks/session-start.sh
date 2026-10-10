#!/bin/bash
# Ensures the tools this repo uses are available in Claude Code cloud sessions:
#   - node + playwright: render runner-bingo/bingo.html to PNG
#   - ffmpeg: edit the videos in edited/
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq ffmpeg >/dev/null
fi

if ! node -e "require.resolve('playwright')" >/dev/null 2>&1; then
  # Chromium is preinstalled; PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD avoids refetching it.
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install -g playwright >/dev/null
  echo "export NODE_PATH=\"$(npm root -g)\${NODE_PATH:+:\$NODE_PATH}\"" >> "$CLAUDE_ENV_FILE"
fi

echo "session-start: ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}'), node $(node -v), playwright ready"
