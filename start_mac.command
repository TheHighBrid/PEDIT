#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

# Rebuild native dependencies outside the quarantined downloaded ZIP directory.
# Do not disable Gatekeeper or remove quarantine attributes.
# macOS may resolve 'python3' to Apple's older system Python even when
# Homebrew Python 3.12 is installed and used by the previous PEDIT environment.
# Prefer known compatible versioned interpreters, including keg-only Homebrew
# locations, before falling back to 'python3'.
python_exec=""
for name in python3.12 python3.13 python3.11 python3.10 python3.14 python3; do
  candidates=(
    "$name"
    "/opt/homebrew/bin/$name"
    "/usr/local/bin/$name"
    "/opt/homebrew/opt/python@${name#python}/bin/$name"
    "/usr/local/opt/python@${name#python}/bin/$name"
  )
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      python_exec="$candidate"
      break 2
    fi
  done
done
if [[ -z "$python_exec" ]]; then
  echo "Could not find a working Python 3.10+ installation." >&2
  echo "Install Python 3.12 with: brew install python@3.12" >&2
  exit 1
fi
echo "Using $("$python_exec" --version 2>&1) from $(command -v "$python_exec")"
version="$("$python_exec" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
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
  "$python_exec" -m venv "$venv"
  "$venv/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
  if ! native_imports_ok; then
    echo "Native Python dependency verification failed. System security settings were not changed."
    exit 1
  fi
  print -r -- "$requirements_hash" > "$stamp"
fi

exec "$venv/bin/python" launch_classic.py "$@"
