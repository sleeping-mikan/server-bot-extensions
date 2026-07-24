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
    rcon/commands.py                         ← RCON経由コマンド実行(確実な結果取得 + エイリアス多数)
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
上記基準でCUTしています。`idle-hours-notice` は一度実装しましたが、オーナー本人から
「意味が分からない」というフィードバックを受け撤去しました。`tps-lag-report` は本来サーバー標準出力を
読むフックが無いため実装不可(blocked)でしたが、後述の `rcon` 拡張の追加でこの制約自体が
実質的に解消されています。

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

### rcon

Minecraft の RCON プロトコルで直接コマンドを送り、送ったコマンドに対する**サーバーの応答を
そのまま確実に**受け取ります。標準ライブラリ(asyncio/struct)のみで実装しており、追加の
pipインストールは不要です。

**なぜ必要か**: コアBotの `/cmd serverin` は実は結果を正確に取得できていません
(`bot/commands/cmd.py` が `ctx.is_back_discord=True` にしてから最大3秒
`ctx.cmd_logs` をポーリングし、コマンド送信後に**次に来たstdoutの1行を無条件にその結果として
扱っている**だけ)。拡張機能側の `write_server_in()` に至っては応答を一切拾わない
fire-and-forgetです。RCONはコマンドと応答が同一TCP往復で1対1対応する専用プロトコルなので、
この「たまたま次に出た行」問題が原理的に起きません(フェイクRCONサーバーを使って
認証成功/失敗・空応答・エラー応答の4パターンで動作検証済みです)。

**前提**: `server.properties` で以下を設定し、サーバーを再起動しておく必要があります。

```
enable-rcon=true
rcon.port=25575
rcon.password=<空でない値>
```

パスワードはDiscordに入力させず、`server.properties` から拡張機能が直接読み取ります。
`/extension-rcon check` で設定状況と疎通を確認できます。

- `/extension-rcon check` — RCON設定状況と疎通確認
- `/extension-rcon cmd <command>` — 任意コマンドをそのまま実行(要上位権限)
- `/extension-rcon list` — オンラインプレイヤー一覧
- `/extension-rcon gamemode <mode> [selector=@a]`
- `/extension-rcon weather <clear|rain|thunder> [seconds]`
- `/extension-rcon time <set|add> <value>`
- `/extension-rcon difficulty <peaceful|easy|normal|hard>`
- `/extension-rcon say <message>`
- `/extension-rcon tp <selector> <x> <y> <z>`
- `/extension-rcon give <selector> <item> [count]`
- `/extension-rcon kill <selector>`(要上位権限)
- `/extension-rcon xp <selector> <amount> [unit=points|levels]`
- `/extension-rcon summon <entity> [x] [y] [z]`
- `/extension-rcon setblock <x> <y> <z> <block>`
- `/extension-rcon title <selector> <text>`
- `/extension-rcon effect give|clear`
- `/extension-rcon whitelist add|remove|list`
- `/extension-rcon player ban|pardon|kick|op|deop`(要上位権限)
- `/extension-rcon execute run <chain>` — execute の続き(as/at/if等)をそのまま実行
- `/extension-rcon execute as|at|if-entity <selector> <command>` — よく使う execute パターンのショートカット
- `/extension-rcon execute custom` — as/at/positioned/rotated/facing/anchored/align/in/if_entity/
  unless_entity/if_block/if_score を個別の型付き引数として指定し、`as → at → positioned → rotated →
  facing → anchored → align → in → if/unless → run` の順で自動的に組み立てて実行する

`execute` の**機能**自体は `run`(任意のチェインをそのままRCONへ渡すだけ)の時点で100%再現できています
— コンソールで打てることは全部打てます。再現できていないのはそこではなく、「chain全体を
1個のDiscordスラッシュコマンドの個別の型付き引数として表現しきる」方です。as/at/positioned/…/if/unless
は任意個・任意順に何度でも連結できるため、固定スキーマ(引数は最大25個、可変長の繰り返し構造は不可)の
スラッシュコマンドでは理論上どうやっても全パターンを型付き引数だけでは表現しきれません。
そこで `custom` で「各修飾子1回ずつ」というよく使う範囲を型付き引数として構造化し、それを超える
(同じ条件を複数回重ねる等の)ケースは `run` にフォールバックする、という2段構えにしています。
