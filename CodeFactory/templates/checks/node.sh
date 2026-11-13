#!/usr/bin/env bash
# Checks for a Node / TypeScript project. Adjust the script names to this repo's package.json.
set -euo pipefail

npm ci --prefer-offline --no-audit --no-fund
npm run lint
npm run typecheck
npm test -- --run
npm run build
