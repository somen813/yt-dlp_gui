import os
import sys
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from PySide6.QtCore import QObject, Signal, QThread
import yt_dlp

logger = logging.getLogger(__name__)

def parse_time_to_seconds(time_str: Optional[str]) -> Optional[float]:
    """hh:mm:ss または mm:ss または 秒数文字列を float の秒数に変換"""
    if not time_str or not str(time_str).strip():
        return None
    parts = str(time_str).strip().split(':')
    try:
        parts = [float(p) for p in parts if p.strip()]
        if len(parts) == 3:
            return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60.0 + parts[1]
        elif len(parts) == 1:
            return parts[0]
    except ValueError:
        pass
    return None


class DownloadWorker(QThread):
    """バックグラウンドでダウンロードを実行するスレッドクラス"""
    progress_signal = Signal(dict)
    log_signal = Signal(str)
    finished_signal = Signal(bool, str)

    def __init__(
        self,
        url: str,
        output_dir: str,
        mode: str = "video",  # "video" or "audio"
        ext: str = "mp4",
        height: Optional[str] = None,
        fps: Optional[str] = None,
        audio_bitrate: Optional[str] = None,
        section_start: Optional[str] = None,  # hh:mm:ss or mm:ss or seconds
        section_end: Optional[str] = None,
        bin_dir: Optional[str] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        self.url = url
        self.output_dir = output_dir
        self.mode = mode
        self.ext = ext
        self.height = height
        self.fps = fps
        self.audio_bitrate = audio_bitrate
        self.section_start = section_start
        self.section_end = section_end
        self.bin_dir = bin_dir or os.path.join(os.getcwd(), "bin")
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _progress_hook(self, d: Dict[str, Any]):
        if self._is_cancelled:
            raise yt_dlp.utils.DownloadError("ユーザーによってキャンセルされました。")

        status = d.get('status')
        if status == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded_bytes = d.get('downloaded_bytes', 0)
            
            percent = 0.0
            if total_bytes > 0:
                percent = (downloaded_bytes / total_bytes) * 100.0
                
            speed = d.get('speed', 0) or 0
            speed_mb = speed / (1024 * 1024) if speed else 0.0
            eta = d.get('eta', 0) or 0

            speed_str = f"{speed_mb:.2f} MB/s" if speed else "N/A"
            eta_str = f"{eta}秒" if eta else "不明"
            
            filename = os.path.basename(d.get('filename', ''))
            
            info = {
                'status': 'downloading',
                'percent': percent,
                'downloaded_bytes': downloaded_bytes,
                'total_bytes': total_bytes,
                'speed': speed_str,
                'eta': eta_str,
                'filename': filename,
                'raw': d
            }
            self.progress_signal.emit(info)

        elif status == 'finished':
            info = {
                'status': 'finished',
                'percent': 100.0,
                'filename': os.path.basename(d.get('filename', '')),
                'raw': d
            }
            self.progress_signal.emit(info)
            self.log_signal.emit(f"ダウンロード完了: {d.get('filename')}")

    def _logger_callback(self, msg: str):
        self.log_signal.emit(msg)
        # ログファイル書き込み
        self._write_to_logfile(msg)

    def _write_to_logfile(self, msg: str):
        try:
            log_dir = os.path.join(os.getcwd(), "data", "logs")
            os.makedirs(log_dir, exist_ok=True)
            today_str = datetime.now().strftime("%Y-%m-%d")
            log_path = os.path.join(log_dir, f"{today_str}.log")
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {msg}\n")
        except Exception as e:
            logger.error(f"Failed to write log: {e}")

    def build_yt_dlp_opts(self) -> Dict[str, Any]:
        outtmpl = os.path.join(self.output_dir, '%(title)s [%(id)s].%(ext)s')
        
        ydl_opts: Dict[str, Any] = {
            'outtmpl': outtmpl,
            'progress_hooks': [self._progress_hook],
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'logtostderr': False,
            'quiet': False,
        }

        # ffmpeg パスの指定
        ffmpeg_bin = os.path.join(self.bin_dir, "ffmpeg.exe") if sys.platform == "win32" else os.path.join(self.bin_dir, "ffmpeg")
        if os.path.exists(ffmpeg_bin):
            ydl_opts['ffmpeg_location'] = self.bin_dir

        # フォーマット指定の構築
        if self.mode == "audio":
            ydl_opts['format'] = 'bestaudio/best'
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': self.ext,  # 'mp3' or 'wav'
                'preferredquality': '192',
            }]
            if self.audio_bitrate and self.audio_bitrate != "最高音質":
                bitrate_clean = self.audio_bitrate.replace('k', '')
                postprocessors[0]['preferredquality'] = bitrate_clean
            ydl_opts['postprocessors'] = postprocessors
        else:
            # 動画モード
            format_parts = []
            
            # 画質フィルタ
            height_filter = ""
            if self.height and self.height != "最高画質":
                h_val = self.height.replace('p', '')
                height_filter = f"[height<={h_val}]"
                
            # FPSフィルタ
            fps_filter = ""
            if self.fps and self.fps != "指定なし":
                fps_filter = f"[fps<={self.fps}]"
                
            # ビットレートフィルタ
            audio_filter = ""
            if self.audio_bitrate and self.audio_bitrate != "最高音質":
                b_val = self.audio_bitrate.replace('k', '')
                audio_filter = f"[abr<={b_val}]"
                
            # 拡張子条件
            ext_filter = f"[ext={self.ext}]" if self.ext else ""
            
            # yt-dlp フォーマット選択文字列
            # 例: bestvideo[height<=1080][fps<=60][ext=mp4]+bestaudio[abr<=192]/bestvideo[height<=1080]+bestaudio/best
            primary_fmt = f"bestvideo{height_filter}{fps_filter}{ext_filter}+bestaudio{audio_filter}/bestvideo{height_filter}{fps_filter}+bestaudio/best"
            ydl_opts['format'] = primary_fmt
            ydl_opts['merge_output_format'] = self.ext

        # トリミング指定 (download-sections)
        if self.section_start or self.section_end:
            start_sec = parse_time_to_seconds(self.section_start) or 0.0
            end_sec = parse_time_to_seconds(self.section_end) or float('inf')
            
            ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(start_sec, end_sec)])
            ydl_opts['force_keyframes_at_cuts'] = True

        return ydl_opts

    def run(self):
        try:
            self.log_signal.emit(f"ダウンロード開始: {self.url}")
            opts = self.build_yt_dlp_opts()
            
            class CustomLogger:
                def __init__(self, callback):
                    self.callback = callback
                def debug(self, msg):
                    if not msg.startswith('[debug] '):
                        self.callback(msg)
                def info(self, msg):
                    self.callback(msg)
                def warning(self, msg):
                    self.callback(f"[警告] {msg}")
                def error(self, msg):
                    self.callback(f"[エラー] {msg}")
                    
            opts['logger'] = CustomLogger(self._logger_callback)

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([self.url])
                
            if not self._is_cancelled:
                self.finished_signal.emit(True, "ダウンロードが完了しました。")
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Download error: {err_msg}")
            self.log_signal.emit(f"エラー発生: {err_msg}")
            self.finished_signal.emit(False, f"エラーが発生しました: {err_msg}")
