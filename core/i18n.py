import json
import os
import threading

LANG_FILE = "/home/ubuntu/vee/users_db.json"
_lock = threading.Lock()
_cache = {"data": {}, "time": 0}
CACHE_TTL = 5

DEFAULT_LANG = "en"

LANGUAGES = {
    "en": "English",
    "zh": "中文",
    "ja": "日本語",
}

TRANSLATIONS = {
    "en": {
        "welcome": "Welcome! Your ID: {user_id}\n\nSend me a link from YouTube, TikTok, Instagram, Twitter, Bilibili, or other supported platforms.\n\nI can download:\n• Videos (up to 2GB with local Bot API)\n• Thumbnails\n• Audio (MP3)\n\nJust send a link to choose what to download!",
        "your_id": "Your Telegram ID: `{user_id}`\nUsername: @{username}\nName: {name}",
        "not_authorized": "You are not authorized to use this bot.",
        "rate_limit_exceeded": "Rate limit exceeded. Please try again later.",
        "unsupported_url": "Unsupported URL. Supported: YouTube, TikTok, Instagram, Twitter, Bilibili, etc.",
        "what_download": "What would you like to download?",
        "video": "🎬 Video",
        "audio": "🎵 Audio (MP3)",
        "thumbnail": "🖼️ Thumbnail",
        "downloading": "⬇️ Downloading...",
        "processing": "Processing... Please wait.",
        "download_complete": "✅ Download complete! Processing...",
        "file_too_large": "File too large ({size}). Maximum is 2GB.",
        "upload_failed": "Upload failed: {error}",
        "download_failed": "Download failed: {error}",
        "no_video_found": "No video found in this tweet.\n\nTry: 🖼️ Thumbnail to get the image, or 🎵 Audio (MP3) if it's a video with audio issues.",
        "no_thumbnail": "No thumbnail available.",
        "no_history": "No download history.",
        "history_title": "Your download history:",
        "select_quality": "Select quality for:\n{title}...",
        "loading_quality": "⏳ Loading quality options...",
        "uploading": "Uploading...",
        "fetching_thumbnail": "Fetching thumbnail...",
        "session_expired": "Session expired. Please send the link again.",
        "available_commands": "Available commands:\n/start - Start the bot\n/help - Show this help\n/history - Your download history\n\nJust send a link to download!",
        "admin_commands": "Available commands:\n/start - Start the bot\n/help - Show this help\n/history - Your download history\n\nAdmin commands:\n/allow <user_id> - Allow a user\n/block <user_id> - Block a user\n/users - List allowed users\n/stats - Bot usage stats\n/broadcast <message> - Broadcast to all users\n/userhistory <user_id> - View user history\n/rateinfo - View rate limit info\n/setrate <max> [on/off] - Set rate limit\n/queue - Download queue status\n/storage - Disk usage details\n/failed <user_id> - View failed downloads\n/cleanup - Clean temp files\n/status - Bot system status\n/cookie - Upload cookies file\n/lang - Change language\n\nFeatures:\n• aria2: Resumable multi-connection downloads\n• Auto cookie refresh (configurable)",
        "user_allowed": "User {user_id} has been allowed.",
        "user_blocked": "User {user_id} has been blocked.",
        "no_users": "No users yet.",
        "allowed_users": "Allowed users:",
        "broadcast_sent": "Broadcast sent to {success} users. Failed: {failed}",
        "no_users_to_show": "No users to show.",
        "select_user_history": "Select a user to view history:",
        "no_history_for_user": "No history for user {user_id}.",
        "rate_limit_status": "Rate Limit Status:\n- Status: {status}\n- Max downloads/hour: {max}\n- Active users tracked: {users}",
        "rate_limit_updated": "Rate limit updated: {max}/hour, {status}",
        "temp_cleaned": "Temp files cleaned up.",
        "usage_allow": "Usage: /allow <user_id>",
        "usage_block": "Usage: /block <user_id>",
        "usage_broadcast": "Usage: /broadcast <message>",
        "usage_setrate": "Usage: /setrate <max> [on/off]",
        "usage_failed": "Usage: /failed <user_id>",
        "invalid_user_id": "Invalid user ID. Must be a number.",
        "invalid_number": "Invalid number.",
        "value_range": "Value must be between 1-100",
        "queue_title": "📥 Download Queue\n\nActive downloads: {active}\nQueued: {queued}",
        "active_downloads": "Active:",
        "storage_title": "💾 Storage Status{alert}\n\nTotal disk: {total}\nUsed: {used} ({percent}%)\nFree: {free}\n\nTemp directory ({temp_dir}):\nFiles: {temp_files}\nSize: {temp_size}",
        "oldest": "Oldest: {filename}\n   {date}",
        "failed_title": "❌ Failed downloads for user {user_id}:",
        "no_failed": "No failed downloads for user {user_id}.",
        "status_title": "📊 Bot Status\n\nCPU: {cpu}%\nMemory: {memory}%\n\nTemp files: {temp_files}\nTemp size: {temp_size}\n\nRate limit: {rate_limit}/hour\nRate enabled: {rate_enabled}\nCleanup interval: {cleanup_interval}h\nTemp file max age: {temp_max_age}h",
        "language_changed": "Language changed to English.",
        "select_language": "Select your language:",
        "cached_file_used": "📂 Using cached file (downloaded recently)",
    },
    "zh": {
        "welcome": "欢迎！你的ID: {user_id}\n\n发送 YouTube、TikTok、Instagram、Twitter、B站 或其他支持的平台的链接。\n\n我可以下载：\n• 视频（本地Bot API最多2GB）\n• 缩略图\n• 音频（MP3）\n\n发送链接选择要下载的内容！",
        "your_id": "你的Telegram ID: `{user_id}`\n用户名: @{username}\n名字: {name}",
        "not_authorized": "你未被授权使用此机器人。",
        "rate_limit_exceeded": "超出速率限制。请稍后再试。",
        "unsupported_url": "不支持的链接。支持：YouTube、TikTok、Instagram、Twitter、B站等。",
        "what_download": "你想下载什么？",
        "video": "🎬 视频",
        "audio": "🎵 音频 (MP3)",
        "thumbnail": "🖼️ 缩略图",
        "downloading": "⬇️ 下载中...",
        "processing": "处理中...请稍候...",
        "download_complete": "✅ 下载完成！处理中...",
        "file_too_large": "文件太大（{size}）。最大支持2GB。",
        "upload_failed": "上传失败：{error}",
        "download_failed": "下载失败：{error}",
        "no_video_found": "未找到此推文中的视频。\n\n尝试：🖼️ 缩略图获取图片，或🎵 音频（MP3）如果视频音频有问题。",
        "no_thumbnail": "没有可用的缩略图。",
        "no_history": "没有下载历史。",
        "history_title": "你的下载历史：",
        "select_quality": "选择画质：\n{title}...",
        "loading_quality": "⏳ 加载画质选项中...",
        "uploading": "上传中...",
        "fetching_thumbnail": "获取缩略图中...",
        "session_expired": "会话已过期。请重新发送链接。",
        "available_commands": "可用命令：\n/start - 启动机器人\n/help - 显示帮助\n/history - 下载历史\n\n发送链接下载！",
        "admin_commands": "可用命令：\n/start - 启动机器人\n/help - 显示帮助\n/history - 下载历史\n\n管理员命令：\n/allow <user_id> - 允许用户\n/block <user_id> - 封禁用户\n/users - 列出用户\n/stats - 使用统计\n/broadcast <message> - 广播消息\n/userhistory <user_id> - 查看用户历史\n/rateinfo - 速率限制信息\n/setrate <max> [on/off] - 设置速率限制\n/queue - 下载队列状态\n/storage - 磁盘使用情况\n/failed <user_id> - 查看失败下载\n/cleanup - 清理临时文件\n/status - 系统状态\n/cookie - 上传cookies文件\n/lang - 更改语言\n\n功能：\n• aria2: 可恢复多连接下载\n• 自动刷新cookies（可配置）",
        "user_allowed": "用户 {user_id} 已允许。",
        "user_blocked": "用户 {user_id} 已封禁。",
        "no_users": "还没有用户。",
        "allowed_users": "允许的用户：",
        "broadcast_sent": "广播已发送给 {success} 个用户。失败：{failed}",
        "no_users_to_show": "没有可显示的用户。",
        "select_user_history": "选择查看历史的用户：",
        "no_history_for_user": "用户 {user_id} 没有历史。",
        "rate_limit_status": "速率限制状态：\n- 状态：{status}\n- 每小时最大下载：{max}\n- 追踪用户数：{users}",
        "rate_limit_updated": "速率限制已更新：{max}/小时，{status}",
        "temp_cleaned": "临时文件已清理。",
        "usage_allow": "用法：/allow <user_id>",
        "usage_block": "用法：/block <user_id>",
        "usage_broadcast": "用法：/broadcast <message>",
        "usage_setrate": "用法：/setrate <max> [on/off]",
        "usage_failed": "用法：/failed <user_id>",
        "invalid_user_id": "无效的用户ID。必须是数字。",
        "invalid_number": "无效的数字。",
        "value_range": "值必须在1-100之间",
        "queue_title": "📥 下载队列\n\n活跃下载：{active}\n排队中：{queued}",
        "active_downloads": "活跃中：",
        "storage_title": "💾 存储状态{alert}\n\n总磁盘：{total}\n已用：{used} ({percent}%)\n可用：{free}\n\n临时目录（{temp_dir}）：\n文件数：{temp_files}\n大小：{temp_size}",
        "oldest": "最旧：{filename}\n   {date}",
        "failed_title": "❌ 用户 {user_id} 的失败下载：",
        "no_failed": "用户 {user_id} 没有失败的下载。",
        "status_title": "📊 机器人状态\n\nCPU：{cpu}%\n内存：{memory}%\n\n临时文件：{temp_files}\n临时大小：{temp_size}\n\n速率限制：{rate_limit}/小时\n速率启用：{rate_enabled}\n清理间隔：{cleanup_interval}h\n临时文件最大保留：{temp_max_age}h",
        "language_changed": "语言已更改为中文。",
        "select_language": "选择你的语言：",
        "cached_file_used": "📂 使用缓存文件（最近下载）",
    },
    "ja": {
        "welcome": "ようこそ！あなたのID: {user_id}\n\nYouTube、TikTok、Instagram、Twitter Bilibiliなどのサポートされているプラットフォームのリンクを送信してください。\n\nダウンロード可能：\n• 動画（ローカルBot APIで最大2GB）\n• サムネイル\n• 音声（MP3）\n\nリンクを送信してダウンロードを選択！",
        "your_id": "あなたのTelegram ID: `{user_id}`\nユーザー名: @{username}\n名前: {name}",
        "not_authorized": "このボットの使用は許可されていません。",
        "rate_limit_exceeded": "レート制限を超えました。 後でもう一度お試しください。",
        "unsupported_url": "サポートされていないURLです。対応：YouTube、TikTok、Instagram、Twitter、Bilibiliなど。",
        "what_download": "何をダウンロードしますか？",
        "video": "🎬 動画",
        "audio": "🎵 音声 (MP3)",
        "thumbnail": "🖼️ サムネイル",
        "downloading": "⬇️ ダウンロード中...",
        "processing": "処理中...お待ちください。",
        "download_complete": "✅ ダウンロード完了！処理中...",
        "file_too_large": "ファイルが大きすぎます（{size}）。最大2GBです。",
        "upload_failed": "アップロード失敗：{error}",
        "download_failed": "ダウンロード失敗：{error}",
        "no_video_found": "このツイートに動画が見つかりません。\n\n試す：🖼️ サムネイルで画像を取得、または🎵 音声（MP3）動画に音声問題がある場合。",
        "no_thumbnail": "サムネイルがありません。",
        "no_history": "ダウンロード履歴がありません。",
        "history_title": "あなたのダウンロード履歴：",
        "select_quality": "品質を選択：\n{title}...",
        "loading_quality": "⏳ 品質オプションを読み込み中...",
        "uploading": "アップロード中...",
        "fetching_thumbnail": "サムネイルを取得中...",
        "session_expired": "セッションが期限切れです。再度リンクを送信してください。",
        "available_commands": "利用可能なコマンド：\n/start - ボットを開始\n/help - ヘルプを表示\n/history - ダウンロード履歴\n\nリンクを送信してダウンロード！",
        "admin_commands": "利用可能なコマンド：\n/start - ボットを開始\n/help - ヘルプを表示\n/history - ダウンロード履歴\n\n管理者コマンド：\n/allow <user_id> - ユーザーを許可\n/block <user_id> - ユーザーをブロック\n/users - ユーザー一覧\n/stats - 使用統計\n/broadcast <message> - ブロードキャスト\n/userhistory <user_id> - ユーザー履歴\n/rateinfo - レート制限情報\n/setrate <max> [on/off] - レート制限設定\n/queue - ダウンロードキュー状態\n/storage - ディスク使用量\n/failed <user_id> - 失敗したダウンロード\n/cleanup - 一時ファイル清理\n/status - システム状態\n/cookie - クッキーファイルをアップロード\n/lang - 言語変更\n\n機能：\n• aria2: 再開可能なマルチ接続ダウンロード\n• 自動Cookie更新（設定可能）",
        "user_allowed": "ユーザー {user_id} を許可しました。",
        "user_blocked": "ユーザー {user_id} をブロックしました。",
        "no_users": "ユーザーがいません。",
        "allowed_users": "許可されたユーザー：",
        "broadcast_sent": "ブロードキャストを {success} 人に送信しました。失敗：{failed}",
        "no_users_to_show": "表示するユーザーがいません。",
        "select_user_history": "履歴を表示するユーザーを選択：",
        "no_history_for_user": "ユーザー {user_id} の履歴がありません。",
        "rate_limit_status": "レート制限状態：\n- ステータス：{status}\n- 1時間あたりの最大ダウンロード：{max}\n- トラッキング中のユーザー：{users}",
        "rate_limit_updated": "レート制限を更新：{max}/時間、{status}",
        "temp_cleaned": "一時ファイルを清理しました。",
        "usage_allow": "用法：/allow <user_id>",
        "usage_block": "用法：/block <user_id>",
        "usage_broadcast": "用法：/broadcast <message>",
        "usage_setrate": "用法：/setrate <max> [on/off]",
        "usage_failed": "用法：/failed <user_id>",
        "invalid_user_id": "無効なユーザーID。数字である必要があります。",
        "invalid_number": "無効な数字。",
        "value_range": "値は1-100の間である必要があります",
        "queue_title": "📥 ダウンロードキュー\n\nアクティブなダウンロード：{active}\nキュー：{queued}",
        "active_downloads": "アクティブ：",
        "storage_title": "💾 ストレージ状態{alert}\n\n総ディスク：{total}\n使用：{used} ({percent}%)\n空き：{free}\n\n一時ディレクトリ（{temp_dir}）：\nファイル数：{temp_files}\nサイズ：{temp_size}",
        "oldest": "最古：{filename}\n   {date}",
        "failed_title": "❌ ユーザー {user_id} の失敗したダウンロード：",
        "no_failed": "ユーザー {user_id} に失敗したダウンロードはありません。",
        "status_title": "📊 ボット状態\n\nCPU：{cpu}%\nメモリ：{memory}%\n\n一時ファイル：{temp_files}\n一時サイズ：{temp_size}\n\nレート制限：{rate_limit}/時間\nレート有効：{rate_enabled}\n清理間隔：{cleanup_interval}h\n一時ファイル最大期間：{temp_max_age}h",
        "language_changed": "言語を日本語に変更しました。",
        "select_language": "言語を選択：",
        "cached_file_used": "📂 キャッシュファイルを使用（最近ダウンロード）",
    },
}


