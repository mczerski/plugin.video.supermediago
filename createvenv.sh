#!/usr/bin/env sh

set -e

python3 -m venv .venv

# Activate the virtualenv
. .venv/bin/activate

# Install dependencies
pip install ruff==0.15.16
