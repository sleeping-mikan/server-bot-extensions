# server-bot-extensions-pack

[server-bot-v3](https://github.com/sleeping-mikan/server-bot-v3) 用の拡張機能実装集です。
拡張機能の仕様は公式リポジトリ [server-bot-extensions](https://github.com/sleeping-mikan/server-bot-extensions) を参照してください。

## 構成

```
plan.json                                    ← 実装計画(採否の判断根拠・API概要)
mikanassets/
  extension/
    auto-announce/commands.py                ← 定期アナウンス(季節限定メッセージ対応)
    whitelist-ops-viewer/commands.py         ← ホワイトリスト/OP/BAN一覧表示
    restart-warning-timer/commands.py        ← 再起動予告タイマー
    disk-space-watchdog/commands.py          ← ディスク容量監視
    backup-watch/commands.py                 ← バックアップ未実施リマインド + 自動整理
    downtime-notifier/commands.py            ← 予期しないダウン通知
    idle-hours-notice/commands.py            ← 静かな時間帯の告知
```

`mikanassets/` 以下は server-bot-v3 が実際に読み込むディレクトリ構成と同じです。
使いたい拡張機能のフォルダ (`mikanassets/extension/<拡張機能名>/`) を、
server-bot-v3 を配置しているディレクトリの `mikanassets/extension/` にそのままコピーしてください。
(各拡張機能は初回のコマンド実行時に、自分のフォルダ内へ `state.json` を自動生成して設定を保存します)

## 採否の経緯

最初に `quick-commands`(weather/time/gamemode等の選択式スラッシュコマンド化)を実装しましたが、
コアBotの `/cmd serverin`(config の許可リストに名前を足すだけで同じことができる)と
ターミナルチャンネルの生パススルーで完全に代替可能と指摘を受け、却下しました。

この反省を踏まえ、以下の観点で拡張機能一覧を選定し直しています。

1. コアBotの既存コマンドで代替できないか(できるなら拡張機能として作る意味が無い)
2. `append_task` による定期実行など、コアBotに無い自動化を提供しているか
3. 管理者1名・身内数人規模の個人サーバー運用で、実際に使われそうか

判断の詳細(KEEP/CUT/MERGEの理由)は [plan.json](plan.json) の各エントリの `notes` を参照してください。
`custom-whitelist-request` / `uptime` / `dice-omikuji` / `safe-console-whitelist` / `cross-server-relay` は
上記基準でCUTしています。`tps-lag-report` はサーバー標準出力を読むフックが今の拡張APIに無いため実装不可(blocked)です。

## 実装済み拡張機能

### auto-announce

設定した間隔で、定型メッセージをサーバー内(コンソールの `say`)と Discord チャンネルの
両方(または片方)に自動送信します。各メッセージに `date` (MM-DD) を指定すると、その日だけ
送信される季節限定メッセージにもなります(元々別案だった季節告知はここへ統合しました)。

- `/extension-auto-announce add <message> [date]`
- `/extension-auto-announce remove <index>`
- `/extension-auto-announce list`
- `/extension-auto-announce config [interval_minutes] [to_server] [to_discord] [discord_channel]`

### whitelist-ops-viewer

`whitelist.json` / `ops.json` / `banned-players.json` を読み取り整形して表示します(読み取り専用)。

- `/extension-whitelist-ops-viewer whitelist`
- `/extension-whitelist-ops-viewer ops`
- `/extension-whitelist-ops-viewer bans`

### restart-warning-timer

指定分数後を目標に、10分前/5分前/1分前/0分の段階でサーバー内へ警告broadcastします。
実際の停止/再起動操作はコアBotの `/restart` 等を別途実行してください。

- `/extension-restart-warning-timer schedule <minutes>`
- `/extension-restart-warning-timer cancel`

### disk-space-watchdog

サーバー設置先ドライブの空き容量を定期チェックし、閾値を下回ったらDiscordへ警告します。

- `/extension-disk-space-watchdog status`
- `/extension-disk-space-watchdog config [threshold_gb] [interval_minutes] [channel]`

### backup-watch

`ctx.backup_path` のファイル名からタイムスタンプを直接パースするため手動記録は不要です。
最終バックアップからの経過時間の通知と、保持期間を超えた古いバックアップの自動削除の両方を提供します。

- `/extension-backup-watch last`
- `/extension-backup-watch prune-now`(要上位権限)
- `/extension-backup-watch config [remind_after_hours] [retention_days] [auto_prune] [channel]`

### downtime-notifier

サーバープロセスが停止した状態のまま猶予時間を超えて続いている場合にDiscordへ通知します。
「クラッシュ検知」ではなく中立的な「ダウン通知」です。理由は
[plan.json](plan.json) の `downtime-notifier.notes` と `commands.py` 冒頭のコメントを参照してください
(`ctx.use_stop` は意図的な停止とクラッシュを外部ポーリングで区別する用途には使えないため)。

- `/extension-downtime-notifier config [grace_seconds] [channel]`

### idle-hours-notice

設定した時間帯の開始時刻に1日1回、監視が手薄になる旨をDiscordへ通知します。

- `/extension-idle-hours-notice config [start_hour] [channel]`
