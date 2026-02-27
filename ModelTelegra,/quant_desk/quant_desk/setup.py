#!/usr/bin/env python3
"""
Setup helper — creates .env, installs deps, configures scheduling.

Run: python setup.py
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def create_env_file():
    """Create .env from template if it doesn't exist."""
    env_file = PROJECT_ROOT / ".env"
    template = PROJECT_ROOT / "config" / ".env.example"

    if env_file.exists():
        print("[OK] .env already exists")
        return

    if template.exists():
        shutil.copy(template, env_file)
        print("[CREATED] .env file — please edit it with your Telegram credentials")
    else:
        print("[WARN] No .env.example found")


def install_dependencies():
    """Install Python dependencies."""
    req_file = PROJECT_ROOT / "requirements.txt"
    print("[INSTALL] Installing dependencies...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-r", str(req_file)
    ])
    print("[OK] Dependencies installed")


def setup_windows_task():
    """Create Windows Task Scheduler task."""
    if platform.system() != "Windows":
        print("[SKIP] Not Windows — use crontab instead")
        return

    python_exe = sys.executable
    script_path = PROJECT_ROOT / "main.py"

    xml_content = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2025-01-01T07:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions>
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>{script_path}</Arguments>
      <WorkingDirectory>{PROJECT_ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
  </Settings>
</Task>"""

    xml_path = PROJECT_ROOT / "quant_desk_task.xml"
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(xml_content)

    print(f"[CREATED] Task Scheduler XML: {xml_path}")
    print()
    print("To import into Task Scheduler:")
    print(f'  schtasks /create /tn "QuantDesk" /xml "{xml_path}"')
    print()
    print("Or open Task Scheduler GUI → Import Task → select the XML file")


def setup_linux_cron():
    """Print cron setup instructions."""
    if platform.system() == "Windows":
        print("[INFO] For WSL2, run this inside your WSL terminal:")

    python_exe = sys.executable
    script_path = PROJECT_ROOT / "main.py"

    print()
    print("Add to crontab (crontab -e):")
    print(f'0 7 * * * cd {PROJECT_ROOT} && {python_exe} {script_path} >> {PROJECT_ROOT}/logs/cron.log 2>&1')
    print()
    print("To make it survive reboots, also add:")
    print(f'@reboot sleep 120 && cd {PROJECT_ROOT} && {python_exe} {script_path} >> {PROJECT_ROOT}/logs/boot.log 2>&1')


def main():
    print("=" * 60)
    print("Quant Desk — Setup")
    print("=" * 60)
    print()

    create_env_file()
    print()

    try:
        install_dependencies()
    except Exception as e:
        print(f"[WARN] Dependency install failed: {e}")
        print("       Run manually: pip install -r requirements.txt")
    print()

    print("--- Scheduling Setup ---")
    if platform.system() == "Windows":
        setup_windows_task()
    setup_linux_cron()

    print()
    print("=" * 60)
    print("Setup complete! Next steps:")
    print("  1. Edit .env with your Telegram bot token and chat ID")
    print("  2. Run: python main.py --no-telegram  (test locally)")
    print("  3. Run: python main.py  (full run with Telegram)")
    print("=" * 60)


if __name__ == "__main__":
    main()
