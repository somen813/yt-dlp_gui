import os
import json
from typing import Dict, Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTabWidget, QTextEdit, QFileDialog, QMessageBox,
    QFrame, QDialog, QComboBox, QGroupBox, QSplitter
)

from core.updater import YtdlpUpdater
from ui.single_download_tab import SingleDownloadTab
from ui.batch_download_tab import BatchDownloadTab


class VersionCheckThread(QThread):
    """起動時に非同期で yt-dlp バージョンチェックを行うスレッド"""
    version_result_signal = Signal(bool, str, str)  # has_update, latest_ver, download_url

    def __init__(self, updater: YtdlpUpdater, parent=None):
        super().__init__(parent)
        self.updater = updater

    def run(self):
        has_update, latest_ver, download_url = self.updater.check_latest_release()
        self.version_result_signal.emit(has_update, latest_ver, download_url)


class VersionManagementDialog(QDialog):
    """yt-dlp のバージョン管理・ダウングレード用ダイアログ"""

    def __init__(self, updater: YtdlpUpdater, parent=None):
        super().__init__(parent)
        self.updater = updater
        self.setWindowTitle("yt-dlp バージョン管理")
        self.setMinimumWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        current_ver = self.updater.get_current_version()
        layout.addWidget(QLabel(f"<b>現在の yt-dlp バージョン:</b> {current_ver}"))

        group = QGroupBox("保存済み過去バージョン（ダウングレード）")
        group_layout = QVBoxLayout(group)

        self.combo_versions = QComboBox()
        saved_vers = self.updater.list_saved_versions()
        if saved_vers:
            self.combo_versions.addItems(saved_vers)
        else:
            self.combo_versions.addItem("保存済みバージョンなし")
            self.combo_versions.setEnabled(False)

        group_layout.addWidget(self.combo_versions)

        self.restore_btn = QPushButton("選択したバージョンに戻す")
        self.restore_btn.setEnabled(bool(saved_vers))
        self.restore_btn.clicked.connect(self._restore_selected)
        group_layout.addWidget(self.restore_btn)

        layout.addWidget(group)

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _restore_selected(self):
        target = self.combo_versions.currentText()
        if not target or target == "保存済みバージョンなし":
            return

        succ, msg = self.updater.restore_version(target)
        if succ:
            QMessageBox.information(self, "完了", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "エラー", msg)


APP_VERSION = "1.1.0"

