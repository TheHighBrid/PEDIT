"""Regression coverage for Python discovery in the macOS startup script.

Tests only the interpreter-selection preamble, without installing packages or
modifying a developer's Python installation.
"""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def _mock_python(directory: Path, name: str, version: str, compatible: bool) -> None:
    executable = directory / name
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  case \"$2\" in\n"
        f"    *sys.exit*) exit {0 if compatible else 1};;\n"
        f"    *sys.version_info.major*) echo '{version}'; exit 0;;\n"
        "  esac\n"
        "fi\n"
        f"if [ \"$1\" = \"--version\" ]; then echo 'Python {version}'; exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def _select_python(directory: Path):
    source = (ROOT / "start_mac.command").read_text(encoding="utf-8")
    start = source.index('python_exec=""')
    end = source.index('\nvenv=', start)
    selection = source[start:end]
    command = "set -euo pipefail\n" + selection + '\nprintf "SELECTED:%s:%s\\n" "$python_exec" "$version"\n'
    return subprocess.run(
        ["/bin/bash", "-c", command],
        env={"PATH": str(directory), "HOME": str(directory)},
        capture_output=True, text=True, check=False,
    )


def test_launcher_prefers_existing_versioned_homebrew_python(tmp_path):
    _mock_python(tmp_path, "python3", "3.9", compatible=False)
    _mock_python(tmp_path, "python3.12", "3.12", compatible=True)
    result = _select_python(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "SELECTED:python3.12:3.12" in result.stdout


def test_launcher_uses_compatible_unversioned_python(tmp_path):
    _mock_python(tmp_path, "python3", "3.11", compatible=True)
    result = _select_python(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "SELECTED:python3:3.11" in result.stdout


def test_launcher_explains_missing_compatible_python(tmp_path):
    _mock_python(tmp_path, "python3", "3.9", compatible=False)
    result = _select_python(tmp_path)
    assert result.returncode != 0
    assert "Could not find a working Python 3.10+" in result.stderr


def test_launcher_creates_venv_using_selected_interpreter():
    source = (ROOT / "start_mac.command").read_text(encoding="utf-8")
    assert '"$python_exec" -m venv "$venv"' in source
    assert '  python3 -m venv "$venv"' not in source
