"""Paths and tool locations. Every value can be overridden with an environment variable."""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(os.environ.get("HF_ROOT", Path(__file__).resolve().parents[2]))


def _load_env_file(path: Path) -> None:
    """KEY=VALUE lines from .env (no quotes needed). Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ROOT / ".env")
DATA = Path(os.environ.get("HF_DATA", ROOT / "data"))
TEMPLATES = Path(os.environ.get("HF_TEMPLATES", ROOT / "templates"))
PLANNER_DIR = ROOT / "planner"

JOBS = DATA / "jobs"
LIBRARY = DATA / "library"
USAGE_DB = DATA / "usage.db"
RENDER_CACHE = DATA / "render-cache"

HYPERFRAMES_VERSION = os.environ.get("HF_HYPERFRAMES_VERSION", "0.8.188")
PLANNER_MODEL = os.environ.get("HF_PLANNER_MODEL", "sonnet")
PLANNER_PROVIDER = os.environ.get("HF_PLANNER_PROVIDER", "claude")  # default when the app does not choose
PROVIDERS = ("claude", "codex")
CODEX_BIN = os.environ.get("HF_CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("HF_CODEX_MODEL", "gpt-6-sol")  # a slug your ChatGPT login offers in Codex
CODEX_EFFORT = os.environ.get("HF_CODEX_EFFORT", "high")
# Optional Firebase mirror of the usage log (service-account JSON); empty = local SQLite only.
FIREBASE_CREDENTIALS = os.environ.get("HF_FIREBASE_CREDENTIALS", "")
FIREBASE_COLLECTION = os.environ.get("HF_FIREBASE_COLLECTION", "hf_usage")
API_TOKEN = os.environ.get("HF_API_TOKEN", "")

_validator_name = "cutagent-validator.bat" if sys.platform == "win32" else "cutagent-validator"
VALIDATOR = Path(
    os.environ.get("HF_VALIDATOR", ROOT / "validator" / "build" / "install" / "cutagent-validator" / "bin" / _validator_name)
)


def tool(name: str) -> str:
    """Resolve an executable on PATH (handles .cmd/.exe shims on Windows)."""
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} not found on PATH")
    return found


CLAUDE_BIN = os.environ.get("HF_CLAUDE_BIN", "claude")


def claude_executable() -> str:
    """The real Claude Code binary. npm's claude.cmd shim runs through cmd.exe, which cuts
    multi-line arguments (the system prompt) at the first newline, so bypass it."""
    found = tool(CLAUDE_BIN)
    if found.lower().endswith((".cmd", ".bat")):
        exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
        raise RuntimeError(f"{found} is a cmd shim and claude.exe was not found next to it; set HF_CLAUDE_BIN")
    return found
