# Vee - Telegram Media Downloader Bot

[中文版](./README.zh.md) | [日本語](./README.ja.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A powerful Telegram bot for downloading videos, audio, thumbnails, and subtitles from multiple platforms.

## Features

- **Multi-platform Support**: YouTube, TikTok, Instagram, Twitter/X, Bilibili, Spotify, and more
- **Multiple Download Types**: Videos (up to 2GB), Audio (MP3), Thumbnails, Subtitles
- **High-Speed Downloads**: aria2 multi-connection support for faster downloads
- **User Management**: Allow/block system with rate limiting
- **Download History**: SQLite-based history with file ID caching to avoid re-uploads
- **Multi-language**: English, Chinese, Japanese, Korean support
- **Cookie Management**: Auto-refresh cookies for authenticated downloads
- **Template Method Pattern**: Flexible base strategy with common download/upload workflow

## Installation

### Prerequisites

- Python 3.10+
- Telegram Bot Token
- FFmpeg (for audio conversion)
- aria2c (optional, for faster downloads)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/KaguyaTaketori/vee.git
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
├── modules/
│   ├── downloader/           # Download module
│   │   ├── strategies/      # Download strategies (video, audio, thumbnail, subtitle, spotify)
│   │   ├── handlers/         # Message & callback handlers
│   │   ├── services/        # Facades and domain services
│   │   └── integrations/    # External integrations (yt-dlp, aria2, spotify)
│   └── billing/             # Billing module
├── core/                    # Core bot functionality
│   ├── callback_bus.py       # Event callback bus
│   ├── handler_registry.py   # Handler registration
│   ├── bot_setup.py          # Bot initialization
│   ├── filters.py            # Update filters
│   └── ...
├── database/                # Database layer
├── shared/                  # Shared utilities & repositories
├── models/                  # Domain models
├── config.py                # Configuration
└── vee.py                   # Main entry point
```

## Design Patterns

- **Strategy Pattern**: Modular download strategies (`VideoStrategy`, `AudioStrategy`, `ThumbnailStrategy`, etc.)
- **Factory Pattern**: `StrategyFactory` for dynamic strategy selection
- **Template Method**: `TaskStrategy` base class with common download/upload workflow
- **Facade Pattern**: `DownloadFacade` as simple API to task queue

## Supported Platforms

| Platform | Video | Audio | Thumbnail | Subtitle |
|----------|-------|-------|-----------|----------|
| YouTube | ✅ | ✅ | ✅ | ✅ |
| TikTok | ✅ | ✅ | ✅ | ❌ |
| Instagram | ✅ | ✅ | ✅ | ❌ |
| Twitter/X | ✅ | ✅ | ✅ | ❌ |
| Bilibili | ✅ | ✅ | ✅ | ✅ |
| Spotify | ✅ | ✅ | ❌ | ❌ |

## License

MIT License

## Links

- [GitHub Repository](https://github.com/KaguyaTaketori/vee)
- [yt-dlp Documentation](https://github.com/yt-dlp/yt-dlp)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## Contributing

Pull requests are welcome!
