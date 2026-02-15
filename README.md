# MTB Daily Morning Playlist Updater

これは macOS の `launchd` と Amazon Echo（Alexa）を組み合わせて、
"朝向けの静かなマイナー寄り洋楽ポップ" を毎朝自動で流すための
Spotify プレイリスト更新スクリプトです。

- Python スクリプトは **プレイリストの中身を更新** するのみ。
- 再生/デバイス操作は Alexa のルーチンに任せます。
- Spotify アプリを常時起動する必要はありません。

## 📦 依存関係

```sh
pip install spotipy python-dotenv requests
```

## ⚙ 環境設定

1. [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) にログイン
2. 新しいアプリを作成し、以下を控える:
   - Client ID
   - Client Secret
3. **Redirect URI** を登録 (例: `http://localhost:8888/callback`)
4. このリポジトリをクローンし、以下ファイルを作成:

```sh
cp .env.example .env
# 編集して CLIENT_ID, CLIENT_SECRET, REDIRECT_URI を設定
```

5. 必要に応じてプレイリスト名等のデフォルトを `.env` に書けます。
   - `PLAYLIST_NAME` — デフォルト `MTB Daily Morning`
   - `HISTORY_FILE`, `DAYS_HISTORY`, `MIN_TRACKS`, `MAX_TRACKS` など

## 🛠 初回認可

1. ターミナルで次を実行:
   ```sh
   python3 morning_playlist_update.py
   ```
2. ブラウザが開いて Spotify へのアクセス許可を求められます。
3. 承認後、トークンが `~/.cache` に保存され、以降はリフレッシュトークンを
   自動的に使って無人実行できます。

> **メモ**: 必ず手動実行で成功させてください。スクリプトが最初のトークンを
> 取得するまでブラウザ認可が必要です。

## 🎯 スクリプトの動作

- ジャンル `indie pop`、`chill pop`、`dream pop` の曲を検索
- 人気度が低め、テンポ/エネルギーも控えめな曲を優先
- Spotify Dev Mode で `audio-features` が 403 の場合は、人気度フィルタのみで継続（処理は停止しません）
- 直近 **DAYS_HISTORY** 日に登場した曲は除外
- 1 日あたり **MIN_TRACKS–MAX_TRACKS** 曲をランダム選出
- 同一アーティスト連続を避ける簡易ルール
- プレイリストを上書き更新し、履歴を `history.json` に保存


## ✅ 動作確認手順（Dev Mode対応後）

トークンキャッシュを一度削除して再認可し、更新処理が通ることを確認します。

```sh
rm -f .cache
python3 morning_playlist_update.py
```

- 初回はブラウザ認可が開きます。
- 実行後に `morning_playlist_update.log` にエラーがなく、対象プレイリストが更新されていればOKです。

## ⏰ macOS での自動実行 (`launchd`)

以下の plist サンプルを `~/Library/LaunchAgents/` に保存します。
例えば `com.example.morning_playlist_update.plist`。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.example.morning_playlist_update</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>python3</string>
        <string>/path/to/Spotify-music-list/morning_playlist_update.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>50</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/morning_playlist_update.out</string>
    <key>StandardErrorPath</key>
    <string>/tmp/morning_playlist_update.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <!-- 環境変数を明示的に設定する場合
        <key>CLIENT_ID</key><string>...</string>
        <key>CLIENT_SECRET</key><string>...</string>
        <key>REDIRECT_URI</key><string>...</string>
        -->
    </dict>
</dict>
</plist>
```

- plist を保存したら:
  ```sh
  launchctl load ~/Library/LaunchAgents/com.example.morning_playlist_update.plist
  ```
- 実行を停止するには:
  ```sh
  launchctl unload ~/Library/LaunchAgents/com.example.morning_playlist_update.plist
  ```
- ログは `/tmp/morning_playlist_update.out`/`.err` や
  リポジトリ内の `morning_playlist_update.log` を参照。

## 📡 Alexa ルーチン設定

1. Alexa アプリを開き、**ルーチン**を作成。
2. トリガーは「毎日 7:00」など好きな時刻。
3. アクションで「音楽を再生」→サービスに **Spotify** を選択。
   - プレイリスト名を `MTB Daily Morning`（または上記で指定した名前）
4. 必要ならシャッフルを ON、音量調整もルーチンに追加。
5. Echo Studio (2025) をデフォルトデバイスにしておく。

> Spotify アプリを起動しておく必要はありません。

## 💡 注意事項

- Spotify API のレート制限があるため、429 応答では自動的にリトライします。
- エラー時は非ゼロ終了コードを返し、ログに原因を残します。
- スクリプトを編集する場合は `.env` で設定を管理してください。

---

このセットアップによって、毎朝 Amazon Echo から静かなマイナー洋楽が流れるはずです。
お楽しみください！