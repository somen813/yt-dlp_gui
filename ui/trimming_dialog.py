from typing import Optional
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QSlider, QMessageBox
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


def seconds_to_hhmmss(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def hhmmss_to_seconds(time_str: str) -> int:
    parts = time_str.strip().split(':')
    try:
        parts = [int(p) for p in parts if p.strip()]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 1:
            return parts[0]
    except Exception:
        pass
    return 0


class TrimmingDialog(QDialog):
    """動画の再生プレビューと範囲指定トリミングを行うダイアログ"""

    def __init__(self, stream_url: str, audio_url: str = "", initial_start: str = "", initial_end: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("範囲指定トリミング プレビュー")
        self.resize(540, 520)
        self.setMinimumSize(480, 450)

        self.stream_url = stream_url
        self.audio_url = audio_url
        self.result_start = initial_start
        self.result_end = initial_end

        # 動画用プレイヤー初期化
        self.video_player = QMediaPlayer(self)
        
        # 音声用プレイヤー初期化 (分離ストリーム対策)
        self.audio_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_player.setAudioOutput(self.audio_output)

        self._init_ui(initial_start, initial_end)

        if self.stream_url:
            self.video_player.setSource(QUrl(self.stream_url))
            
        if self.audio_url:
            self.audio_player.setSource(QUrl(self.audio_url))
        else:
            # 結合ストリームの場合は video_player に音声出力を設定
            self.video_player.setAudioOutput(self.audio_output)

    def _init_ui(self, initial_start: str, initial_end: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- 1. 動画プレビューエリア ---
        preview_box = QGroupBox("プレビュー画面")
        preview_layout = QVBoxLayout(preview_box)

        video_container = QHBoxLayout()
        video_container.setAlignment(Qt.AlignCenter)

        self.video_widget = QVideoWidget()
        self.video_widget.setFixedSize(480, 270)
        self.video_widget.setStyleSheet("background-color: #000; border: 1px solid #444; border-radius: 4px;")
        self.video_player.setVideoOutput(self.video_widget)
        video_container.addWidget(self.video_widget)

        preview_layout.addLayout(video_container)

        # 再生コントロール
        controls_layout = QHBoxLayout()
        self.play_btn = QPushButton("▶ 再生")
        self.play_btn.clicked.connect(self._toggle_play)
        controls_layout.addWidget(self.play_btn)

        self.preview_slider = QSlider(Qt.Horizontal)
        self.preview_slider.sliderMoved.connect(self._on_slider_moved)
        controls_layout.addWidget(self.preview_slider)

        self.preview_time_label = QLabel("00:00:00 / 00:00:00")
        self.preview_time_label.setStyleSheet("color: #ccc;")
        controls_layout.addWidget(self.preview_time_label)

        preview_layout.addLayout(controls_layout)
        layout.addWidget(preview_box)

        # プレイヤーシグナル
        self.video_player.positionChanged.connect(self._on_media_position_changed)
        self.video_player.durationChanged.connect(self._on_media_duration_changed)

        # --- 2. 範囲指定トリミング入力 ---
        trim_box = QGroupBox("トリミング範囲の設定")
        trim_layout = QVBoxLayout(trim_box)

        # 開始位置
        row_start = QHBoxLayout()
        row_start.addWidget(QLabel("開始位置 (hh:mm:ss):"))
        self.trim_start_edit = QLineEdit(initial_start)
        self.trim_start_edit.setPlaceholderText("00:00:00")
        row_start.addWidget(self.trim_start_edit)

        self.set_start_btn = QPushButton("📍 現在位置をセット")
        self.set_start_btn.clicked.connect(self._set_current_as_start)
        row_start.addWidget(self.set_start_btn)

        self.jump_start_btn = QPushButton("▶ 開始位置へジャンプ")
        self.jump_start_btn.clicked.connect(self._jump_to_start)
        row_start.addWidget(self.jump_start_btn)

        trim_layout.addLayout(row_start)

        # 終了位置
        row_end = QHBoxLayout()
        row_end.addWidget(QLabel("終了位置 (hh:mm:ss):"))
        self.trim_end_edit = QLineEdit(initial_end)
        self.trim_end_edit.setPlaceholderText("全編 (空欄)")
        row_end.addWidget(self.trim_end_edit)

        self.set_end_btn = QPushButton("📍 現在位置をセット")
        self.set_end_btn.clicked.connect(self._set_current_as_end)
        row_end.addWidget(self.set_end_btn)

        self.jump_end_btn = QPushButton("▶ 終了位置へジャンプ")
        self.jump_end_btn.clicked.connect(self._jump_to_end)
        row_end.addWidget(self.jump_end_btn)

        trim_layout.addLayout(row_end)

        layout.addWidget(trim_box)

        # --- 3. 確定 / キャンセル ボタン ---
        action_layout = QHBoxLayout()
        action_layout.addStretch()

        self.ok_btn = QPushButton("確定 (適用)")
        self.ok_btn.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; padding: 6px 20px;")
        self.ok_btn.clicked.connect(self._on_apply)
        action_layout.addWidget(self.ok_btn)

        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.clicked.connect(self.reject)
        action_layout.addWidget(self.cancel_btn)

        layout.addLayout(action_layout)

    def _toggle_play(self):
        if self.video_player.playbackState() == QMediaPlayer.PlayingState:
            self.video_player.pause()
            if self.audio_url:
                self.audio_player.pause()
            self.play_btn.setText("▶ 再生")
        else:
            self.video_player.play()
            if self.audio_url:
                self.audio_player.play()
            self.play_btn.setText("⏸ 一時停止")

    def _on_slider_moved(self, position: int):
        self.video_player.setPosition(position)
        if self.audio_url:
            self.audio_player.setPosition(position)

    def _on_media_position_changed(self, position: int):
        self.preview_slider.setValue(position)
        cur_sec = position // 1000
        dur_sec = self.video_player.duration() // 1000
        self.preview_time_label.setText(f"{seconds_to_hhmmss(cur_sec)} / {seconds_to_hhmmss(dur_sec)}")

    def _on_media_duration_changed(self, duration: int):
        self.preview_slider.setRange(0, duration)
        cur_sec = self.video_player.position() // 1000
        dur_sec = duration // 1000
        self.preview_time_label.setText(f"{seconds_to_hhmmss(cur_sec)} / {seconds_to_hhmmss(dur_sec)}")

    def _set_current_as_start(self):
        pos_sec = self.video_player.position() // 1000
        self.trim_start_edit.setText(seconds_to_hhmmss(pos_sec))

    def _set_current_as_end(self):
        pos_sec = self.video_player.position() // 1000
        self.trim_end_edit.setText(seconds_to_hhmmss(pos_sec))

    def _jump_to_start(self):
        text = self.trim_start_edit.text().strip()
        if text:
            sec = hhmmss_to_seconds(text)
            self.video_player.setPosition(sec * 1000)
            if self.audio_url:
                self.audio_player.setPosition(sec * 1000)

    def _jump_to_end(self):
        text = self.trim_end_edit.text().strip()
        if text:
            sec = hhmmss_to_seconds(text)
            self.video_player.setPosition(sec * 1000)
            if self.audio_url:
                self.audio_player.setPosition(sec * 1000)

    def _on_apply(self):
        self.result_start = self.trim_start_edit.text().strip()
        self.result_end = self.trim_end_edit.text().strip()

        # 簡単なバリデーション
        if self.result_start and self.result_end:
            start_sec = hhmmss_to_seconds(self.result_start)
            end_sec = hhmmss_to_seconds(self.result_end)
            if start_sec >= end_sec:
                QMessageBox.warning(self, "警告", "開始位置は終了位置より前である必要があります。")
                return

        self._stop_players()
        self.accept()

    def _stop_players(self):
        self.video_player.stop()
        if self.audio_url:
            self.audio_player.stop()

    def closeEvent(self, event):
        self._stop_players()
        super().closeEvent(event)
