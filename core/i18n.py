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
        "welcome": """👋 Welcome! Your ID: `{user_id}`

📎 Send me a link from YouTube, TikTok, Instagram, Twitter, Bilibili, Spotify, or other supported platforms.

✨ I can download:
• 🎬 Videos (up to 2GB)
• 🎵 Audio (MP3)
• 🖼️ Thumbnails

Just send a link to get started!""",
        "your_id": "🆔 Your Telegram ID: `{user_id}`\n👤 Username: @{username}\n📛 Name: {name}",
        "not_authorized": "🔒 You are not authorized to use this bot.",
        "rate_limit_exceeded": "⏳ Rate limit exceeded. Please try again later.",
        "unsupported_url": "❌ Unsupported URL.\n\n✅ Supported: YouTube, TikTok, Instagram, Twitter, Bilibili, Spotify, and more.",
        "what_download": "📥 What would you like to download?",
        "video": "🎬 Video",
        "audio": "🎵 Audio (MP3)",
        "thumbnail": "🖼️ Thumbnail",
        "downloading": "⬇️ Downloading...",
        "processing": "⚙️ Processing... Please wait.",
        "download_complete": "✅ Download complete! Processing...",
        "file_too_large": "📦 File too large ({size}). Maximum is 2GB.",
        "upload_failed": "📤 Upload failed: {error}",
        "download_failed": "❌ Download failed: {error}",
        "no_video_found": "😕 No video found in this tweet.\n\nTry: 🖼️ Thumbnail to get the image, or 🎵 Audio (MP3) if it's a video with audio issues.",
        "no_thumbnail": "🖼️ No thumbnail available.",
        "no_history": "📭 No download history.",
        "history_title": "📜 Your download history:",
        "select_quality": "🎞️ Select quality for:\n{title}...",
        "loading_quality": "⏳ Loading quality options...",
        "uploading": "📤 Uploading to Telegram...",
        "fetching_thumbnail": "🖼️ Fetching thumbnail...",
        "session_expired": "⏰ Session expired. Please send the link again.",
        "available_commands": """📋 Available commands:

/start - Start the bot
/help - Show this help
/history - Your download history
/lang - Change language

Just send a link to download!""",
        "admin_commands": """⚙️ Admin Commands

📋 General:
/start - Start the bot
/help - Show help
/history - Download history

👥 User Management:
/allow <user_id> - Allow a user
/block <user_id> - Block a user
/users - List allowed users
/broadcast <message> - Broadcast message
/userhistory <user_id> - View user history

📊 Monitoring:
/stats - Bot usage stats
/queue - Download queue status
/storage - Disk usage details
/status - Bot system status

⚡ Rate Limiting:
/rateinfo - View rate limit info
/setrate <max> [on/off] - Set rate limit

🧹 Maintenance:
/failed <user_id> - View failed downloads
/cleanup - Clean temp files
/cookie - Upload cookies file

🌐 Language:
/lang - Change language

✨ Features:
• aria2: Multi-connection downloads
• Auto cookie refresh""",
        "user_allowed": "✅ User {user_id} has been allowed.",
        "user_blocked": "🚫 User {user_id} has been blocked.",
        "no_users": "👥 No users yet.",
        "allowed_users": "👥 Allowed users:",
        "broadcast_sent": "📢 Broadcast sent to {success} users. Failed: {failed}",
        "no_users_to_show": "👥 No users to show.",
        "select_user_history": "👤 Select a user to view history:",
        "no_history_for_user": "📭 No history for user {user_id}.",
        "rate_limit_status": "⚡ Rate Limit Status\n\n• Status: {status}\n• Max downloads/hour: {max}\n• Active users: {users}",
        "rate_limit_updated": "✅ Rate limit updated: {max}/hour, {status}",
        "temp_cleaned": "🧹 Temp files cleaned up.",
        "usage_allow": "📝 Usage: /allow <user_id>",
        "usage_block": "📝 Usage: /block <user_id>",
        "usage_broadcast": "📝 Usage: /broadcast <message>",
        "usage_setrate": "📝 Usage: /setrate <max> [on/off]",
        "usage_failed": "📝 Usage: /failed <user_id>",
        "invalid_user_id": "❌ Invalid user ID. Must be a number.",
        "invalid_number": "❌ Invalid number.",
        "value_range": "❌ Value must be between 1-100",
        "queue_title": "📥 Download Queue\n\nActive: {active}\nQueued: {queued}",
        "active_downloads": "▶️ Active:",
        "storage_title": "💾 Storage Status{alert}\n\n📀 Total: {total}\n📁 Used: {used} ({percent}%)\n💿 Free: {free}\n\n📂 Temp ({temp_dir}):\n• Files: {temp_files}\n• Size: {temp_size}",
        "oldest": "📅 Oldest: {filename}\n   {date}",
        "failed_title": "❌ Failed downloads for user {user_id}:",
        "no_failed": "✅ No failed downloads for user {user_id}.",
        "status_title": "📊 Bot Status\n\n🖥️ CPU: {cpu}%\n💾 Memory: {memory}%\n\n📂 Temp Files: {temp_files}\n📦 Temp Size: {temp_size}\n\n⚡ Rate: {rate_limit}/hour ({rate_enabled})\n🕐 Cleanup: {cleanup_interval}h\n⏰ Max Age: {temp_max_age}h",
        "language_changed": "✅ Language changed to English.",
        "select_language": "🌐 Select your language:",
        "cached_file_used": "📂 Using cached file (downloaded recently)",
    },
    "zh": {
        "welcome": """👋 欢迎！你的ID: `{user_id}`

📎 发送 YouTube、TikTok、Instagram、Twitter、B站、Spotify 或其他支持的平台的链接。

✨ 我可以下载：
• 🎬 视频（最大2GB）
• 🎵 音频（MP3）
• 🖼️ 缩略图

发送链接开始使用！""",
        "your_id": "🆔 你的Telegram ID: `{user_id}`\n👤 用户名: @{username}\n📛 名字: {name}",
        "not_authorized": "🔒 你未被授权使用此机器人。",
        "rate_limit_exceeded": "⏳ 超出速率限制。请稍后再试。",
        "unsupported_url": "❌ 不支持的链接。\n\n✅ 支持：YouTube、TikTok、Instagram、Twitter、B站、Spotify等。",
        "what_download": "📥 你想下载什么？",
        "video": "🎬 视频",
        "audio": "🎵 音频 (MP3)",
        "thumbnail": "🖼️ 缩略图",
        "downloading": "⬇️ 下载中...",
        "processing": "⚙️ 处理中...请稍候...",
        "download_complete": "✅ 下载完成！处理中...",
        "file_too_large": "📦 文件太大（{size}）。最大支持2GB。",
        "upload_failed": "📤 上传失败：{error}",
        "download_failed": "❌ 下载失败：{error}",
        "no_video_found": "😕 未找到此推文中的视频。\n\n尝试：🖼️ 缩略图获取图片，或🎵 音频（MP3）如果视频音频有问题。",
        "no_thumbnail": "🖼️ 没有可用的缩略图。",
        "no_history": "📭 没有下载历史。",
        "history_title": "📜 你的下载历史：",
        "select_quality": "🎞️ 选择画质：\n{title}...",
        "loading_quality": "⏳ 加载画质选项中...",
        "uploading": "📤 上传到Telegram中...",
        "fetching_thumbnail": "🖼️ 获取缩略图中...",
        "session_expired": "⏰ 会话已过期。请重新发送链接。",
        "available_commands": """📋 可用命令：

/start - 启动机器人
/help - 显示帮助
/history - 下载历史
/lang - 更改语言

发送链接下载！""",
        "admin_commands": """⚙️ 管理员命令

📋 通用：
/start - 启动机器人
/help - 显示帮助
/history - 下载历史

👥 用户管理：
/allow <user_id> - 允许用户
/block <user_id> - 封禁用户
/users - 列出用户
/broadcast <message> - 广播消息
/userhistory <user_id> - 查看用户历史

📊 监控：
/stats - 使用统计
/queue - 下载队列状态
/storage - 磁盘使用情况
/status - 系统状态

⚡ 速率限制：
/rateinfo - 速率限制信息
/setrate <max> [on/off] - 设置速率限制

🧹 维护：
/failed <user_id> - 查看失败下载
/cleanup - 清理临时文件
/cookie - 上传cookies文件

🌐 语言：
/lang - 更改语言

✨ 功能：
• aria2: 多连接下载
• 自动刷新cookies""",
        "user_allowed": "✅ 用户 {user_id} 已允许。",
        "user_blocked": "🚫 用户 {user_id} 已封禁。",
        "no_users": "👥 还没有用户。",
        "allowed_users": "👥 允许的用户：",
        "broadcast_sent": "📢 广播已发送给 {success} 个用户。失败：{failed}",
        "no_users_to_show": "👥 没有可显示的用户。",
        "select_user_history": "👤 选择查看历史的用户：",
        "no_history_for_user": "📭 用户 {user_id} 没有历史。",
        "rate_limit_status": "⚡ 速率限制状态\n\n• 状态：{status}\n• 每小时最大下载：{max}\n• 追踪用户数：{users}",
        "rate_limit_updated": "✅ 速率限制已更新：{max}/小时，{status}",
        "temp_cleaned": "🧹 临时文件已清理。",
        "usage_allow": "📝 用法：/allow <user_id>",
        "usage_block": "📝 用法：/block <user_id>",
        "usage_broadcast": "📝 用法：/broadcast <message>",
        "usage_setrate": "📝 用法：/setrate <max> [on/off]",
        "usage_failed": "📝 用法：/failed <user_id>",
        "invalid_user_id": "❌ 无效的用户ID。必须是数字。",
        "invalid_number": "❌ 无效的数字。",
        "value_range": "❌ 值必须在1-100之间",
        "queue_title": "📥 下载队列\n\n活跃：{active}\n排队：{queued}",
        "active_downloads": "▶️ 活跃中：",
        "storage_title": "💾 存储状态{alert}\n\n📀 总磁盘：{total}\n📁 已用：{used} ({percent}%)\n💿 可用：{free}\n\n📂 临时（{temp_dir}）：\n• 文件数：{temp_files}\n• 大小：{temp_size}",
        "oldest": "📅 最旧：{filename}\n   {date}",
        "failed_title": "❌ 用户 {user_id} 的失败下载：",
        "no_failed": "✅ 用户 {user_id} 没有失败的下载。",
        "status_title": "📊 机器人状态\n\n🖥️ CPU：{cpu}%\n💾 内存：{memory}%\n\n📂 临时文件：{temp_files}\n📦 临时大小：{temp_size}\n\n⚡ 速率限制：{rate_limit}/小时（{rate_enabled}）\n🕐 清理间隔：{cleanup_interval}h\n⏰ 最大保留：{temp_max_age}h",
        "language_changed": "✅ 语言已更改为中文。",
        "select_language": "🌐 选择你的语言：",
        "cached_file_used": "📂 使用缓存文件（最近下载）",
    },
    "ja": {
        "welcome": """👋 ようこそ！あなたのID: `{user_id}`

📎 YouTube、TikTok、Instagram、Twitter、Bilibili、Spotifyなどのサポートされているプラットフォームのリンクを送信してください。

✨ ダウンロード可能：
• 🎬 動画（最大2GB）
• 🎵 音声（MP3）
• 🖼️ サムネイル

リンクを送信して始めましょう！""",
        "your_id": "🆔 あなたのTelegram ID: `{user_id}`\n👤 ユーザー名: @{username}\n📛 名前: {name}",
        "not_authorized": "🔒 このボットの使用は許可されていません。",
        "rate_limit_exceeded": "⏳ レート制限を超えました。後でもう一度お試しください。",
        "unsupported_url": "❌ サポートされていないURL。\n\n✅ 対応：YouTube、TikTok、Instagram、Twitter、Bilibili、Spotifyなど。",
        "what_download": "📥 何をダウンロードしますか？",
        "video": "🎬 動画",
        "audio": "🎵 音声 (MP3)",
        "thumbnail": "🖼️ サムネイル",
        "downloading": "⬇️ ダウンロード中...",
        "processing": "⚙️ 処理中...お待ちください。",
        "download_complete": "✅ ダウンロード完了！処理中...",
        "file_too_large": "📦 ファイルが大きすぎます（{size}）。最大2GBです。",
        "upload_failed": "📤 アップロード失敗：{error}",
        "download_failed": "❌ ダウンロード失敗：{error}",
        "no_video_found": "😕 このツイートに動画が見つかりません。\n\n試す：🖼️ サムネイルで画像を取得、または🎵 音声（MP3）",
        "no_thumbnail": "🖼️ サムネイルがありません。",
        "no_history": "📭 ダウンロード履歴がありません。",
        "history_title": "📜 あなたのダウンロード履歴：",
        "select_quality": "🎞️ 品質を選択：\n{title}...",
        "loading_quality": "⏳ 品質オプションを読み込み中...",
        "uploading": "📤 Telegramにアップロード中...",
        "fetching_thumbnail": "🖼️ サムネイルを取得中...",
        "session_expired": "⏰ セッションが期限切れです。再度リンクを送信してください。",
        "available_commands": """📋 利用可能なコマンド：

/start - ボットを開始
/help - ヘルプを表示
/history - ダウンロード履歴
/lang - 言語変更

リンクを送信してダウンロード！""",
        "admin_commands": """⚙️ 管理者コマンド

📋 一般：
/start - ボットを開始
/help - ヘルプを表示
/history - ダウンロード履歴

👥 ユーザー管理：
/allow <user_id> - ユーザーを許可
/block <user_id> - ユーザーをブロック
/users - ユーザー一覧
/broadcast <message> - ブロードキャスト
/userhistory <user_id> - ユーザー履歴

📊 監視：
/stats - 使用統計
/queue - ダウンロードキュー状態
/storage - ディスク使用量
/status - システム状態

⚡ レート制限：
/rateinfo - レート制限情報
/setrate <max> [on/off] - レート制限設定

🧹 メンテナンス：
/failed <user_id> - 失敗したダウンロード
/cleanup - 一時ファイル清理
/cookie - クッキーファイルをアップロード

🌐 言語：
/lang - 言語変更

✨ 機能：
• aria2: マルチ接続ダウンロード
• 自動Cookie更新""",
        "user_allowed": "✅ ユーザー {user_id} を許可しました。",
        "user_blocked": "🚫 ユーザー {user_id} をブロックしました。",
        "no_users": "👥 ユーザーがいません。",
        "allowed_users": "👥 許可されたユーザー：",
        "broadcast_sent": "📢 ブロードキャストを {success} 人に送信しました。失敗：{failed}",
        "no_users_to_show": "👥 表示するユーザーがいません。",
        "select_user_history": "👤 履歴を表示するユーザーを選択：",
        "no_history_for_user": "📭 ユーザー {user_id} の履歴がありません。",
        "rate_limit_status": "⚡ レート制限状態\n\n• ステータス：{status}\n• 1時間あたりの最大ダウンロード：{max}\n• トラッキング中のユーザー：{users}",
        "rate_limit_updated": "✅ レート制限を更新：{max}/時間、{status}",
        "temp_cleaned": "🧹 一時ファイルを清理しました。",
        "usage_allow": "📝 用法：/allow <user_id>",
        "usage_block": "📝 用法：/block <user_id>",
        "usage_broadcast": "📝 用法：/broadcast <message>",
        "usage_setrate": "📝 用法：/setrate <max> [on/off]",
        "usage_failed": "📝 用法：/failed <user_id>",
        "invalid_user_id": "❌ 無効なユーザーID。数字である必要があります。",
        "invalid_number": "❌ 無効な数字。",
        "value_range": "❌ 値は1-100の間である必要があります",
        "queue_title": "📥 ダウンロードキュー\n\nアクティブ：{active}\nキュー：{queued}",
        "active_downloads": "▶️ アクティブ：",
        "storage_title": "💾 ストレージ状態{alert}\n\n📀 総ディスク：{total}\n📁 使用：{used} ({percent}%)\n💿 空き：{free}\n\n📂 一時（{temp_dir}）：\n• ファイル数：{temp_files}\n• サイズ：{temp_size}",
        "oldest": "📅 最古：{filename}\n   {date}",
        "failed_title": "❌ ユーザー {user_id} の失敗したダウンロード：",
        "no_failed": "✅ ユーザー {user_id} に失敗したダウンロードはありません。",
        "status_title": "📊 ボット状態\n\n🖥️ CPU：{cpu}%\n💾 メモリ：{memory}%\n\n📂 一時ファイル：{temp_files}\n📦 一時サイズ：{temp_size}\n\n⚡ レート：{rate_limit}/時間（{rate_enabled}）\n🕐 清理間隔：{cleanup_interval}h\n⏰ 最大期間：{temp_max_age}h",
        "language_changed": "✅ 言語を日本語に変更しました。",
        "select_language": "🌐 言語を選択：",
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
