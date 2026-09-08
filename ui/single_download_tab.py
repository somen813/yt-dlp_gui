import os
import urllib.request
from typing import Dict, Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QRadioButton, QGroupBox, QProgressBar,
    QMessageBox, QFrame, QGridLayout, QScrollArea
)

from core.parser import VideoMetadataParser
from core.downloader import DownloadWorker
from ui.trimming_dialog import TrimmingDialog


class ParseWorker(QThread):
    """URL解析用のバックグラウンドスレッド"""
    parsed_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            metadata = VideoMetadataParser.parse_url(self.url)
            self.parsed_signal.emit(metadata)
        except Exception as e:
            self.error_signal.emit(str(e))


class ThumbnailWorker(QThread):
    """サムネイル画像を取得するスレッド"""
    loaded_signal = Signal(QPixmap)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            req = urllib.request.Request(self.url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
                img = QImage()
                if img.loadFromData(data):
                    pixmap = QPixmap.fromImage(img)
                    self.loaded_signal.emit(pixmap)
        except Exception:
            pass


class SingleDownloadTab(QWidget):
    """単体ダウンロードタブのUIとロジック"""
    log_signal = Signal(str)

    def __init__(self, get_output_folder_func, get_bin_dir_func, parent=None):
        super().__init__(parent)
        self.get_output_folder = get_output_folder_func
        self.get_bin_dir = get_bin_dir_func
        
        self.current_metadata: Optional[Dict[str, Any]] = None
        self.download_worker: Optional[DownloadWorker] = None
        self.parse_worker: Optional[ParseWorker] = None
        self.thumb_worker: Optional[ThumbnailWorker] = None

        # トリミング指定時刻の保持変数
        self.trim_start: str = ""
        self.trim_end: str = ""

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 縦スクロール領域（QScrollArea）の設定
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        container_widget = QWidget()
        layout = QVBoxLayout(container_widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # --- 1. URL 入力エリア ---
        url_box = QGroupBox("動画 / 音声 URL")
        url_layout = QHBoxLayout(url_box)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_input.returnPressed.connect(self._start_parse)
        url_layout.addWidget(self.url_input)

        self.parse_btn = QPushButton("URL解析")
        self.parse_btn.setStyleSheet("padding: 6px 16px; font-weight: bold;")
        self.parse_btn.clicked.connect(self._start_parse)
        url_layout.addWidget(self.parse_btn)

        layout.addWidget(url_box)

        # --- 2. 解析結果表示エリア ---
        self.info_card = QFrame()
        self.info_card.setFrameShape(QFrame.StyledPanel)
        self.info_card.setStyleSheet("background-color: #2b2b2b; border-radius: 8px; padding: 8px;")
        info_layout = QHBoxLayout(self.info_card)

        # サムネイル表示
        self.thumb_label = QLabel("サムネイル表示")
        self.thumb_label.setFixedSize(200, 112)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("border: 1px solid #555; background: #1e1e1e; color: #888;")
        info_layout.addWidget(self.thumb_label)

        # テキスト詳細
        detail_layout = QVBoxLayout()
        self.title_label = QLabel("タイトル: (URLを入力して解析してください)")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #fff;")
        detail_layout.addWidget(self.title_label)

        self.uploader_label = QLabel("チャンネル: -")
        self.uploader_label.setStyleSheet("color: #aaa;")
        detail_layout.addWidget(self.uploader_label)

        self.duration_label = QLabel("再生時間: -")
        self.duration_label.setStyleSheet("color: #aaa;")
        detail_layout.addWidget(self.duration_label)

        info_layout.addLayout(detail_layout)
        layout.addWidget(self.info_card)

        # --- 3. ダウンロード設定エリア ---
        settings_box = QGroupBox("ダウンロード設定")
        settings_layout = QVBoxLayout(settings_box)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("ダウンロード種別:"))
        
        self.radio_video = QRadioButton("動画")
        self.radio_video.setChecked(True)
        self.radio_video.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self.radio_video)

        self.radio_audio = QRadioButton("音声のみ")
        self.radio_audio.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self.radio_audio)
        
        mode_layout.addStretch()
        settings_layout.addLayout(mode_layout)

        grid = QGridLayout()
        
        grid.addWidget(QLabel("保存フォーマット:"), 0, 0)
        self.combo_ext = QComboBox()
        self.combo_ext.addItems(["mp4", "webm"])
        grid.addWidget(self.combo_ext, 0, 1)

        grid.addWidget(QLabel("画質 (解像度):"), 0, 2)
        self.combo_quality = QComboBox()
        self.combo_quality.addItems(["最高画質"])
        grid.addWidget(self.combo_quality, 0, 3)

        grid.addWidget(QLabel("フレームレート:"), 1, 0)
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["指定なし"])
        grid.addWidget(self.combo_fps, 1, 1)

        grid.addWidget(QLabel("音質 (ビットレート):"), 1, 2)
        self.combo_audio_bitrate = QComboBox()
        self.combo_audio_bitrate.addItems(["最高音質"])
        grid.addWidget(self.combo_audio_bitrate, 1, 3)

        settings_layout.addLayout(grid)
        layout.addWidget(settings_box)

        # --- 4. 範囲指定トリミング設定エリア (ダイアログ起動UI) ---
        trim_box = QGroupBox("範囲指定トリミング")
        trim_layout = QHBoxLayout(trim_box)

        self.trim_status_label = QLabel("トリミング設定: 未設定 (全編ダウンロード)")
        self.trim_status_label.setStyleSheet("color: #ddd; font-weight: bold;")
        trim_layout.addWidget(self.trim_status_label)

        trim_layout.addStretch()

        self.open_trim_dialog_btn = QPushButton("✂️ 範囲指定トリミングの設定・プレビュー...")
        self.open_trim_dialog_btn.setEnabled(False)
        self.open_trim_dialog_btn.setStyleSheet("padding: 6px 12px; font-weight: bold;")
        self.open_trim_dialog_btn.clicked.connect(self._open_trimming_dialog)
        trim_layout.addWidget(self.open_trim_dialog_btn)

        self.clear_trim_btn = QPushButton("解除")
        self.clear_trim_btn.setEnabled(False)
        self.clear_trim_btn.clicked.connect(self._clear_trimming)
        trim_layout.addWidget(self.clear_trim_btn)

        layout.addWidget(trim_box)

        # --- 5. プログレスバー & 実行ボタン ---
        exec_layout = QHBoxLayout()
        
        self.download_btn = QPushButton("ダウンロード開始")
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 8px 24px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #0098ff;
            }
            QPushButton:disabled {
                background-color: #444;
                color: #888;
            }
        """)
        self.download_btn.clicked.connect(self._start_download)
        exec_layout.addWidget(self.download_btn)

        self.open_folder_btn = QPushButton("保存先フォルダを開く")
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        exec_layout.addWidget(self.open_folder_btn)

        layout.addLayout(exec_layout)

        # 進捗バー
        progress_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("待機中...")
        self.status_label.setStyleSheet("color: #aaa;")
        progress_layout.addWidget(self.status_label)

        layout.addLayout(progress_layout)

        # スクロールエリアにコンテナを割り当て
        scroll_area.setWidget(container_widget)
        main_layout.addWidget(scroll_area)

    def _start_parse(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "エラー", "URLを入力してください。")
            return

        self.parse_btn.setEnabled(False)
        self.title_label.setText("タイトル: 解析中...")
        self.uploader_label.setText("チャンネル: 解析中...")
        self.duration_label.setText("再生時間: 解析中...")
        self.download_btn.setEnabled(False)
        self.open_trim_dialog_btn.setEnabled(False)
        self.clear_trim_btn.setEnabled(False)

        self.parse_worker = ParseWorker(url)
        self.parse_worker.parsed_signal.connect(self._on_parsed)
        self.parse_worker.error_signal.connect(self._on_parse_error)
        self.parse_worker.start()

    def _on_parsed(self, metadata: Dict[str, Any]):
        self.parse_btn.setEnabled(True)
        self.current_metadata = metadata

        self.title_label.setText(f"タイトル: {metadata['title']}")
        self.uploader_label.setText(f"チャンネル: {metadata['uploader']}")
        self.duration_label.setText(f"再生時間: {metadata['duration_str']}")

        # ドロップダウンの項目更新
        self.combo_quality.clear()
        self.combo_quality.addItems(metadata['height_options'])

        self.combo_fps.clear()
        self.combo_fps.addItems(metadata['fps_options'])

        self.combo_audio_bitrate.clear()
        self.combo_audio_bitrate.addItems(metadata['audio_options'])

        # サムネイル取得
        if metadata.get('thumbnail'):
            self.thumb_worker = ThumbnailWorker(metadata['thumbnail'])
            self.thumb_worker.loaded_signal.connect(self._on_thumb_loaded)
            self.thumb_worker.start()

        self.open_trim_dialog_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.status_label.setText("解析完了。トリミング設定およびダウンロードが可能です。")
        self.log_signal.emit(f"URL解析成功: {metadata['title']}")

    def _on_thumb_loaded(self, pixmap: QPixmap):
        scaled = pixmap.scaled(self.thumb_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.thumb_label.setPixmap(scaled)

    def _on_parse_error(self, err_msg: str):
        self.parse_btn.setEnabled(True)
        self.title_label.setText("タイトル: 解析エラー")
        self.status_label.setText(f"解析失敗: {err_msg}")
        QMessageBox.critical(self, "解析エラー", f"URLの解析に失敗しました:\n{err_msg}")
        self.log_signal.emit(f"URL解析失敗: {err_msg}")

    # --- 別ウィンドウ（ダイアログ）呼び出し ---
    def _open_trimming_dialog(self):
        if not self.current_metadata:
            return

        stream_url = self.current_metadata.get('stream_url', '')
        audio_url = self.current_metadata.get('audio_url', '')
        dialog = TrimmingDialog(
            stream_url=stream_url,
            audio_url=audio_url,
            initial_start=self.trim_start,
            initial_end=self.trim_end,
            parent=self
        )
        if dialog.exec() == TrimmingDialog.Accepted:
            self.trim_start = dialog.result_start
            self.trim_end = dialog.result_end
            self._update_trim_status_label()

    def _clear_trimming(self):
        self.trim_start = ""
        self.trim_end = ""
        self._update_trim_status_label()

    def _update_trim_status_label(self):
        if self.trim_start or self.trim_end:
            start_str = self.trim_start if self.trim_start else "先頭"
            end_str = self.trim_end if self.trim_end else "末尾"
            self.trim_status_label.setText(f"トリミング設定: ✂️ {start_str} ～ {end_str}")
            self.trim_status_label.setStyleSheet("color: #34ce57; font-weight: bold;")
            self.clear_trim_btn.setEnabled(True)
            self.log_signal.emit(f"トリミング設定更新: {start_str} ～ {end_str}")
        else:
            self.trim_status_label.setText("トリミング設定: 未設定 (全編ダウンロード)")
            self.trim_status_label.setStyleSheet("color: #ddd; font-weight: bold;")
            self.clear_trim_btn.setEnabled(False)
            self.log_signal.emit("トリミング設定解除")

    def _on_mode_changed(self):
        if self.radio_video.isChecked():
            self.combo_ext.clear()
            self.combo_ext.addItems(["mp4", "webm"])
            self.combo_quality.setEnabled(True)
            self.combo_fps.setEnabled(True)
        else:
            self.combo_ext.clear()
            self.combo_ext.addItems(["mp3", "wav"])
            self.combo_quality.setEnabled(False)
            self.combo_fps.setEnabled(False)

    def _start_download(self):
        url = self.url_input.text().strip()
        out_dir = self.get_output_folder()

        if not out_dir or not os.path.exists(out_dir):
            QMessageBox.warning(self, "エラー", "有効な保存先フォルダを選択してください。")
            return

        mode = "video" if self.radio_video.isChecked() else "audio"
        ext = self.combo_ext.currentText()
        height = self.combo_quality.currentText()
        fps = self.combo_fps.currentText()
        bitrate = self.combo_audio_bitrate.currentText()

        start_time = self.trim_start
        end_time = self.trim_end

        self.download_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("ダウンロード準備中...")

        self.download_worker = DownloadWorker(
            url=url,
            output_dir=out_dir,
            mode=mode,
            ext=ext,
            height=height,
            fps=fps,
            audio_bitrate=bitrate,
            section_start=start_time,
            section_end=end_time,
            bin_dir=self.get_bin_dir()
        )
        self.download_worker.progress_signal.connect(self._on_download_progress)
        self.download_worker.log_signal.connect(self.log_signal.emit)
        self.download_worker.finished_signal.connect(self._on_download_finished)
        self.download_worker.start()

    def _on_download_progress(self, info: Dict[str, Any]):
        pct = info.get('percent', 0.0)
        self.progress_bar.setValue(int(pct))
        
        speed = info.get('speed', '')
        eta = info.get('eta', '')
        self.status_label.setText(f"ダウンロード中: {pct:.1f}% ({speed} - 残り: {eta})")

    def _on_download_finished(self, success: bool, msg: str):
        self.download_btn.setEnabled(True)
        if success:
            self.progress_bar.setValue(100)
            self.status_label.setText("ダウンロード完了！")
            QMessageBox.information(self, "完了", msg)
        else:
            self.status_label.setText("ダウンロード失敗")
            QMessageBox.critical(self, "エラー", msg)

    def _open_output_folder(self):
        folder = self.get_output_folder()
        if folder and os.path.exists(folder):
            os.startfile(folder)
        else:
            QMessageBox.warning(self, "エラー", "保存先フォルダが存在しません。")
