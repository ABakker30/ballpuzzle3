from pathlib import Path
from typing import Any

def app_root() -> Path:
    # .../apps/puzzle_ui
    return Path(__file__).resolve().parents[1]

def repo_root() -> Path:
    # repo root
    return Path(__file__).resolve().parents[3]

def win_quote(arg: str) -> str:
    if not arg:
        return '""'
    if any(c.isspace() for c in arg) or '"' in arg or "'" in arg or "\\" in arg:
        return f'"{arg}"'
    return arg

def as_str(v: Any) -> str:
    return str(v)
