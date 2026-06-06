#!/usr/bin/env sh
ruff check resources/lib --fix
ruff format resources/lib
