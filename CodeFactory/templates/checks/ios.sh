#!/usr/bin/env bash
# Checks for an Xcode project. Fill in the scheme and simulator once, in the variables below.
# Install with the INSTALL.md steps: copy this file to <repo>/factory/checks.sh.
set -euo pipefail

SCHEME="${FACTORY_SCHEME:-CHANGE_ME}"
DESTINATION="${FACTORY_DESTINATION:-platform=iOS Simulator,name=iPhone 17}"
PROJECT_FLAGS=()
if [ -f "${FACTORY_WORKSPACE:-}" ]; then PROJECT_FLAGS=(-workspace "$FACTORY_WORKSPACE"); fi

if [ "$SCHEME" = "CHANGE_ME" ]; then
  echo "factory/checks.sh: set FACTORY_SCHEME (or edit SCHEME) to this project's scheme." >&2
  exit 1
fi

# Lint without fixing: SwiftLint only reports (never pass --fix here).
if command -v swiftlint >/dev/null 2>&1; then
  swiftlint lint --strict --quiet
fi

xcodebuild test \
  "${PROJECT_FLAGS[@]}" \
  -scheme "$SCHEME" \
  -destination "$DESTINATION" \
  -quiet \
  CODE_SIGNING_ALLOWED=NO
