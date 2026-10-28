#!/bin/zsh
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo "Install Python 3.10 or newer first from https://www.python.org/downloads/"
  read "?Press Return to close..."
  exit 1
fi
if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
python -m pip install -r requirements.txt
(sleep 3; open 'http://127.0.0.1:8000') &
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
