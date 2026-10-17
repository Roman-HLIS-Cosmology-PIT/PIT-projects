#!/usr/bin/env bash
# GitHub runner only; deploying or changing workspace apps is never done here.
set -euo pipefail
version=4.10.0
archive="${RUNNER_TEMP:?}/pit-slack-cli.tar.gz"
directory="${RUNNER_TEMP}/pit-slack-cli"
mkdir -p "$directory"
curl --fail --silent --show-error --location \
  "https://downloads.slack-edge.com/slack-cli/slack_cli_${version}_linux_amd64.tar.gz" \
  --output "$archive"
echo "9772b17f70c4ec522ea6ae25545220dfe17d6afb5babe28fa1cd55a24b2abffa  $archive" | sha256sum --check --status
tar --no-same-owner -xzf "$archive" -C "$directory" bin/slack
echo "$directory/bin" >> "${GITHUB_PATH:?}"
# CLI datastore commands read this app's manifest via its Deno hooks.
curl --fail --silent --show-error --location \
  https://dl.deno.land/release/v2.2.12/deno-x86_64-unknown-linux-gnu.zip \
  --output "${RUNNER_TEMP}/pit-deno.zip"
echo "bb7ef8ba33d0e02f9f3507e79c3559cfcd31c8a5c64f4f1bb914f3ac83dd66d5  ${RUNNER_TEMP}/pit-deno.zip" | sha256sum --check --status
unzip -q -o "${RUNNER_TEMP}/pit-deno.zip" -d "$directory/bin"
