# Vee - Telegram メディアダウンボット

[English](./README.md) | [中文版](./README.zh.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

複数のプラットフォームから動画、音声、サムネイルをダウンロードできる強力なTelegramボットです。

## 機能

- **マルチプラットフォーム対応**: YouTube、TikTok、Instagram、Twitter/X、Bilibili、Spotifyなど
- **多様なダウンロード形式**: 動画（最大2GB）、音声（MP3）、サムネイル
- **高速ダウンロード**: aria2マルチ接続サポート
- **ユーザー管理**: 許可/ブロックシステム、レート制限
- **多言語**: 英語、中国語、日本語、韓国語対応
- **キャッシュ**: ファイルIDキャッシュで再アップロードを回避
- **Cookie管理**: 認証ダウンロード用の自動Cookie更新

## インストール

### 必要な環境

- Python 3.10+
- Telegram botトークン
- FFmpeg（音声変換用）
- aria2c（オプション、高速ダウンロード用）

### セットアップ

1. リポジトリをクローン:
```bash
git clone https://github.com/yourusername/vee.git
cd vee
```

2. 依存関係をインストール:
```bash
pip install -r requirements.txt
```

3. 環境変数を設定:
```bash
cp .env.example .env
# .envを編集して設定
```

4. ボットを起動:
```bash
python vee.py
```

## 設定

`.env`ファイルを編集:

| 変数 | 説明 |
|------|------|
| `TELEGRAM_TOKEN` | Telegram botトークン |
| `ADMIN_IDS` | 管理者ユーザーID（カンマ区切り） |
| `MAX_FILE_SIZE` | 最大ファイルサイズ（バイト） |
| `TEMP_DIR` | 一時ダウンロード用ディレクトリ |
| `USE_ARIA2` | aria2で高速ダウンロード |
| `COOKIE_REFRESH_CMD` | Cookie更新コマンド |

## コマンド

### ユーザーコマンド
- `/start` - ボットを起動
- `/help` - ヘルプを表示
- `/history` - ダウンロード履歴
- `/lang` - 言語を変更

### 管理者コマンド
- `/allow <user_id>` - ユーザーを許可
- `/block <user_id>` - ユーザーをブロック
- `/users` - 許可済みユーザー一覧
- `/stats` - ボット使用統計
- `/broadcast <message>` - メッセージ配信
- `/queue` - ダウンロードキュー状態
- `/storage` - ディスク使用量
- `/status` - ボットシステム状態
- `/setrate <max>` - レート制限設定

## アーキテクチャ

```
vee/
├── app/              # Telegram botハンドラー
│   ├── commands.py   # コマンドハンドラー
│   ├── callbacks.py  # コールバックハンドラー
│   └── download.py  # ダウンロードユーティリティ
├── core/             # コア機能
│   ├── downloader.py    # ダウンロードロジック
│   ├── strategies.py     # ダウンロード戦略
│   ├── services.py      # サービス抽象化
│   ├── file_handler.py  # ファイル操作
│   ├── history.py       # ダウンロード履歴
│   ├── ratelimit.py     # レート制限
│   └── i18n.py         # 国際化
├── config.py         # 設定
└── vee.py           # メインエントリポイント
```

## 設計パターン

- **ストラテジーパターン**: `DownloadStrategy`で柔軟なダウンロード
- **ファクトリーパターン**: `StrategyFactory`で戦略管理
- **サービスレイヤー**: 分離のための`DownloadService`抽象化
- **テンプレートメソッド**: 共通ワークフローのベース戦略

## 対応プラットフォーム

| プラットフォーム | 動画 | 音声 | サムネイル |
|----------|-------|-------|-----------|
| YouTube | ✅ | ✅ | ✅ |
| TikTok | ✅ | ✅ | ✅ |
| Instagram | ✅ | ✅ | ✅ |
| Twitter/X | ✅ | ✅ | ✅ |
| Bilibili | ✅ | ✅ | ✅ |
| Spotify | ✅ | ✅ | ❌ |

## ライセンス

MITライセンス

## リンク

- [GitHub リポジトリ](https://github.com/KaguyaTaketori/vee)
- [yt-dlp ドキュメント](https://github.com/yt-dlp/yt-dlp)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## コントリビューション

プルリクエスト大歓迎！
