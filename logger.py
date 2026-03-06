import os
import logging
from datetime import datetime
from config import LOG_FOLDER

# Ensure logs folder exists
if not os.path.exists(LOG_FOLDER):
    os.makedirs(LOG_FOLDER)

log_filename = datetime.now().strftime("%Y-%m-%d") + ".log"
log_path = os.path.join(LOG_FOLDER, log_filename)

logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def log_event(message):
    """Log message with timestamp"""
    logging.info(message)
    print(message)