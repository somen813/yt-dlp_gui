import os
from typing import List, Dict, Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QPushButton,
    QComboBox, QRadioButton, QGroupBox, QProgressBar,
    QListWidget, QListWidgetItem, QMessageBox, QGridLayout, QScrollArea, QFrame,
    QFileDialog
)

from core.downloader import DownloadWorker
from core.parser import VideoMetadataParser


class BatchPrepareWorker(QThread):
    """入力されたURL群を解析し、プレイリストやチャンネルを全動画リストに展開するワーカー"""
    log_signal = Signal(str)
    prepare_finished_signal = Signal(list)  # list of Dict[str, str]

    def __init__(self, raw_urls: List[str], bin_dir: str, parent=None):
        super().__init__(parent)
        self.raw_urls = raw_urls
        self.bin_dir = bin_dir
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        self.log_signal.emit("URLの事前解析を開始します（プレイリスト/チャンネルの動画本数を取得中...）")
        def log_cb(msg):
            if not self._is_cancelled:
                self.log_signal.emit(msg)

        expanded_items = VideoMetadataParser.expand_urls(
            urls=self.raw_urls,
            custom_ytdlp_path=self.bin_dir,
            log_callback=log_cb
        )
        if not self._is_cancelled:
            self.prepare_finished_signal.emit(expanded_items)


class BatchRunnerWorker(QThread):
    """展開された動画アイテム群を順次（シーケンシャル）にダウンロードするワーカー"""
    url_started_signal = Signal(int, dict)  # index, item
    url_progress_signal = Signal(int, dict)  # index, info
    url_completed_signal = Signal(int, bool, str)  # index, success, msg
    batch_finished_signal = Signal(int, int)  # total_success, total_failed
    log_signal = Signal(str)

    def __init__(self, items: List[Dict[str, str]], output_dir: str, mode: str, ext: str,
                 height: str, fps: str, bitrate: str, bin_dir: str, parent=None):
        super().__init__(parent)
        self.items = items
        self.output_dir = output_dir
        self.mode = mode
        self.ext = ext
        self.height = height
        self.fps = fps
        self.bitrate = bitrate
        self.bin_dir = bin_dir
        self._is_cancelled = False
        self.current_worker: Optional[DownloadWorker] = None

    def cancel(self):
        self._is_cancelled = True
        if self.current_worker:
            self.current_worker.cancel()

    def run(self):
        success_count = 0
        failed_count = 0

        for idx, item in enumerate(self.items):
            if self._is_cancelled:
                self.log_signal.emit("一括ダウンロードがキャンセルのため中断されました。")
                break

            url = item.get('url', '')
            title = item.get('title', url)

            self.url_started_signal.emit(idx, item)
            self.log_signal.emit(f"一括ダウンロード [{idx+1}/{len(self.items)}]: {title} ({url})")

            # 各URLを単体ワーカーで同期処理的に実行
            worker = DownloadWorker(
                url=url,
                output_dir=self.output_dir,
                mode=self.mode,
                ext=self.ext,
                height=self.height,
                fps=self.fps,
                audio_bitrate=self.bitrate,
                bin_dir=self.bin_dir
            )
            self.current_worker = worker

            current_success = False
            current_msg = ""

            def on_prog(info):
                self.url_progress_signal.emit(idx, info)

            def on_finish(succ, msg):
                nonlocal current_success, current_msg
                current_success = succ
                current_msg = msg

            worker.progress_signal.connect(on_prog, Qt.DirectConnection)
            worker.log_signal.connect(self.log_signal.emit, Qt.DirectConnection)
            worker.finished_signal.connect(on_finish, Qt.DirectConnection)

            # BatchRunnerWorker スレッド内で同期的に直接実行
            worker.run()
            self.current_worker = None

            if current_success:
                success_count += 1
            else:
                failed_count += 1

            self.url_completed_signal.emit(idx, current_success, current_msg)

        self.batch_finished_signal.emit(success_count, failed_count)


