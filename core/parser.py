import logging
from typing import Dict, Any, List, Optional, Callable
import yt_dlp

logger = logging.getLogger(__name__)

class VideoMetadataParser:
    """yt-dlp を使用して動画のメタデータ（解像度、FPS、音質、サムネイル等）を解析・取得するモジュール"""
    
    @staticmethod
    def parse_url(url: str, custom_ytdlp_path: Optional[str] = None) -> Dict[str, Any]:
        """
        URL から動画情報を抽出する。
        yt-dlp の dump-json 相当の情報を dict で返す。
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
        }
        
        if custom_ytdlp_path:
            ydl_opts['ffmpeg_location'] = custom_ytdlp_path
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise ValueError("動画情報を取得できませんでした。")
            
            # プレイリスト等の複数要素の場合
            if 'entries' in info:
                raise ValueError("プレイリストURLが入力されました。単体ダウンロードタブでは1動画のみ指定可能です。")
                
            return VideoMetadataParser._extract_summary(info)
            
    @staticmethod
    def _extract_summary(info: Dict[str, Any]) -> Dict[str, Any]:
        """生データからGUIに必要な表示用データと選択肢を整理して抽出する"""
        title = info.get('title', 'Unknown Title')
        uploader = info.get('uploader') or info.get('channel') or info.get('uploader_id') or 'Unknown Uploader'
        duration = info.get('duration', 0)
        thumbnail = info.get('thumbnail', '')
        
        formats = info.get('formats', [])
        
        heights = set()
        fps_list = set()
        audio_bitrates = set()
        
        for f in formats:
            # 動画トラック情報
            if f.get('vcodec') != 'none':
                height = f.get('height')
                if height and isinstance(height, int):
                    heights.add(height)
                fps = f.get('fps')
                if fps and isinstance(fps, (int, float)):
                    fps_list.add(int(fps))
                    
            # 音声トラック情報
            if f.get('acodec') != 'none':
                abr = f.get('abr')
                if abr and isinstance(abr, (int, float)):
                    audio_bitrates.add(int(abr))
                    
        # 整理整頓（降順ソート）
        sorted_heights = sorted(list(heights), reverse=True)
        sorted_fps = sorted(list(fps_list), reverse=True)
        sorted_bitrates = sorted(list(audio_bitrates), reverse=True)
        
        # 表示名整形リスト
        height_options = ["最高画質"] + [f"{h}p" for h in sorted_heights]
        fps_options = ["指定なし"] + [str(f) for f in sorted_fps]
        audio_options = ["最高音質"] + [f"{b}k" for b in sorted_bitrates]
        
        # プレビュー再生用ストリームURLの選出
        stream_url = ""
        audio_url = ""
        
        # 1. 結合フォーマット (動画 + 音声) を検索
        for f in formats:
            fmt_url = f.get('url', '')
            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            if fmt_url and fmt_url.startswith('http') and vcodec != 'none' and acodec != 'none':
                stream_url = fmt_url
                audio_url = ""
                break
                
        # 2. 結合フォーマットがない場合 (YouTube等の分離ストリーム)
        if not stream_url:
            # 動画 URL (480p/360p 付近優先の MP4)
            mp4_candidates = []
            for f in formats:
                fmt_url = f.get('url', '')
                vcodec = f.get('vcodec', 'none')
                ext = f.get('ext', '')
                height = f.get('height') or 0
                proto = str(f.get('protocol', ''))
                
                if fmt_url and vcodec != 'none' and ext == 'mp4' and proto == 'https':
                    mp4_candidates.append((abs(height - 480), fmt_url))
                    
            if mp4_candidates:
                mp4_candidates.sort(key=lambda x: x[0])
                stream_url = mp4_candidates[0][1]

            # 音声 URL (m4a/aac 優先)
            audio_candidates = []
            for f in formats:
                fmt_url = f.get('url', '')
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                ext = f.get('ext', '')
                proto = str(f.get('protocol', ''))
                
                if fmt_url and vcodec == 'none' and acodec != 'none' and proto == 'https':
                    priority = 1 if ext in ('m4a', 'mp4') else 2
                    audio_candidates.append((priority, fmt_url))
                    
            if audio_candidates:
                audio_candidates.sort(key=lambda x: x[0])
                audio_url = audio_candidates[0][1]

        # 3. フォールバック
        if not stream_url:
            for f in formats:
                fmt_url = f.get('url', '')
                vcodec = f.get('vcodec', 'none')
                if fmt_url and fmt_url.startswith('http') and vcodec != 'none':
                    stream_url = fmt_url
                    break

        return {
            'title': title,
            'uploader': uploader,
            'duration': duration,
            'duration_str': VideoMetadataParser._format_duration(duration),
            'thumbnail': thumbnail,
            'stream_url': stream_url,
            'audio_url': audio_url,
            'raw_info': info,
            'available_heights': sorted_heights,
            'available_fps': sorted_fps,
            'available_audio_bitrates': sorted_bitrates,
            'height_options': height_options,
            'fps_options': fps_options,
            'audio_options': audio_options
        }

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """秒数を hh:mm:ss または mm:ss に整形"""
        if not seconds:
            return "00:00"
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def expand_urls(urls: List[str], custom_ytdlp_path: Optional[str] = None, log_callback: Optional[Callable[[str], None]] = None) -> List[Dict[str, str]]:
        """
        与えられた URL リストを解析し、プレイリストやチャンネルの場合は内部の全動画（Videos/Shorts問わず）に再帰的に展開して返す。
        戻り値: [{'url': '...', 'title': '...', 'playlist_title': '...', 'type_tag': '...'}, ...]
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',
            'skip_download': True,
        }
        if custom_ytdlp_path:
            ydl_opts['ffmpeg_location'] = custom_ytdlp_path

        expanded_items: List[Dict[str, str]] = []
        seen_urls = set()

        def _fetch_entries(ydl, target_url: str, depth: int = 0):
            if depth > 3:
                return
            if log_callback:
                log_callback(f"URL解析中: {target_url}")
            try:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    return

                if 'entries' in info:
                    playlist_title = info.get('title') or "プレイリスト"
                    entries = [e for e in info['entries'] if e] if info.get('entries') else []
                    
                    if log_callback and depth == 0:
                        log_callback(f"プレイリスト/チャンネル検出: {playlist_title} ({len(entries)}項目)")

                    for entry in entries:
                        entry_type = entry.get('_type', '')
                        entry_url = entry.get('url') or entry.get('webpage_url')
                        
                        # サブプレイリスト / タブ（/videos, /shorts 等）の検出
                        if entry_type in ('playlist', 'multi_video') or (entry.get('entries') is not None) or (entry_url and any(path in entry_url for path in ['/videos', '/shorts', '/streams', '/playlists', 'list='])):
                            sub_target = entry_url or target_url
                            if sub_target not in seen_urls:
                                seen_urls.add(sub_target)
                                _fetch_entries(ydl, sub_target, depth + 1)
                        else:
                            v_url = entry.get('url') or entry.get('webpage_url')
                            v_id = entry.get('id')
                            if v_url and (v_url.startswith('http://') or v_url.startswith('https://')):
                                item_url = v_url
                            elif v_id:
                                item_url = f"https://www.youtube.com/watch?v={v_id}"
                            else:
                                item_url = target_url

                            if item_url in seen_urls:
                                continue
                            seen_urls.add(item_url)

                            item_title = entry.get('title') or item_url
                            is_shorts = "/shorts/" in item_url or "/shorts/" in str(entry.get('webpage_url', ''))
                            type_tag = "[Shorts] " if is_shorts else ""

                            expanded_items.append({
                                'url': item_url,
                                'title': item_title,
                                'playlist_title': playlist_title,
                                'type_tag': type_tag
                            })
                else:
                    # 単体動画
                    v_url = info.get('webpage_url') or target_url
                    if v_url not in seen_urls:
                        seen_urls.add(v_url)
                        v_title = info.get('title') or target_url
                        is_shorts = "/shorts/" in v_url or "/shorts/" in str(info.get('webpage_url', ''))
                        type_tag = "[Shorts] " if is_shorts else ""
                        expanded_items.append({
                            'url': v_url,
                            'title': v_title,
                            'playlist_title': '',
                            'type_tag': type_tag
                        })
            except Exception as e:
                logger.warning(f"URL解析エラー ({target_url}): {e}")
                if log_callback:
                    log_callback(f"[解析エラー] {target_url}: {e}")
                if target_url not in seen_urls:
                    seen_urls.add(target_url)
                    expanded_items.append({
                        'url': target_url,
                        'title': target_url,
                        'playlist_title': '',
                        'type_tag': ''
                    })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for raw_url in urls:
                _fetch_entries(ydl, raw_url, depth=0)

        return expanded_items


