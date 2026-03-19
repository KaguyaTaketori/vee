# Vee - Telegram botプラットフォーム

[English](./README.md) | [中文版](./README.zh.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

強力なTelegram botプラットフォームで、モジュラープラグインアーキテクチャを採用しています。現在、メディアダウンロードと請求管理の機能を備えています。

## 機能

### メディアダウンモジュール
- **マルチプラットフォーム対応**: YouTube、TikTok、Instagram、Twitter/X、Bilibili、Spotifyなど
- **多様なダウンロード形式**: 動画（最大2GB）、音声（MP3）、サムネイル、字幕
- **高速ダウンロード**: aria2マルチ接続サポート
- **ダウンロード履歴**: SQLiteベースの履歴、ファイルIDキャッシュで再アップロードを回避
- **Cookie管理**: 認証ダウンロード用の自動Cookie更新

### 請求モジュール
- **請求書の解析**: さまざまなソースからの請求書を解析・管理
- **ユーザーの請求履歴**: 過去の請求記録の追跡と表示

### コア機能
- **ユーザー管理**: 許可/ブロックシステム、レート制限
- **多言語**: 英語、中国語、日本語、韓国語対応
- **プラグインアーキテクチャ**: 機能拡張が容易なモジュラーデザイン
- **タスクキュー**: 并発制御付きの非同期タスクキュー

## インストール

### 必要な環境

- Python 3.10+
- Telegram botトークン
- FFmpeg（音声変換用）
- aria2c（オプション、高速ダウンロード用）

### セットアップ

1. リポジトリをクローン:
```bash
git clone https://github.com/KaguyaTaketori/vee.git
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
- `/mybills` - 請求書を表示
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
- `/failed` - 失敗したダウンロードを表示
- `/clear` - キャッシュまたは履歴をクリア

## アーキテクチャ

```
vee/
├── modules/                  # プラグインモジュール
│   ├── downloader/          # メディアダウンロード
│   │   ├── strategies/       # ダウンロード戦略（動画、音声、サムネイル、字幕、Spotify）
│   │   ├── handlers/         # メッセージ・コールバックハンドラー
│   │   ├── services/         # Facadeとサービス
│   │   └── integrations/     # 外部連携（yt-dlp、aria2、spotify）
│   └── billing/             # 請求管理
│       ├── handlers/         # 請求書ハンドラーとコールバック
│       ├── services/         # 請求書解析とキャッシュ
│       └── database/         # 請求書ストレージ
├── core/                     # コアボット機能
│   ├── callback_bus.py       # イベントコールバックバス
│   ├── handler_registry.py   # ハンドラー登録
│   ├── bot_setup.py          # ボット初期化
│   ├── filters.py            # 更新フィルター
│   └── jobs.py               # 定期ジョブ
├── database/                 # データベースレイヤー
├── shared/                   # 共有ユーティリティとリポジトリ
├── models/                   # ドメインモデル
├── config.py                 # 設定
└── vee.py                    # メインエントリポイント
```

## 設計パターン

- **ストラテジーパターン**: モジュール型ダウンロード戦略（`VideoStrategy`、`AudioStrategy`、`ThumbnailStrategy`など）
- **ファクトリーパターン**: `StrategyFactory`で動的に戦略を選択
- **テンプレートメソッド**: `TaskStrategy`基底クラスで共通のダウンロード/アップロードワークフロー
- **ファサードパターン**: `DownloadFacade`と請求FacadeでクリーンなAPIを提供
- **プラグインアーキテクチャ**: 拡張可能な独立モジュール

## 対応プラットフォーム（メディアダウンロード）

| プラットフォーム | 動画 | 音声 | サムネイル | 字幕 |
|----------|-------|-------|-----------|----------|
| YouTube | ✅ | ✅ | ✅ | ✅ |
| TikTok | ✅ | ✅ | ✅ | ❌ |
| Instagram | ✅ | ✅ | ✅ | ❌ |
| Twitter/X | ✅ | ✅ | ✅ | ❌ |
| Bilibili | ✅ | ✅ | ✅ | ✅ |
| Spotify | ✅ | ✅ | ❌ | ❌ |

## ライセンス

MITライセンス

## リンク

- [GitHub リポジトリ](https://github.com/KaguyaTaketori/vee)
- [yt-dlp ドキュメント](https://github.com/yt-dlp/yt-dlp)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## コントリビューション

プルリクエスト大歓迎！
