# Vee - Telegram Bot Platform

[中文版](./README.zh.md) | [日本語](./README.ja.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A powerful Telegram bot platform with modular plugin architecture. Currently includes media downloading and billing management.

## Features

### Media Downloader Module
- **Multi-platform Support**: YouTube, TikTok, Instagram, Twitter/X, Bilibili, Spotify, and more
- **Multiple Download Types**: Videos (up to 2GB), Audio (MP3), Thumbnails, Subtitles
- **High-Speed Downloads**: aria2 multi-connection support for faster downloads
- **Download History**: SQLite-based history with file ID caching to avoid re-uploads
- **Cookie Management**: Auto-refresh cookies for authenticated downloads

### Billing Module
- **Bill Parsing**: Parse and manage billing information from various sources
- **User Bill History**: Track and view past billing records

### Core Features
- **User Management**: Allow/block system with rate limiting
- **Multi-language**: English, Chinese, Japanese, Korean support
- **Plugin Architecture**: Modular design for easy feature extension
- **Task Queue**: Async task queue with concurrency control

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
- `/mybills` - View your billing records
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
├── modules/                  # Plugin modules
│   ├── downloader/          # Media downloader
│   │   ├── strategies/      # Download strategies (video, audio, thumbnail, subtitle, spotify)
│   │   ├── handlers/        # Message & callback handlers
│   │   ├── services/        # Facades and domain services
│   │   └── integrations/   # External integrations (yt-dlp, aria2, spotify)
│   └── billing/             # Billing management
│       ├── handlers/         # Bill handlers & callbacks
│       ├── services/         # Bill parsing & caching
│       └── database/         # Bill storage
├── core/                    # Core bot functionality
│   ├── callback_bus.py       # Event callback bus
│   ├── handler_registry.py   # Handler registration
│   ├── bot_setup.py          # Bot initialization
│   ├── filters.py            # Update filters
│   └── jobs.py               # Scheduled jobs
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
- **Facade Pattern**: `DownloadFacade` and billing facades for clean APIs
- **Plugin Architecture**: Independent modules that can be extended

## Supported Platforms (Media Downloader)

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
