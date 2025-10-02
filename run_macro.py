#!/usr/bin/env python3
import os
import sys
import subprocess
import venv

# Detect project root
ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(ROOT, "venv")

# Step 1: create virtual environment if missing
if not os.path.exists(VENV_DIR):
    print("Creating virtual environment...")
    venv.create(VENV_DIR, with_pip=True)

# Step 2: define venv Python and pip paths
if os.name == "nt":
    python_bin = os.path.join(VENV_DIR, "Scripts", "python.exe")
    pip_bin = os.path.join(VENV_DIR, "Scripts", "pip.exe")
else:
    python_bin = os.path.join(VENV_DIR, "bin", "python")
    pip_bin = os.path.join(VENV_DIR, "bin", "pip")

# Step 3: upgrade pip
print("Upgrading pip...")
subprocess.check_call([python_bin, "-m", "pip", "install", "--upgrade", "pip"])

# Step 4: install dependencies
print("Installing dependencies...")
deps = ["PySide6", "pyautogui", "pynput"]
subprocess.check_call([pip_bin, "install"] + deps)

# Step 5: run the macro tool
print("Launching Macro Tool...")
subprocess.check_call([python_bin, os.path.join(ROOT, "AutoClicker.py")])
