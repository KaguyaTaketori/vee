"""
main.py
───────
Application entry point.

All platform-specific wiring lives in ``infra/telegram/runner.py``.
All service assembly lives in ``bootstrap.py``.
This file only names the modules and fires the runner.

To add a new module: append it to MODULES. That's it.
"""

from utils.logger import setup_logging
from modules.downloader import DownloaderModule
from modules.billing import BillingModule
from infra.telegram.runner import run

MODULES = [
    DownloaderModule(),
    BillingModule(),
]


if __name__ == "__main__":
    setup_logging()
    run(MODULES)
