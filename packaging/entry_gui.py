"""PyInstaller entry point for the copilot-usage-gui standalone executable."""

from __future__ import annotations

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()

from copilot_usage_visualizer.gui_cli import main

if __name__ == "__main__":
    sys.exit(main())
