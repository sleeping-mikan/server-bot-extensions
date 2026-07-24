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
- `/extension-rcon config <timeout_seconds>` — RCON接続/応答のタイムアウト秒数を設定(要上位権限)
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

`execute` チェイン専用のエイリアスは用意していません。`cmd` がRCONへの生コマンド送信そのものなので、
`cmd command:"execute as @a at @s run say hi"` のように execute チェインをそのまま渡せば済みます。
専用の execute グループ(run/as/at/if-entity/custom)は一度実装しましたが、`cmd` と機能が完全に
重複するだけの薄いラッパーだったため撤去しました。経緯は [plan.json](plan.json) の `rcon` エントリの
`notes` を参照してください。

#### rcon の権限レベルは .config で設定する

コアBotはコマンドごとの要求権限レベルを `.config` の
`discord_commands.permission.commands_level`(例: `"stop": 1`, `"backup apply": 3`,
`"permission change": 4`)に一元管理しており、`ctx.text.command_permission` はこの
辞書をそのまま指しています(`main.py`: `ctx.text.command_permission =
config["discord_commands"]["permission"]["commands_level"]`)。

`rcon` 拡張は最初 `state.json` 側に独自の権限設定を持たせていましたが、それだと
このBotの他の全コマンドとは別の場所・別の仕組みで権限を管理することになり不整合でした。
そのため `rcon` の各コマンドも `"rcon <サブコマンド名>"` というキーで**同じ
`commands_level` 辞書**を直接参照する設計に変更しています。`.config` にキーが
存在すればその値を、無ければ以下のデフォルト値を使います:

| デフォルトレベル | 対象コマンド(`.config`でのキー例) |
|---|---|
| 0(誰でも) | `rcon check`, `rcon list`, `rcon whitelist list` |
| 1 | `rcon gamemode`, `rcon weather`, `rcon time`, `rcon difficulty`, `rcon say`, `rcon tp`, `rcon give`, `rcon xp`, `rcon summon`, `rcon setblock`, `rcon title`, `rcon effect give`, `rcon effect clear`, `rcon whitelist add`, `rcon whitelist remove` |
| 2 | `rcon config`, `rcon cmd`(任意コマンド)、`rcon kill`、`rcon player ban/pardon/kick/op/deop` |

権限レベルを変更したい場合は、Discordコマンドではなく `.config` を直接編集してください。例:

```json
"discord_commands": {
  "permission": {
    "commands_level": {
      "rcon cmd": 3,
      "rcon gamemode": 0
    }
  }
}
```

`rcon cmd` は `allow_cmd` のような許可リストを一切通さない無条件の生コマンド実行なので、
コアBotの `/cmd serverin`(既定 level 1)より一段高いデフォルト level 2 にしています。

**キーは自動的に追加されます。** `.config` に `"rcon cmd"` のようなキーがまだ存在しない場合、
拡張ロード時にデフォルト値で登録し、そのまま**その場で** `.config` ファイルへ書き戻します
(コアBotが `core/config_loader.py` の起動時処理で自身のコマンドの権限キーを補完しているのと
同じ考え方です)。拡張のロードは Discord のイベントループが始まる前の同期的な起動フェーズで
行われるため、コマンド実行を待つ必要がなく即座に反映できます。既に手動で値を設定してある
キーは上書きされません。つまり、Botを一度起動するだけで `.config` を開けば
`"rcon cmd": 2` のように全キーが既に並んだ状態になっており、変更したい値だけ書き換えれば
済みます。また `ctx.text.command_permission` はコアBotの `/permission view <user>
detail:true` が一覧表示する辞書そのものなので、rconのキーもそこに一緒に表示されます。