class BatchDownloadTab(QWidget):
    """複数一括ダウンロード（list.txt）用のUIとロジック"""
    log_signal = Signal(str)

    def __init__(self, get_output_folder_func, get_bin_dir_func, parent=None):
        super().__init__(parent)
        self.get_output_folder = get_output_folder_func
        self.get_bin_dir = get_bin_dir_func
        self.prepare_worker: Optional[BatchPrepareWorker] = None
        self.runner_worker: Optional[BatchRunnerWorker] = None
        self.items: List[Dict[str, str]] = []
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        container_widget = QWidget()
        layout = QVBoxLayout(container_widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # --- 1. チャンネル / プレイリスト URL 入力エリア（枠1） ---
        pc_box = QGroupBox("チャンネル / プレイリスト URL")
        pc_layout = QVBoxLayout(pc_box)

        pc_guide = QLabel("YouTubeチャンネル (例: `https://www.youtube.com/@ChannelName`) や プレイリストURL を入力してください。\nVideos (通常動画) や Shorts などを自動検出し、動画1本1本に展開してダウンロードキューに追加します。")
        pc_guide.setWordWrap(True)
        pc_layout.addWidget(pc_guide)

        self.playlist_channel_edit = QLineEdit()
        self.playlist_channel_edit.setPlaceholderText("https://www.youtube.com/@ChannelName または https://www.youtube.com/playlist?list=...")
        pc_layout.addWidget(self.playlist_channel_edit)

        layout.addWidget(pc_box)

        # --- 2. 個別URLリスト & テキストファイル (.txt) エリア（枠2） ---
        input_box = QGroupBox("個別URLリスト & テキストファイル (.txt)")
        input_layout = QVBoxLayout(input_box)

        guide_label = QLabel("個別の動画URLを1行に1つずつ入力するか、任意のテキストファイル (.txt) を選択して読み込めます。")
        guide_label.setWordWrap(True)
        input_layout.addWidget(guide_label)

        self.url_text_edit = QTextEdit()
        self.url_text_edit.setPlaceholderText(
            "ここにURLを入力（複数指定可）\n"
            "例:\n"
            "https://www.youtube.com/watch?v=sample1\n"
            "https://www.youtube.com/watch?v=sample2\n"
            "# #から始まる行はコメントとして無視されます"
        )
        self.url_text_edit.setMinimumHeight(120)
        input_layout.addWidget(self.url_text_edit)

        btn_layout = QHBoxLayout()
        self.load_list_btn = QPushButton("txt ファイルを開く...")
        self.load_list_btn.clicked.connect(self._load_from_txt_dialog)
        btn_layout.addWidget(self.load_list_btn)

        self.save_list_btn = QPushButton("txt ファイルとして保存...")
        self.save_list_btn.clicked.connect(self._save_to_txt_dialog)
        btn_layout.addWidget(self.save_list_btn)

        self.clear_btn = QPushButton("クリア")
        self.clear_btn.clicked.connect(self._clear_all_inputs)
        btn_layout.addWidget(self.clear_btn)

        input_layout.addLayout(btn_layout)
        layout.addWidget(input_box)

        # --- 3. 一括設定エリア ---
        settings_box = QGroupBox("一括ダウンロード設定")
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
        
        grid.addWidget(QLabel("拡張子:"), 0, 0)
        self.combo_ext = QComboBox()
        self.combo_ext.addItems(["mp4", "webm"])
        grid.addWidget(self.combo_ext, 0, 1)

        grid.addWidget(QLabel("最高画質上限:"), 0, 2)
        self.combo_quality = QComboBox()
        self.combo_quality.addItems(["最高画質", "2160p", "1440p", "1080p", "720p", "480p", "360p"])
        grid.addWidget(self.combo_quality, 0, 3)

        grid.addWidget(QLabel("最高FPS上限:"), 1, 0)
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["指定なし", "60", "30", "24"])
        grid.addWidget(self.combo_fps, 1, 1)

        grid.addWidget(QLabel("音質上限:"), 1, 2)
        self.combo_audio_bitrate = QComboBox()
        self.combo_audio_bitrate.addItems(["最高音質", "320k", "256k", "192k", "128k"])
        grid.addWidget(self.combo_audio_bitrate, 1, 3)

        settings_layout.addLayout(grid)
        layout.addWidget(settings_box)

        # --- 4. キュー一覧リスト ---
        queue_box = QGroupBox("ダウンロードキュー")
        queue_layout = QVBoxLayout(queue_box)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(150)
        queue_layout.addWidget(self.list_widget)

        layout.addWidget(queue_box)

        # --- 5. プログレス＆実行制御 ---
        exec_layout = QHBoxLayout()

        self.start_batch_btn = QPushButton("一括ダウンロード開始")
        self.start_batch_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 8px 24px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #34ce57;
            }
            QPushButton:disabled {
                background-color: #444;
                color: #888;
            }
        """)
        self.start_batch_btn.clicked.connect(self._start_batch)
        exec_layout.addWidget(self.start_batch_btn)

        self.stop_batch_btn = QPushButton("中断")
        self.stop_batch_btn.setEnabled(False)
        self.stop_batch_btn.clicked.connect(self._stop_batch)
        exec_layout.addWidget(self.stop_batch_btn)

        layout.addLayout(exec_layout)

        # 進捗バー
        self.overall_progress = QProgressBar()
        layout.addWidget(self.overall_progress)

        self.status_label = QLabel("待機中")
        layout.addWidget(self.status_label)

        scroll_area.setWidget(container_widget)
        main_layout.addWidget(scroll_area)

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

    def _get_urls_from_text_edit(self) -> List[str]:
        text = self.url_text_edit.toPlainText()
        urls = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                urls.append(stripped)
        return urls

    def _load_from_txt_dialog(self):
        folder = self.get_output_folder() or os.getcwd()
        file_path, _ = QFileDialog.getOpenFileName(
            self, "テキストファイルを開く", folder, "テキストファイル (*.txt);;すべてのファイル (*)"
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.url_text_edit.setPlainText(content)
                self.log_signal.emit(f"テキストファイルを読み込みました: {file_path}")
                QMessageBox.information(self, "成功", f"ファイルから読み込みました:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"ファイルの読み込みに失敗しました: {e}")

    def _save_to_txt_dialog(self):
        folder = self.get_output_folder() or os.getcwd()
        default_path = os.path.join(folder, "urls.txt")
        file_path, _ = QFileDialog.getSaveFileName(
            self, "テキストファイルとして保存", default_path, "テキストファイル (*.txt);;すべてのファイル (*)"
        )
        if file_path:
            try:
                content = self.url_text_edit.toPlainText()
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.log_signal.emit(f"テキストファイルを保存しました: {file_path}")
                QMessageBox.information(self, "成功", f"ファイルに保存しました:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"ファイルの保存に失敗しました: {e}")

    def _clear_all_inputs(self):
        self.playlist_channel_edit.clear()
        self.url_text_edit.clear()

    def _start_batch(self):
        folder = self.get_output_folder()
        if not folder or not os.path.exists(folder):
            QMessageBox.warning(self, "エラー", "有効な保存先フォルダを選択してください。")
            return

        urls = []
        pc_url = self.playlist_channel_edit.text().strip()
        if pc_url:
            urls.append(pc_url)

        edit_urls = self._get_urls_from_text_edit()
        urls.extend(edit_urls)

        if not urls:
            QMessageBox.warning(
                self,
                "警告",
                "URLが入力されていません。\n「チャンネル / プレイリスト URL」欄か「個別URLリスト」欄にURLを入力するか、.txt ファイルを読み込んでください。"
            )
            return

        self.list_widget.clear()
        self.start_batch_btn.setEnabled(False)
        self.stop_batch_btn.setEnabled(True)
        self.overall_progress.setRange(0, 0)
        self.status_label.setText("URL事前解析中（Videos/Shorts等の各動画を動画1本1本に展開中...）")

        self.prepare_worker = BatchPrepareWorker(
            raw_urls=urls,
            bin_dir=self.get_bin_dir()
        )
        self.prepare_worker.prepare_finished_signal.connect(self._on_prepare_finished)
        self.prepare_worker.log_signal.connect(self.log_signal.emit)
        self.prepare_worker.start()

    def _on_prepare_finished(self, items: List[Dict[str, str]]):
        self.items = items
        if not items:
            QMessageBox.warning(self, "警告", "解析の結果、対象となる動画が見つかりませんでした。")
            self.start_batch_btn.setEnabled(True)
            self.stop_batch_btn.setEnabled(False)
            self.overall_progress.setRange(0, 100)
            self.overall_progress.setValue(0)
            self.status_label.setText("待機中")
            return

        self.list_widget.clear()
        for idx, item in enumerate(items):
            title = item.get('title', item.get('url', ''))
            playlist_title = item.get('playlist_title', '')
            type_tag = item.get('type_tag', '')
            tag = f"[{playlist_title}] " if playlist_title else ""
            list_item = QListWidgetItem(f"⏳ 待機中 [{idx+1}/{len(items)}]: {type_tag}{tag}{title}")
            self.list_widget.addItem(list_item)

        self.overall_progress.setRange(0, len(items))
        self.overall_progress.setValue(0)
        self.status_label.setText(f"ダウンロード実行中 (全 {len(items)} 本)")

        mode = "video" if self.radio_video.isChecked() else "audio"
        ext = self.combo_ext.currentText()
        height = self.combo_quality.currentText()
        fps = self.combo_fps.currentText()
        bitrate = self.combo_audio_bitrate.currentText()

        self.runner_worker = BatchRunnerWorker(
            items=items,
            output_dir=self.get_output_folder(),
            mode=mode,
            ext=ext,
            height=height,
            fps=fps,
            bitrate=bitrate,
            bin_dir=self.get_bin_dir()
        )
        self.runner_worker.url_started_signal.connect(self._on_url_started)
        self.runner_worker.url_progress_signal.connect(self._on_url_progress)
        self.runner_worker.url_completed_signal.connect(self._on_url_completed)
        self.runner_worker.batch_finished_signal.connect(self._on_batch_finished)
        self.runner_worker.log_signal.connect(self.log_signal.emit)
        self.runner_worker.start()

    def _stop_batch(self):
        if self.prepare_worker:
            self.prepare_worker.cancel()
        if self.runner_worker:
            self.runner_worker.cancel()
        self.stop_batch_btn.setEnabled(False)
        self.status_label.setText("中断処理中...")

    def _on_url_started(self, idx: int, item: dict):
        list_item = self.list_widget.item(idx)
        if list_item:
            title = item.get('title', item.get('url', ''))
            type_tag = item.get('type_tag', '')
            list_item.setText(f"⬇️ ダウンロード中 [{idx+1}/{len(self.items)}]: {type_tag}{title}")
            self.list_widget.scrollToItem(list_item)

    def _on_url_progress(self, idx: int, info: dict):
        pct = info.get('percent', 0.0)
        list_item = self.list_widget.item(idx)
        if list_item and idx < len(self.items):
            title = self.items[idx].get('title', '')
            type_tag = self.items[idx].get('type_tag', '')
            list_item.setText(f"⬇️ ダウンロード中 ({pct:.1f}%) [{idx+1}/{len(self.items)}]: {type_tag}{title}")

    def _on_url_completed(self, idx: int, success: bool, msg: str):
        list_item = self.list_widget.item(idx)
        if list_item and idx < len(self.items):
            title = self.items[idx].get('title', '')
            type_tag = self.items[idx].get('type_tag', '')
            if success:
                list_item.setText(f"✅ 完了 [{idx+1}/{len(self.items)}]: {type_tag}{title}")
                list_item.setForeground(Qt.green)
            else:
                list_item.setText(f"❌ 失敗 [{idx+1}/{len(self.items)}]: {type_tag}{title} ({msg})")
                list_item.setForeground(Qt.red)
        self.overall_progress.setValue(idx + 1)

    def _on_batch_finished(self, success_cnt: int, failed_cnt: int):
        self.start_batch_btn.setEnabled(True)
        self.stop_batch_btn.setEnabled(False)
        
        summary = f"一括ダウンロード完了 (成功: {success_cnt}件, 失敗: {failed_cnt}件)"
        self.status_label.setText(summary)
        self.log_signal.emit(summary)
        QMessageBox.information(self, "完了", summary)