def _load_users_db():
    global _cache
    import time
    now = time.time()
    if _cache["data"] is not None and (now - _cache["time"]) < CACHE_TTL:
        return _cache["data"]
    
    if os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, "r") as f:
                _cache["data"] = json.load(f)
                _cache["time"] = now
                return _cache["data"]
        except:
            pass
    return {}


def _save_users_db(data):
    global _cache
    with _lock:
        try:
            with open(LANG_FILE, "w") as f:
                json.dump(data, f, indent=2)
            _cache["data"] = data
            _cache["time"] = time.time()
        except:
            pass


import time


def get_user_lang(user_id: int) -> str:
    db = _load_users_db()
    user_id_str = str(user_id)
    if user_id_str in db and isinstance(db[user_id_str], dict):
        return db[user_id_str].get("lang", DEFAULT_LANG)
    return DEFAULT_LANG


def set_user_lang(user_id: int, lang: str):
    db = _load_users_db()
    user_id_str = str(user_id)
    if user_id_str not in db:
        db[user_id_str] = {}
    if not isinstance(db[user_id_str], dict):
        db[user_id_str] = {}
    db[user_id_str]["lang"] = lang
    _save_users_db(db)


def t(key: str, user_id: int = None, **kwargs) -> str:
    if user_id:
        lang = get_user_lang(user_id)
    else:
        lang = DEFAULT_LANG
    
    translations = TRANSLATIONS.get(lang, TRANSLATIONS["en"])
    text = translations.get(key, TRANSLATIONS["en"].get(key, key))
    
    return text.format(**kwargs)
