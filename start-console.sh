#!/bin/sh
BASE=$(CDPATH= cd -P "$(dirname "$0")" && pwd) || exit 1
exec python3 "$BASE/console-ui/run.py" "$@"
