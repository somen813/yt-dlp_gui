import os
import sys
import shutil
import logging
import urllib.request
import json
import subprocess
from typing import Dict, Any, List, Optional, Tuple, Callable

logger = logging.getLogger(__name__)

class YtdlpUpdater:
    """yt-dlpのアップデート・ダウングレード・バージョン管理クラス"""
    
    GITHUB_RELEASES_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
    
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.getcwd()
        self.bin_dir = os.path.join(self.base_dir, "bin")
        self.versions_dir = os.path.join(self.base_dir, "yt-dlp_versions")
        
        os.makedirs(self.bin_dir, exist_ok=True)
        os.makedirs(self.versions_dir, exist_ok=True)
        
        self.exe_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
        self.exe_path = os.path.join(self.bin_dir, self.exe_name)

    def get_current_version(self) -> str:
        """現在の yt-dlp バイナリのバージョンを取得する。存在しないか実行不可なら python パッケージ版バージョンを返す。"""
        if os.path.exists(self.exe_path):
            try:
                res = subprocess.run(
                    [self.exe_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception as e:
                logger.warning(f"バイナリ実行によるバージョン取得失敗: {e}")

        # フォールバック: インストールされている yt_dlp モジュールのバージョン
        try:
            import yt_dlp.version
            return yt_dlp.version.__version__
        except Exception:
            return "不明"

    def check_latest_release(self) -> Tuple[bool, str, str]:
        """
        最新バージョンをGitHub APIから取得して現在版と比較する。
        Return: (更新があるか: bool, 最新バージョン: str, ダウンロードURL: str)
        """
        try:
            req = urllib.request.Request(
                self.GITHUB_RELEASES_API,
                headers={'User-Agent': 'YtdlpGUI-App'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    latest_tag = data.get('tag_name', '').strip()
                    
                    download_url = ""
                    for asset in data.get('assets', []):
                        if asset.get('name') == self.exe_name:
                            download_url = asset.get('browser_download_url', '')
                            break
                            
                    current_ver = self.get_current_version()
                    has_update = (latest_tag != "" and latest_tag != current_ver)
                    return has_update, latest_tag, download_url
        except Exception as e:
            logger.error(f"GitHub Releases API チェック失敗: {e}")
            
        return False, "", ""

    def update_ytdlp(self, download_url: str, progress_callback: Optional[Callable[[int], None]] = None) -> Tuple[bool, str]:
        """最新バイナリをダウンロードして更新する。更新前にバックアップを作成。"""
        current_ver = self.get_current_version()
        
        # 1. バックアップ作成
        if current_ver and current_ver != "不明" and os.path.exists(self.exe_path):
            backup_folder = os.path.join(self.versions_dir, current_ver)
            os.makedirs(backup_folder, exist_ok=True)
            backup_path = os.path.join(backup_folder, self.exe_name)
            try:
                shutil.copy2(self.exe_path, backup_path)
                logger.info(f"バックアップ作成完了: {backup_path}")
            except Exception as e:
                logger.warning(f"バックアップ作成失敗: {e}")

        # 2. 新バイナリのダウンロード
        temp_path = self.exe_path + ".tmp"
        try:
            req = urllib.request.Request(
                download_url,
                headers={'User-Agent': 'YtdlpGUI-App'}
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(temp_path, 'wb') as out_file:
                total_size = int(resp.headers.get('content-length', 0))
                downloaded = 0
                block_size = 8192
                
                while True:
                    buffer = resp.read(block_size)
                    if not buffer:
                        break
                    downloaded += len(buffer)
                    out_file.write(buffer)
                    if total_size > 0 and progress_callback:
                        pct = int((downloaded / total_size) * 100)
                        progress_callback(pct)

            # ダウンロード成功後、安全に置換
            if os.path.exists(self.exe_path):
                os.remove(self.exe_path)
            os.rename(temp_path, self.exe_path)
            
            # パーミッション設定 (Linux/macOS用)
            if sys.platform != "win32":
                os.chmod(self.exe_path, 0o755)

            new_ver = self.get_current_version()
            return True, f"yt-dlp をバージョン {new_ver} に更新しました。"
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False, f"アップデート中にエラーが発生しました: {e}"

    def list_saved_versions(self) -> List[str]:
        """`yt-dlp_versions/` 配下に保存されている過去バージョン一覧を取得（新しい順）"""
        if not os.path.exists(self.versions_dir):
            return []
        
        versions = []
        for entry in os.listdir(self.versions_dir):
            dir_path = os.path.join(self.versions_dir, entry)
            exe_in_dir = os.path.join(dir_path, self.exe_name)
            if os.path.isdir(dir_path) and os.path.exists(exe_in_dir):
                versions.append(entry)
                
        versions.sort(reverse=True)
        return versions

    def restore_version(self, target_version: str) -> Tuple[bool, str]:
        """指定した過去バージョンにダウングレード・復元する"""
        source_exe = os.path.join(self.versions_dir, target_version, self.exe_name)
        if not os.path.exists(source_exe):
            return False, f"指定されたバージョン ({target_version}) のバイナリが見つかりません。"
            
        try:
            # 現在のをバックアップ
            current_ver = self.get_current_version()
            if current_ver and current_ver != "不明" and current_ver != target_version:
                cur_backup = os.path.join(self.versions_dir, current_ver)
                os.makedirs(cur_backup, exist_ok=True)
                shutil.copy2(self.exe_path, os.path.join(cur_backup, self.exe_name))

            shutil.copy2(source_exe, self.exe_path)
            return True, f"yt-dlp をバージョン {target_version} に復元しました。"
        except Exception as e:
            return False, f"復元中にエラーが発生しました: {e}"

    def cleanup_old_versions(self, keep_limit: int = 5):
        """保存バージョンが上限を超えている場合に古いものを削除"""
        versions = self.list_saved_versions()
        if len(versions) > keep_limit:
            to_delete = versions[keep_limit:]
            for ver in to_delete:
                del_path = os.path.join(self.versions_dir, ver)
                try:
                    shutil.rmtree(del_path)
                    logger.info(f"古いバックアップバージョン削除: {ver}")
                except Exception as e:
                    logger.warning(f"バージョンフォルダの削除失敗 ({ver}): {e}")
