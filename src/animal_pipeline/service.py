from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from .core import ROOT, settings

LABEL = "ai.naminal.desk"
AGENTS = Path.home() / "Library" / "LaunchAgents"


def plist(root: Path, interval_s: int, limit: int) -> str:
    logs = root / "output" / "logs"
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(root / ".venv" / "bin" / "python"),
                             "main.py", "run", "--limit", str(limit)],
        "WorkingDirectory": str(root),
        "StartInterval": interval_s,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(logs / "desk.out.log"),
        "StandardErrorPath": str(logs / "desk.err.log"),
    }).decode()


def install(root: Path, agents_dir: Path, *, install_launchctl: bool = True) -> dict:
    cfg = settings()["service"]
    agents_dir.mkdir(parents=True, exist_ok=True)
    (root / "output" / "logs").mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{LABEL}.plist"
    path.write_text(plist(root, cfg["interval_s"], cfg["run_limit"]))
    os.chmod(path, 0o644)
    bootstrapped = False
    if install_launchctl:
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
        bootstrapped = True
    return {"plist": str(path), "bootstrapped": bootstrapped,
            "interval_s": cfg["interval_s"], "run_limit": cfg["run_limit"],
            "note": "local renders only; uploads remain disabled"}


def status() -> dict:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True, text=True, timeout=30)
    installed = (AGENTS / f"{LABEL}.plist").is_file()
    return {"label": LABEL, "plist_installed": installed,
            "loaded": result.returncode == 0,
            "state": "loaded" if result.returncode == 0 else "not loaded"}


def uninstall() -> dict:
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True, text=True, timeout=30)
    path = AGENTS / f"{LABEL}.plist"
    path.unlink(missing_ok=True)
    return {"label": LABEL, "removed": not path.exists(),
            "bootout": result.returncode == 0}
