#!/usr/bin/env python3
"""Interactively write local backup settings to config.json."""
import json
import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
CREDENTIALS_PATH = Path.home() / ".local/state/zoho-wd-backup/credentials.json"
SOURCE_DIRS_PATH = PROJECT_DIR / "backup-dirs.txt"
ASCII_ART_FILE = PROJECT_DIR / "assets" / "ascii-art-patorjk-com-favourites.txt"


def print_banner():
    print(f"\n{ASCII_ART_FILE.read_text(encoding='utf-8').rstrip()}\n")


def ask(question, hint, default=None, show_current=True):
    color = os.isatty(1) and os.environ.get("NO_COLOR") is None
    cyan = "\033[96m" if color else ""
    purple = "\033[35m" if color else ""
    dark_cyan = "\033[36m" if color else ""
    grey = "\033[90m" if color else ""
    white = "\033[37m" if color else ""
    reset = "\033[0m" if color else ""
    print(f"{cyan}{question}{reset}")
    print(f"{grey}HINT: {hint}{reset}")
    if show_current and default not in (None, ""):
        print(f"{purple}Current:{reset} {white}{default}{reset} {dark_cyan}- Enter to keep; type to replace.{reset}")
    return input("> ").strip() or (default or "")


def ask_yes_no(question, hint, default):
    while True:
        answer = ask(
            question,
            hint,
            "y" if default else "n",
            show_current=default is not None,
        ).lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please enter y or n (or press Enter to keep the current value).")


def main():
    print_banner()
    try:
        existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}

    folder_id = ask(
        "WorkDrive remote folder ID:",
        "Example: https://workdrive.zoho.in/.../folders/abcdef123456 — the value after /folders/ is the folder ID.",
        existing.get("WORKDRIVE_FOLDER_ID", ""),
    )
    if not folder_id:
        raise SystemExit("Remote folder ID is required.")

    print()
    delete_remote = ask_yes_no(
        "Enable --delete-remote? [y/n]",
        "Local deletions will be reflected in remote if this flag is selected.",
        existing.get("deleteRemote"),
    )
    print()
    apply = ask_yes_no(
        "Enable --apply (uploads)? [y/n]",
        "Dry run is the default; enabling --apply makes the script directly upload without a dry run.",
        existing.get("apply"),
    )
    print()
    local_dir = ask(
        "Local directory to back up:",
        "Enter the full path to the directory. This script saves the path; it does not scan it.",
        existing.get("localDir", ""),
    )
    if not local_dir:
        raise SystemExit("Local directory is required.")

    config = {
        "WORKDRIVE_FOLDER_ID": folder_id,
        "deleteRemote": delete_remote,
        "apply": apply,
        "localDir": str(Path(local_dir).expanduser().resolve()),
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)

    yellow = "\033[93m" if os.isatty(1) and os.environ.get("NO_COLOR") is None else ""
    reset_status = "\033[0m" if yellow else ""
    grey_status = "\033[90m" if os.isatty(1) and os.environ.get("NO_COLOR") is None else ""
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.write_text(
            json.dumps(
                {
                    "refresh_token": "YOUR_REFRESH_TOKEN_HERE",
                    "api_domain": "https://www.zohoapis.in",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(CREDENTIALS_PATH, 0o600)
        print(f"\n{yellow}Created credentials placeholder at {CREDENTIALS_PATH} (mode 0600).{reset_status}")
    else:
        print(f"\n{grey_status}Credentials file already exists at: {CREDENTIALS_PATH} — skipping. (Don't worry, this script NEVER reads it.){reset_status}")

    if not SOURCE_DIRS_PATH.exists():
        SOURCE_DIRS_PATH.write_text(
            "# One source directory relative to the configured backup root per line.\n"
            "Documents\nPictures\nProjects\n",
            encoding="utf-8",
        )
        print(f"\n{yellow}Created sample source list: {SOURCE_DIRS_PATH}{reset_status}")
    else:
        print(f"\n{grey_status}{SOURCE_DIRS_PATH.name} already exists; skipping.{reset_status}")

    green = "\033[92m" if os.isatty(1) and os.environ.get("NO_COLOR") is None else ""
    reset = "\033[0m" if green else ""
    print(f"\n{green}Saved local settings to config.json (keep it private).{reset}")


if __name__ == "__main__":
    main()
