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

# Step 4: check for dependencies inside the venv and install if missing
deps = ["PySide6", "pyautogui", "pynput"]

# Run a Python command inside the venv to check each package
missing = []
for dep in deps:
    try:
        subprocess.run([python_bin, "-c", f"import {dep}"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        missing.append(dep)

if missing:
    print(f"Installing missing dependencies: {missing}")
    try:
        subprocess.check_call([pip_bin, "install"] + missing)
    except subprocess.CalledProcessError:
        print("Failed to install dependencies. Check your internet connection.")
        sys.exit(1)
else:
    print("All dependencies are already installed.")

# Step 5: run the macro tool
print("Launching Macro Tool...")
subprocess.check_call([python_bin, os.path.join(ROOT, "AutoClicker.py")])
