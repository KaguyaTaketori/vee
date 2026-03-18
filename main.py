from utils.logger import setup_logging
from modules.downloader import DownloaderModule
from modules.billing import BillingModule
from infra.telegram.runner import run

MODULES = [
    BillingModule(),
    DownloaderModule(),
]


if __name__ == "__main__":
    setup_logging()
    run(MODULES)