class MainWindow(QMainWindow):
    """メインウィンドウ"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"yt-dlp GUI v{APP_VERSION} - 動画＆音声ダウンロード")
        self.resize(900, 700)

        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.settings_path = os.path.join(self.base_dir, "data", "settings.json")
        self.updater = YtdlpUpdater(self.base_dir)

        self.latest_download_url = ""
        self.settings = self._load_settings()

        self._init_ui()

        # ダークモード風スタイルセット
        self._apply_dark_theme()

        # 起動時バージョンチェック
        self._check_version_async()

    def _load_settings(self) -> Dict[str, Any]:
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "last_output_folder": os.path.expanduser("~/Downloads"),
            "default_video_ext": "mp4",
            "default_audio_ext": "mp3",
            "kept_ytdlp_versions": 5
        }

    def _save_settings(self):
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"設定保存エラー: {e}")

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # --- 1. 通知バナー (yt-dlp アップデート用非モーダルバナー) ---
        self.banner = QFrame()
        self.banner.setStyleSheet("background-color: #2b4c7e; border-radius: 4px; padding: 4px;")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(10, 4, 10, 4)

        self.banner_label = QLabel("yt-dlp の新しいバージョンが利用可能です。")
        self.banner_label.setStyleSheet("color: white; font-weight: bold;")
        banner_layout.addWidget(self.banner_label)

        self.banner_update_btn = QPushButton("今すぐ更新")
        self.banner_update_btn.setStyleSheet("background-color: #008cba; color: white; padding: 4px 12px;")
        self.banner_update_btn.clicked.connect(self._exec_update)
        banner_layout.addWidget(self.banner_update_btn)

        self.banner_close_btn = QPushButton("✕")
        self.banner_close_btn.setFixedWidth(24)
        self.banner_close_btn.clicked.connect(lambda: self.banner.setVisible(False))
        banner_layout.addWidget(self.banner_close_btn)

        self.banner.setVisible(False)
        main_layout.addWidget(self.banner)

        # --- 2. 上部ヘッダー（保存先フォルダ＆バージョン管理） ---
        header_box = QGroupBox("共通設定")
        header_layout = QHBoxLayout(header_box)

        header_layout.addWidget(QLabel("出力先フォルダ:"))
        
        self.output_folder_input = QLineEdit()
        folder = self.settings.get("last_output_folder", "")
        if not folder or not os.path.exists(folder):
            folder = os.path.expanduser("~/Downloads")
        self.output_folder_input.setText(folder)
        self.output_folder_input.textChanged.connect(self._on_folder_changed)
        header_layout.addWidget(self.output_folder_input)

        browse_btn = QPushButton("参照...")
        browse_btn.clicked.connect(self._browse_output_folder)
        header_layout.addWidget(browse_btn)

        ver_mgr_btn = QPushButton("バージョン管理")
        ver_mgr_btn.clicked.connect(self._open_version_mgr)
        header_layout.addWidget(ver_mgr_btn)

        main_layout.addWidget(header_box)

        # --- 3. メインタブエリア ---
        self.tab_widget = QTabWidget()
        
        self.single_tab = SingleDownloadTab(
            get_output_folder_func=self.get_output_folder,
            get_bin_dir_func=self.get_bin_dir
        )
        self.single_tab.log_signal.connect(self.log)
        self.tab_widget.addTab(self.single_tab, "単体ダウンロード")

        self.batch_tab = BatchDownloadTab(
            get_output_folder_func=self.get_output_folder,
            get_bin_dir_func=self.get_bin_dir
        )
        self.batch_tab.log_signal.connect(self.log)
        self.tab_widget.addTab(self.batch_tab, "複数ダウンロード")

        main_layout.addWidget(self.tab_widget, stretch=1)

        # --- 4. ログエリア (折りたたみ可能) ---
        log_box = QGroupBox("実行ログ")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(6, 6, 6, 6)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas, monospace;")
        log_layout.addWidget(self.log_text)

        main_layout.addWidget(log_box)

        self.log(f"アプリ起動完了 (yt-dlpバージョン: {self.updater.get_current_version()})")

    def get_output_folder(self) -> str:
        return self.output_folder_input.text().strip()

    def get_bin_dir(self) -> str:
        return self.updater.bin_dir

    def _browse_output_folder(self):
        curr = self.get_output_folder()
        chosen = QFileDialog.getExistingDirectory(self, "保存先フォルダの選択", curr)
        if chosen:
            self.output_folder_input.setText(chosen)

    def _on_folder_changed(self, text: str):
        self.settings["last_output_folder"] = text
        self._save_settings()

    def _open_version_mgr(self):
        dlg = VersionManagementDialog(self.updater, self)
        dlg.exec()

    def _check_version_async(self):
        self.ver_thread = VersionCheckThread(self.updater)
        self.ver_thread.version_result_signal.connect(self._on_version_checked)
        self.ver_thread.start()

    def _on_version_checked(self, has_update: bool, latest_ver: str, download_url: str):
        if has_update and download_url:
            self.latest_download_url = download_url
            self.banner_label.setText(f"新しい yt-dlp バージョン ({latest_ver}) が利用可能です。")
            self.banner.setVisible(True)
            self.log(f"yt-dlp のアップデートが検出されました: {latest_ver}")

    def _exec_update(self):
        if not self.latest_download_url:
            return

        self.banner_update_btn.setEnabled(False)
        self.log("yt-dlp のアップデートを開始します...")

        succ, msg = self.updater.update_ytdlp(self.latest_download_url)
        self.banner.setVisible(False)
        self.banner_update_btn.setEnabled(True)

        if succ:
            # クリーニング実行
            self.updater.cleanup_old_versions(self.settings.get("kept_ytdlp_versions", 5))
            QMessageBox.information(self, "アップデート完了", msg)
            self.log(msg)
        else:
            QMessageBox.critical(self, "アップデート失敗", msg)
            self.log(msg)

    def log(self, text: str):
        self.log_text.append(text)

    def _apply_dark_theme(self):
        dark_stylesheet = """
        QMainWindow {
            background-color: #1e1e1e;
        }
        QWidget {
            color: #e0e0e0;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #3d3d3d;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 5px;
            color: #00adb5;
        }
        QLineEdit, QComboBox {
            background-color: #2a2a2a;
            border: 1px solid #444;
            border-radius: 4px;
            padding: 5px;
            color: #fff;
        }
        QComboBox::drop-down {
            border: 0px;
        }
        QPushButton {
            background-color: #3a3a3a;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 6px 12px;
            color: #fff;
        }
        QPushButton:hover {
            background-color: #4a4a4a;
        }
        QTabWidget::pane {
            border: 1px solid #333;
            background-color: #242424;
            border-radius: 4px;
        }
        QTabBar::tab {
            background-color: #1a1a1a;
            color: #aaa;
            padding: 8px 16px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }
        QTabBar::tab:selected {
            background-color: #242424;
            color: #00adb5;
            font-weight: bold;
        }
        QProgressBar {
            border: 1px solid #444;
            border-radius: 4px;
            text-align: center;
            background-color: #2a2a2a;
        }
        QProgressBar::chunk {
            background-color: #00adb5;
            width: 10px;
        }
        """
        self.setStyleSheet(dark_stylesheet)
