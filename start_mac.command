#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

# Rebuild native dependencies outside the quarantined downloaded ZIP directory.
# Do not disable Gatekeeper or remove quarantine attributes.
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10 or newer is required. Install it from Python.org or Homebrew."
  exit 1
fi
if ! python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then
  echo "PEDIT requires Python 3.10 or newer."
  exit 1
fi
version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
venv="$HOME/Library/Application Support/PEDIT/venv-python$version-$(uname -m)"
mkdir -p "$(dirname "$venv")"
requirements_hash="$(shasum -a 256 requirements.txt | cut -d ' ' -f 1)"
stamp="$venv/.pedit-requirements-sha256"

function native_imports_ok() {
  [[ -x "$venv/bin/python" ]] && "$venv/bin/python" -c 'import pydantic_core, fastapi, fitz, uvicorn, watchfiles' >/dev/null 2>&1
}

if [[ ! -f "$stamp" ]] || [[ "$(cat "$stamp" 2>/dev/null)" != "$requirements_hash" ]] || ! native_imports_ok; then
  echo "Preparing clean PEDIT Python dependencies in Application Support..."
  # Only replace the isolated PEDIT environment; never modify unrelated Python installs.
  rm -rf -- "$venv"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
  if ! native_imports_ok; then
    echo "Native Python dependency verification failed. System security settings were not changed."
    exit 1
  fi
  print -r -- "$requirements_hash" > "$stamp"
fi

(sleep 2; open 'http://127.0.0.1:8000') &
exec "$venv/bin/python" -m uvicorn app:app --host 127.0.0.1 --port 8000
