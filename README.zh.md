# Vee - Telegram 媒体下载机器人

[English](./README.md) | [日本語](./README.ja.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

一个强大的Telegram机器人，支持从多个平台下载视频、音频、缩略图和字幕。

## 功能

- **多平台支持**: YouTube、TikTok、Instagram、Twitter/X、Bilibili、Spotify等
- **多种下载类型**: 视频（最大2GB）、音频（MP3）、缩略图、字幕
- **高速下载**: aria2多连接支持
- **用户管理**: 允许/阻止系统，速率限制
- **下载历史**: SQLite数据库，支持文件ID缓存避免重复上传
- **多语言**: 英语、中文、日语、韩语支持
- **Cookie管理**: 认证下载的自动Cookie刷新
- **模板方法模式**: 灵活的基础策略实现通用下载/上传流程

## 安装

### 环境要求

- Python 3.10+
- Telegram机器人令牌
- FFmpeg（用于音频转换）
- aria2c（可选，用于更快下载）

### 设置

1. 克隆仓库:
```bash
git clone https://github.com/KaguyaTaketori/vee.git
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
├── modules/
│   ├── downloader/           # 下载模块
│   │   ├── strategies/        # 下载策略（视频、音频、缩略图、字幕、Spotify）
│   │   ├── handlers/         # 消息和回调处理器
│   │   ├── services/         # Facade和服务
│   │   └── integrations/    # 外部集成（yt-dlp、aria2、spotify）
│   └── billing/              # 计费模块
├── core/                     # 核心机器人功能
│   ├── callback_bus.py       # 事件回调总线
│   ├── handler_registry.py   # 处理器注册
│   ├── bot_setup.py          # 机器人初始化
│   ├── filters.py            # 更新过滤器
│   └── ...
├── database/                 # 数据库层
├── shared/                   # 共享工具和仓库
├── models/                   # 领域模型
├── config.py                 # 配置
└── vee.py                    # 主入口
```

## 设计模式

- **策略模式**: 模块化下载策略（`VideoStrategy`、`AudioStrategy`、`ThumbnailStrategy`等）
- **工厂模式**: `StrategyFactory`动态选择策略
- **模板方法**: `TaskStrategy`基类实现通用下载/上传流程
- **外观模式**: `DownloadFacade`提供简单的任务队列API

## 支持的平台

| 平台 | 视频 | 音频 | 缩略图 | 字幕 |
|----------|-------|-------|-----------|----------|
| YouTube | ✅ | ✅ | ✅ | ✅ |
| TikTok | ✅ | ✅ | ✅ | ❌ |
| Instagram | ✅ | ✅ | ✅ | ❌ |
| Twitter/X | ✅ | ✅ | ✅ | ❌ |
| Bilibili | ✅ | ✅ | ✅ | ✅ |
| Spotify | ✅ | ✅ | ❌ | ❌ |

## 许可证

MIT 许可证

## 链接

- [GitHub 仓库](https://github.com/KaguyaTaketori/vee)
- [yt-dlp 文档](https://github.com/yt-dlp/yt-dlp)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## 贡献

欢迎提交Pull Request！
