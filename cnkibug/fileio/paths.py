from __future__ import annotations

import os
import shutil
import subprocess
import sys


def get_real_desktop_path() -> str:
    if sys.platform == "win32":
        import winreg

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            value, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            return os.path.expandvars(value)
        except Exception:
            return os.path.join(os.path.expanduser("~"), "Desktop")
    return _xdg_desktop_path()


def _xdg_desktop_path() -> str:
    home = os.path.expanduser("~")
    fallback = os.path.join(home, "Desktop")
    env_desktop = os.environ.get("XDG_DESKTOP_DIR")
    if env_desktop:
        return os.path.expandvars(env_desktop)

    xdg_bin = shutil.which("xdg-user-dir")
    if xdg_bin:
        try:
            result = subprocess.run(
                [xdg_bin, "DESKTOP"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            path = result.stdout.strip()
            if path and path != home and os.path.isdir(path):
                return path
        except Exception:
            pass

    config = os.path.join(home, ".config", "user-dirs.dirs")
    try:
        with open(config, encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if line.startswith("XDG_DESKTOP_DIR"):
                    value = line.split("=", 1)[1].strip().strip('"')
                    return os.path.expandvars(value.replace("$HOME", home))
    except OSError:
        pass
    return fallback
