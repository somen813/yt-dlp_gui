import sys
import os
import logging

from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

__version__ = "1.1.0"

def setup_logging():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    setup_logging()
    logging.info(f"yt-dlp GUI v{__version__} を起動しています...")
    
    app = QApplication(sys.argv)
    app.setApplicationName(f"yt-dlp GUI v{__version__}")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()