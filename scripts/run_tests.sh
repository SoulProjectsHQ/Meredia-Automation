#!/usr/bin/env sh
# Run all unit tests from the repo root. Needs Python 3.11 or newer, no extra packages.
set -e
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -t . -v
