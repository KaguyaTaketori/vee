# Vee - Telegram 媒体下载机器人

[English](./README.md) | [日本語](./README.ja.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

一个强大的Telegram机器人，支持从多个平台下载视频、音频和缩略图。

## 功能

- **多平台支持**: YouTube、TikTok、Instagram、Twitter/X、Bilibili、Spotify等
- **多种下载类型**: 视频（最大2GB）、音频（MP3）、缩略图
- **高速下载**: aria2多连接支持
- **用户管理**: 允许/阻止系统，速率限制
- **多语言**: 英语、中文、日语、韩语支持
- **缓存**: 自动缓存文件ID，避免重复上传
- **Cookie管理**: 认证下载的自动Cookie刷新

## 安装

### 环境要求

- Python 3.10+
- Telegram机器人令牌
- FFmpeg（用于音频转换）
- aria2c（可选，用于更快下载）

### 设置

1. 克隆仓库:
```bash
git clone https://github.com/yourusername/vee.git
cd vee
```

2. 安装依赖:
```bash
pip install -r requirements.txt
```

3. 配置环境:
```bash
cp .env.example .env
# 编辑 .env 文件进行设置
```

4. 运行机器人:
```bash
python vee.py
```

## 配置

编辑 `.env` 文件:

| 变量 | 描述 |
|----------|-------------|
| `TELEGRAM_TOKEN` | 你的Telegram机器人令牌 |
| `ADMIN_IDS` | 管理员用户ID（逗号分隔） |
| `MAX_FILE_SIZE` | 最大文件大小（字节） |
| `TEMP_DIR` | 临时下载目录 |
| `USE_ARIA2` | 启用aria2加快下载 |
| `COOKIE_REFRESH_CMD` | 刷新Cookie的命令 |

## 命令

### 用户命令
- `/start` - 启动机器人
- `/help` - 显示帮助
- `/history` - 下载历史
- `/lang` - 更改语言

### 管理员命令
- `/allow <user_id>` - 允许用户
- `/block <user_id>` - 阻止用户
- `/users` - 列出允许的用户
- `/stats` - 机器人使用统计
- `/broadcast <message>` - 广播消息
- `/queue` - 下载队列状态
- `/storage` - 磁盘使用情况
- `/status` - 机器人系统状态
- `/setrate <max>` - 设置速率限制
- `/failed` - 查看失败的下载
- `/clear` - 清除缓存或历史

## 架构

```
vee/
├── app/              # Telegram机器人处理器
│   ├── commands.py   # 命令处理器
│   ├── callbacks.py  # 回调处理器
│   └── download.py  # 下载工具
├── core/             # 核心功能
│   ├── downloader.py    # 下载逻辑 (yt-dlp)
│   ├── strategies.py     # 下载策略模式
│   ├── facades.py       # 服务外观
│   ├── history.py       # 下载历史 (SQLite)
│   ├── users.py         # 用户管理
│   ├── ratelimit.py     # 速率限制
│   ├── logger.py        # 日志系统
│   └── i18n.py         # 国际化
├── locales/          # 翻译文件
├── config.py         # 配置
└── vee.py           # 主入口
```

## 设计模式

- **策略模式**: 使用`DownloadStrategy`实现灵活的下载策略
- **工厂模式**: `StrategyFactory`管理策略
- **服务层**: `DownloadService`抽象实现解耦
- **模板方法**: 基础策略实现通用工作流

## 支持的平台

| 平台 | 视频 | 音频 | 缩略图 |
|----------|-------|-------|-----------|
| YouTube | ✅ | ✅ | ✅ |
| TikTok | ✅ | ✅ | ✅ |
| Instagram | ✅ | ✅ | ✅ |
| Twitter/X | ✅ | ✅ | ✅ |
| Bilibili | ✅ | ✅ | ✅ |
| Spotify | ✅ | ✅ | ❌ |

## 许可证

MIT 许可证

## 链接

- [GitHub 仓库](https://github.com/KaguyaTaketori/vee)
- [yt-dlp 文档](https://github.com/yt-dlp/yt-dlp)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## 贡献

欢迎提交Pull Request！
