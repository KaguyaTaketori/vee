# Vee - Telegram Media Downloader Bot

[中文版](./README.zh.md) | [日本語](./README.ja.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A powerful Telegram bot for downloading videos, audio, and thumbnails from multiple platforms.

## Features

- **Multi-platform Support**: YouTube, TikTok, Instagram, Twitter/X, Bilibili, Spotify, and more
- **Multiple Download Types**: Videos (up to 2GB), Audio (MP3), Thumbnails
- **High-Speed Downloads**: aria2 multi-connection support for faster downloads
- **User Management**: Allow/block system with rate limiting
- **Download History**: SQLite-based history with recent downloads
- **Multi-language**: English, Chinese, Japanese, Korean support
- **Caching**: Automatic file ID caching to avoid re-uploading
- **Cookie Management**: Auto-refresh cookies for authenticated downloads

## Installation

### Prerequisites

- Python 3.10+
- Telegram Bot Token
- FFmpeg (for audio conversion)
- aria2c (optional, for faster downloads)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/vee.git
cd vee
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment:
```bash
cp .env.example .env
# Edit .env with your settings
```

4. Run the bot:
```bash
python vee.py
```

## Configuration

Edit `.env` file:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_TOKEN` | Your Telegram bot token |
| `ADMIN_IDS` | Admin user IDs (comma-separated) |
| `MAX_FILE_SIZE` | Maximum file size in bytes |
| `TEMP_DIR` | Directory for temporary downloads |
| `USE_ARIA2` | Enable aria2 for faster downloads |
| `COOKIE_REFRESH_CMD` | Command to refresh cookies |

## Commands

### User Commands
- `/start` - Start the bot
- `/help` - Show help
- `/history` - View download history
- `/lang` - Change language

### Admin Commands
- `/allow <user_id>` - Allow a user
- `/block <user_id>` - Block a user
- `/users` - List allowed users
- `/stats` - Bot usage statistics
- `/broadcast <message>` - Broadcast message
- `/queue` - Download queue status
- `/storage` - Disk usage
- `/status` - Bot system status
- `/setrate <max>` - Set rate limit
- `/failed` - View failed downloads
- `/clear` - Clear cache or history

## Architecture

```
vee/
├── app/              # Telegram bot handlers
│   ├── commands.py   # Command handlers
│   ├── callbacks.py  # Callback handlers
│   └── download.py  # Download utilities
├── core/             # Core functionality
│   ├── downloader.py    # Download logic with yt-dlp
│   ├── strategies.py     # Download strategy pattern
│   ├── facades.py       # Service facades
│   ├── history.py       # Download history (SQLite)
│   ├── users.py         # User management
│   ├── ratelimit.py     # Rate limiting
│   ├── logger.py        # Logging system
│   └── i18n.py         # Internationalization
├── locales/          # Translation files
├── config.py         # Configuration
└── vee.py           # Main entry point
```

## Design Patterns

- **Strategy Pattern**: Flexible download strategies with `DownloadStrategy`
- **Factory Pattern**: `StrategyFactory` for strategy management
- **Service Layer**: `DownloadService` abstraction for decoupling
- **Template Method**: Base strategy with common workflow

## Supported Platforms

| Platform | Video | Audio | Thumbnail |
|----------|-------|-------|-----------|
| YouTube | ✅ | ✅ | ✅ |
| TikTok | ✅ | ✅ | ✅ |
| Instagram | ✅ | ✅ | ✅ |
| Twitter/X | ✅ | ✅ | ✅ |
| Bilibili | ✅ | ✅ | ✅ |
| Spotify | ✅ | ✅ | ❌ |

## License

MIT License

## Links

- [GitHub Repository](https://github.com/KaguyaTaketori/vee)
- [yt-dlp Documentation](https://github.com/yt-dlp/yt-dlp)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## Contributing

Pull requests are welcome!
