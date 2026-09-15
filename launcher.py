"""
TestDataPlotter Launcher — Use this to create a standalone .exe via PyInstaller.

This script starts the Streamlit server and opens the browser.
Build with:
    pyinstaller --onefile --windowed --name TestDataPlotter launcher.py

Note: The .exe must be in the same directory as test_data_plotter.py and .streamlit/
"""

import subprocess
import sys
import os
import time
import webbrowser
import threading


def open_browser():
    """Wait for server to start, then open browser."""
    time.sleep(3)
    webbrowser.open("http://localhost:5002")


def main():
    # Get the directory where this exe/script lives
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller .exe
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))

    app_path = os.path.join(app_dir, "test_data_plotter.py")

    if not os.path.exists(app_path):
        print(f"ERROR: Cannot find test_data_plotter.py in {app_dir}")
        input("Press Enter to exit...")
        sys.exit(1)

    # Open browser in background thread
    threading.Thread(target=open_browser, daemon=True).start()

    # Launch streamlit
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", app_path,
        "--server.headless=true",
        "--server.port=5002",
    ], cwd=app_dir)


if __name__ == "__main__":
    main()