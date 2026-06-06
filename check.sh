#!/usr/bin/env sh
ruff check resources/lib
ruff format --diff resources/lib
