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
    scheduled-backup/commands.py             ← 定期的な停止→バックアップ→起動サイクル
    downtime-notifier/commands.py            ← 予期しないダウン通知
    rcon/commands.py                         ← RCON経由コマンド実行(確実な結果取得 + エイリアス多数)
    world-visualizer/                        ← ワールド全体マップ画像 + プレイヤー所持品画像(複数ファイル構成)
    update-watch/commands.py                 ← バージョン更新監視 + 半自動アップデート
    chat-bridge/commands.py                  ← サーバー内チャット⇔Discordチャット相互中継
```

`mikanassets/` 以下は server-bot-v3 が実際に読み込むディレクトリ構成と同じです。
使いたい拡張機能のフォルダ (`mikanassets/extension/<拡張機能名>/`) を、
server-bot-v3 を配置しているディレクトリの `mikanassets/extension/` にそのままコピーしてください。
(各拡張機能は初回のコマンド実行時に、自分のフォルダ内へ `state.json` を自動生成して設定を保存します)

## コーディング規約

- **embedは `bot.embeds.ModifiedEmbeds` を使う**。生の `discord.Embed(...)` は直接使わない。
  `ModifiedEmbeds.DefaultEmbed(title, description=None)` / `ModifiedEmbeds.ErrorEmbed(title, description=None)`
  はどちらも `discord.Embed` のサブクラスで、共通の下線画像・サムネイルを自動設定する。
  コアBotの全コマンド(terminal.py, cmd.py, status.py 等)がこれを使っており、拡張機能だけ
  見た目が異なると統一感が崩れるため、成功系は `DefaultEmbed`、エラー・失敗系は `ErrorEmbed` を使う。
  プレーンテキストの `send_message("...")` も同様に embed 化する(コアBotの
  `not_enough_permission()` を含め、ユーザー向け応答は常に embed が基本)。
- **コマンドの `description` に権限要件を書かない**。「(要上位権限)」のような記述は、
  権限レベルが `.config` の `discord_commands.permission.commands_level` で変更可能な
  設定値である以上、常に正しいとは限らない(管理者が変更すれば古い記述になる)。
  `description` に必要なのは「このコマンドが何をするか」という使い方であって、
  「誰が使えるか」という利用条件ではない。権限要件を書きたい場合は、コマンド一覧とは別に
  独立したセクション(このREADMEの各拡張の説明や、`rcon` のようにモジュール docstring 内の
  専用セクション)にまとめる。

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

### scheduled-backup

コアBotの `/backup create` はサーバーが停止中でないと実行できない仕様のため、稼働中のサーバーを
定期的にバックアップするには本来「停止 → バックアップ → 起動」を手動で順番に実行する必要があります。
この拡張は `server/control.py` の `start_server` / `stop_server` と `server/backup.py` の
`create_backup`(いずれも `/start` `/stop` `/backup create` と同じ実装)を直接呼び出し、このサイクルを
`append_task` で定期的に自動実行します。サイクル実行時にサーバーが既に停止していた場合は、
**バックアップ自体は変わらず実行します**(`create_backup` はファイルコピーするだけでサーバー状態を
問わないため)。ただし起動は行いません(管理者が意図的に停止している可能性があるため、拡張側から
無条件に起動はしない)。

サーバーへの警告コマンド(`server_warning_command` / `server_warning_minutes_before`)とDiscordへの通知
(`notify_discord` / `discord_channel_id`)は名前で明確に区別しています。Minecraft専用の "say" 等は
ハードコードしておらず、`server_warning_command` は任意のstdinコマンド文字列を`config`で設定できます
(既定は空文字列 = 送信しない)。`discord_channel_id` が未設定のまま `notify_discord` を有効化した場合は、
`/config` を実行したチャンネルへ自動的にフォールバックします(既に設定済みのチャンネルは上書きしません)。

初回実行前の「次回予定」は `enabled` を `true` にした時刻を起点に `interval_minutes` 後として計算します
(一度も実行していない間は「前回実行: なし」と表示されます)。`next_run_in_minutes`(今から何分後か)
で次回1回分の実行時刻を明示指定でき、実行後は自動で通常の間隔ベースの計算に戻ります。日時文字列を
正確に入力させる方式は使いにくいため、他のパラメータ(`interval_minutes`等)と同じ「分数」ベースの
指定に統一しています。

次回実行時刻は「サイクルが完了した時刻」ではなく「本来狙っていた理想スロット」を起点に計算するため、
バックアップの所要時間がそのままインターバルに上乗ってずれ続けることはありません(例: 5分間隔で
1回のバックアップが6分かかっても、次のスロットは6分後ではなく直近の未来のスロット=10分後に
飛びます。処理がinterval以内に終わる通常時は起点が全くずれません)。

- `/extension-scheduled-backup status` — 現在の設定・次回予定・前回実行を表示
- `/extension-scheduled-backup run-now` — スケジュールを待たず今すぐサイクルを実行(要上位権限)
- `/extension-scheduled-backup config` — 有効/無効・間隔・バックアップ対象(`server_path`基準の相対パス)・
  Discord通知有無/通知先チャンネル・サーバーへの事前警告(分数とコマンド)・停止待ちタイムアウト秒数・
  次回1回分の実行時刻(`next_run_in_minutes`)を設定(要上位権限)

権限レベルは `rcon` 拡張と同じく `state.json` ではなく `.config` の
`discord_commands.permission.commands_level` で管理します(`extension-scheduled-backup status` は既定0、
`run-now` / `config` は既定2)。それ以外の設定(間隔・対象・通知・警告)は他拡張と同じく `state.json` に保存します。

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
そのため `rcon` の各コマンドも `"extension-rcon <サブコマンド名>"` というキーで**同じ
`commands_level` 辞書**を直接参照する設計に変更しています。キー名は実際のスラッシュ
コマンド(`/extension-rcon cmd` 等 — `bot/extensions.py` が拡張フォルダ名 `rcon` の頭に
`extension-` を自動で付ける)とそのまま一致させています(単に `rcon cmd` 名義にしていた
初期実装は、キー名と実コマンド名が食い違うため修正しました)。`.config` にキーが
存在すればその値を、無ければ以下のデフォルト値を使います:

| デフォルトレベル | 対象コマンド(`.config`でのキー例) |
|---|---|
| 0(誰でも) | `extension-rcon check`, `extension-rcon list`, `extension-rcon whitelist list` |
| 1 | `extension-rcon gamemode`, `extension-rcon weather`, `extension-rcon time`, `extension-rcon difficulty`, `extension-rcon say`, `extension-rcon tp`, `extension-rcon give`, `extension-rcon xp`, `extension-rcon summon`, `extension-rcon setblock`, `extension-rcon title`, `extension-rcon effect give`, `extension-rcon effect clear`, `extension-rcon whitelist add`, `extension-rcon whitelist remove` |
| 2 | `extension-rcon config`, `extension-rcon cmd`(任意コマンド)、`extension-rcon kill`、`extension-rcon player ban/pardon/kick/op/deop` |

権限レベルを変更したい場合は、Discordコマンドではなく `.config` を直接編集してください。例:

```json
"discord_commands": {
  "permission": {
    "commands_level": {
      "extension-rcon cmd": 3,
      "extension-rcon gamemode": 0
    }
  }
}
```

`extension-rcon cmd` は `allow_cmd` のような許可リストを一切通さない無条件の生コマンド実行
なので、コアBotの `/cmd serverin`(既定 level 1)より一段高いデフォルト level 2 にしています。

**キーは自動的に追加されます。** `.config` に `"extension-rcon cmd"` のようなキーがまだ
存在しない場合、拡張ロード時にデフォルト値で登録し、そのまま**その場で** `.config` ファイルへ
書き戻します(コアBotが `core/config_loader.py` の起動時処理で自身のコマンドの権限キーを
補完しているのと同じ考え方です)。拡張のロードは Discord のイベントループが始まる前の
同期的な起動フェーズで行われるため、コマンド実行を待つ必要がなく即座に反映できます。既に
手動で値を設定してあるキーは上書きされません。つまり、Botを一度起動するだけで `.config` を
開けば `"extension-rcon cmd": 2` のように全キーが既に並んだ状態になっており、変更したい値
だけ書き換えれば済みます。また `ctx.text.command_permission` はコアBotの
`/permission view <user> detail:true` が一覧表示する辞書そのものなので、rconのキーも
そこに一緒に表示されます(この一覧表示自体もキー数増加でDiscordのフィールド文字数上限に
引っかかっていたため、コアBot側の `bot/commands/permission.py` を複数フィールドに
分割するよう修正済みです)。

> [!NOTE]
> 以前 `rcon cmd` のような旧キー名で `.config` に登録されていた場合、そのキーは新キー名
> (`extension-rcon cmd`)には自動移行されません。まだ配布前で影響範囲が手元の環境に
> 限られるため、移行ロジックは実装せず、不要になった旧キーは手動で削除する運用としています。

### world-visualizer

ワールドのリージョンファイル(.mca)とプレイヤーの playerdata(.dat)を直接読み取り、
画像として表示する拡張機能です。RCONやサーバーの標準出力に依存しない読み取り専用実装のため、
サーバーが停止中でも動作します。単一の `commands.py` には収めず、責務ごとに `wv_*.py` へ
分割しています(構成は `commands.py` 冒頭のdocstring「ファイル構成」節を参照)。

- `/extension-world-visualizer map [dimension=overworld|nether|end]` — チャンクごとの
  実ブロック色(草ブロック上面・葉・水などはバイオーム色で補正、それ以外は実テクスチャの
  色そのまま。標高による陰影は無し)から、真上から見た俯瞰マップ画像を1枚合成して返します。
  探索範囲が広い場合は自動的に間引いてサンプリングします(embedの「サンプリング間隔」欄で
  確認できます)。
- `/extension-world-visualizer inventory <player>` — `usercache.json` で名前からUUIDを引き、
  playerdataからメインインベントリ+ホットバーをグリッド画像として、防具/オフハンドをテキストで
  表示します。オフライン中のプレイヤーでも参照できます(直近のオートセーブ時点のデータです)。
- `/extension-world-visualizer config [minecraft_version] [clear_cache]` — マップ描画に
  使うMinecraftバージョンの手動指定/確認、ブロック色キャッシュのクリアを行います。
  ローカルのファイル操作のみでネットワークにはアクセスしません。

**前提**: 画像合成に [Pillow](https://pypi.org/project/Pillow/) が必要です(`pip install Pillow`)。
未インストールの場合は全コマンドとも案内embedを返すだけで、他の拡張機能には影響しません。

**配色の仕組み(知らないブロックが出てきた時だけAPIを叩く。叩く時は全部持ってくる)**:
ブロックID→色の対応表を手作業で用意するとバージョンアップの度にメンテナンスが必要になる
ため、Mojangが公式配布するクライアントjar(ランチャー自体が使うのと同じもの、無認証で
ダウンロード可能)からブロックテクスチャの色を自動抽出します。ただしjar全体(数十MB)を
毎回ダウンロードすることはせず、MojangのCDNが対応しているHTTP RangeリクエストでZIPの
中央ディレクトリだけ取得してファイル一覧を把握します。ネットワークへは「まだ見たことが
無いブロックに遭遇した時」だけアクセスしますが、一度アクセスする以上は中央ディレクトリの
取得コストを払い済みなので、**その場でブロックテクスチャ全部(実測1268種類、約3.4MB・
約19秒)をまとめて取得してキャッシュします**。一度取得しきったバージョンでは、以後
`/map` を何度実行してもネットワークへ一切アクセスしません(バージョンをまたいで
`block_color_cache/colors.json`〈gitignore対象〉へ永続キャッシュ)。ネットワーク不通/
バージョン未検出の環境では解決できなかったブロックが灰色で表示されます(embedの
「配色」欄で確認できます)。詳細な設計意図は `wv_blockcolors.py` 冒頭のdocstringを
参照してください。

Minecraftバージョンは、ワールドの `<level-name>/level.dat` (NBTのData.Version.Name)から
自動検出します(ワールドが1回でも保存されていれば必ず存在するため、この方式を優先しています。
`version_history.json` はサーバー構成によっては生成されないことがあるため補助的な手段として
残しています)。どちらも取得できない場合は `/extension-world-visualizer config minecraft_version:<バージョン>`
で手動指定してください。

チャンクデータ形式は 1.18 以降(sections直下・Heightmaps・セクション毎biomes/block_states
パレット)を前提にしているため、1.17以前のワールドでは `map` が正しく描画できない場合が
あります(詳しい制約は `wv_worldmap.py` 冒頭のdocstringを参照)。読み取り専用のため権限チェックは
設けていません(`whitelist-ops-viewer` と同様)。

### update-watch

`world-visualizer` のバージョン自動検出を実機検証した際、`versions/` に新しいバージョンの
jarが既に置かれているのに、実際に稼働しているワールドは古いバージョンのまま保存され続けている
状態が見つかりました。「新しいバージョンは取得したが、実際に切り替える(停止→バックアップ→
jar差し替え→起動)作業が手間で後回しになっている」という実際の運用コストを埋めるための拡張です。

- 一定間隔(既定24時間)でMojangのバージョンマニフェストを取得し、ワールドの
  `<level-name>/level.dat` (Data.Version.Name、`world-visualizer` と同じ検出方式)と比較、
  新しいバージョンがあればDiscordへ通知します(同じバージョンで再通知はしません)。
- `/extension-update-watch apply [version]` で実際の切り替え(停止→バックアップ→
  `server.jar` 差し替え→起動)を半自動実行できます。バージョン文字列同士の大小比較は
  誤りやすいため自作せず、マニフェスト自身の `releaseTime` 順を使っています。

**安全策**:
- jarのダウンロード・sha1/サイズ検証はサーバー稼働中に済ませ、実際のファイル差し替えは
  停止後にのみ行います(Windows環境ではJVMがserver.jarを実行中ずっと開いたままにするため)。
- 差し替え前に必ず `server_path` 全体をバックアップします(対象を選ばせません)。
- `plugins/` (Paper/Spigot系) または `mods/` (Forge/Fabric系) が存在するサーバー構成では
  `apply` 自体を拒否します。Mojang配布のvanilla jarへ黙って差し替えると導入済みの
  プラグイン/MODが動かなくなる事故につながるため、バニラ以外は対象外にしています。
- 起動失敗時の自動ロールバックは行いません(`downtime-notifier` と同じ理由で、プロセス
  ポーリングだけでは「新バージョン側の問題」か「起動に時間がかかっているだけ」かを
  確実に区別できないため)。直前に取ったバックアップの場所を結果に明記するので、
  問題があればコアBotの `/backup apply` で手動対応してください。

- `/extension-update-watch status` — 検出中のバージョンと更新状況を表示
- `/extension-update-watch check-now` — 今すぐMojangのバージョンマニフェストを確認
- `/extension-update-watch apply [version]` — 停止→バックアップ→jar差し替え→起動を実行(要上位権限)
- `/extension-update-watch config` — 自動チェック有効/無効・間隔(時間)・通知先チャンネル・
  スナップショットを対象に含めるか・対象jarファイル名・停止待ちタイムアウト秒数を設定(要上位権限)

権限レベルは `rcon` / `scheduled-backup` と同じく `state.json` ではなく `.config` の
`discord_commands.permission.commands_level` で管理します(`status` / `check-now` は既定0、
`apply` / `config` は既定2)。

### chat-bridge

サーバー内のチャットとDiscordのチャットを相互に中継します。**Minecraft専用ではありません**
(disk-space-watchdog や auto-announce と同じく `category: general`)。サーバー内チャットが
テキストのログとして出力され、コンソール入力でチャット送信できるサーバーであれば、
検出パターン・送信コマンドを `config` で設定するだけでゲームを問わず使えます。以下で示す
ログ判定パターン(Log4j2形式の正規表現)や `say` コマンドはあくまで「利用者が多いであろう
バニラMinecraftを例にした既定値」であり、コード側にMinecraft固有の処理はハードコードして
いません。他のゲームサーバーで使う場合は、そのサーバーのログ形式とチャット送信コマンドに
合わせて `config` で書き換えてください。

- **サーバー→Discord**: server-bot-v3 が拡張機能向けに提供している `core.log_tailer.LogTailer`
  (`async for line in tailer:` で新規行を待ち続ける非同期イテレータ)を、既定のソースのまま
  使います。ソースは `LogManager.snapshot_log_msg()` — bot/sys/server/cmd 等**全ロガーが混ざった**
  直近100件の共有バッファで、サーバーの標準出力も server ロガー経由で常にここに流れ込みます
  (`server/stdout.py` の `server_log.info(line)` をソースで確認済み。ファイルへも書き出すか
  どうかを決める `.config` の `log.server` フラグには一切左右されず常に実行されるため、
  「ログが存在しない」という状態がほぼ起こりません)。このバッファは表示用にANSIカラーコード
  (`\033[...m`)付きの文字列になっていますが、これは無視せず単純に正規表現で取り除いてから
  チャット検出に使っています(`core/log_setup.py` の `ServerConsoleFormatter` 等をソースで確認し、
  実際に `LogManager` へ本物のログ行を書き込ませて ANSI除去+正規表現の一連の処理が
  日本語チャットも含めて正しく動くことをテスト済み)。他ロガーの行(bot起動メッセージ・
  コマンド実行ログ等)も同じバッファに混ざりますが、`game_log_pattern` は「サーバーのログ行に
  現れる特定の並び」だけを `search()` で拾うため実害はありません。この設計により、以前の実装
  (`ctx.server_path/logs/` 配下のセッションログファイルを直接globで探す方式)が抱えていた
  「`.config` の `log.server` を有効にしておく必要がある」「非公開のファイル名パターンに依存する」
  という2つの制約を両方解消しています。代わりに、共有バッファが全ロガー合算で`maxlen=100`件しか
  無いという制約を受け入れています(個人〜身内数人規模のサーバー運用ならこの上限に達することは
  通常ありません)。
  `async for` は開始した瞬間にその時点でバッファに既にある内容を丸ごと最初のバッチとして
  返すため、拡張を有効化した直後に既存内容を一括でDiscordへ流さないよう、開始時点の最後の行が
  来るまでは処理をスキップしています。マッチした行は `game_log_pattern`(正規表現。`{name}` と
  `{chat}` というプレースホルダを埋め込む)に照合し、マッチした部分を `{name}`/`{chat}` の
  名前付きキャプチャとして抽出します。既定値はバニラMinecraft(Log4j2)の
  `<プレイヤー名> 発言内容` 形式を例にした
  `\[\d{2}:\d{2}:\d{2}\] \[Server thread/INFO\]: <{name}> {chat}`。マッチした発言は
  `discord_message_format`(プレースホルダ `{server}` `{channel}` `{name}` `{chat}` が使える。
  既定値はシンプルな `<{name}> {chat}`。送信先は `discord_channel` 1個に固定されており発言元は
  常に単一のサーバーなので、下記の `game_command_format` のような発言元の区別は既定では不要という
  判断)で整形してDiscordチャンネルへ送信します。
- **Discord→サーバー**: **サーバー→Discordとは非対称の設計です。** サーバー→Discordの送信先は
  `discord_channel` で指定する単一チャンネルですが、Discord→サーバーは特定チャンネルに絞らず
  **bot が参加している全てのDiscordサーバー(ギルド)の全テキストチャンネル**を対象にします
  (`channel.permissions_for(guild.me)` で読み取り権限があるチャンネルのみ)。複数チャンネル・
  複数Discordサーバーから発言が混ざって届くため、`game_command_format` の既定値は `{server}`
  (発言があったDiscordサーバー名、`message.guild.name`)から始まる書式にしてあり、サーバー内で
  どこからの発言か分かるようにしています(既定値 `say {server} #{channel} ✧ <{name}> {chat}`)。
  `on_message` イベントは使いません。`discord.Client`(server-bot-v3の `bot/client.py` で
  `discord.Client(intents=...)` として生成されており、`commands.Bot` ではないことをソースで
  確認済み)には拡張機能から追加のイベントリスナーを登録する `add_listener` が存在せず、
  コアBotは `bot/events.py` で `@client.event async def on_message` を既にモジュールロード時に
  登録して `/terminal set` のターミナルチャンネル機能を実装しているため、拡張側で
  `client.on_message` を上書きするとその機能を壊します。代わりに `append_task` のループ
  (3秒間隔)で対象チャンネルごとに `channel.history()` を定期ポーリングし、前回チェック以降の
  新規メッセージ(bot自身の発言は除く)だけを `game_command_format` で整形し、
  `write_server_in()` でサーバーへ書き込みます。改行は `write_server_in` が複数コマンドの
  区切りとして解釈してしまうため必ず除去してから渡します。

> [!WARNING]
> Discord→サーバーは「bot が参加している全てのDiscordサーバーの全チャンネル」が対象です。
> モデレーター専用チャンネルなど、サーバー内に見せたくない内容のあるチャンネルにbotを招待している
> 場合、その発言もサーバー内チャットへそのまま転送されます。個人〜身内数人規模の運用を前提に
> した割り切りです。公開性の高い環境で使う場合は、転送したくないチャンネルからそのチャンネル
> だけbotの閲覧権限を外すなどして調整してください。

既定の書式同士は「`say {server} #{channel} ✧ <name> chat` を送信した結果ログに出る
`[Server] {server} #{channel} ✧ <name> chat`」が `game_log_pattern` の `: <{name}>` という
並びに一致しないため、Discordから転送した発言がそのままサーバーに送り返され自己ループする心配は
ありません(実際にレンダリングした文字列で検証済み)。ただしこれは既定値同士の組み合わせで
たまたま成立している性質なので、他のゲーム用に `game_log_pattern` / `game_command_format` を
変更する場合は `/extension-chat-bridge test` で自己ループが起きないか確認してください。

**前提**: Discordメッセージの内容を読み取るには Message Content Intent が必要です。
`bot/client.py` を確認したところ、コード側の `discord.Intents` では既に
`intents.message_content = True` が設定済みでした。残る前提は
[Discord Developer Portal](https://discord.com/developers/applications) 側でこのBotアプリケーションの
「Message Content Intent」をオンにすることだけです(こちらはコードでは有効化できない、
Discord側の設定)。無効なままだと、サーバー→Discordは動作しますが、Discord→サーバーの発言内容が
常に空になり中継されません。

- `/extension-chat-bridge status` — 現在の設定と稼働状況を表示(サーバー→Discordの転送先チャンネル、
  Discord→サーバーで実際に監視対象になっているDiscordサーバー数/チャンネル数を含む)
- `/extension-chat-bridge test <sample_line>` — `game_log_pattern` をサンプル行に対して試す
  (マッチ結果とDiscord送信プレビューを表示)
- `/extension-chat-bridge config` — 有効/無効・サーバー→Discordの転送先チャンネル・サーバー表示名・
  サーバーログ判定パターン・Discord表示書式・サーバー送信コマンド書式を設定
  (要上位権限)。`enabled` を `true` にする際、中継先チャンネル未設定なら実行チャンネルへ
  自動的にフォールバックします(`scheduled-backup` と同様)。

権限レベルは `rcon` / `scheduled-backup` / `update-watch` と同じく `state.json` ではなく
`.config` の `discord_commands.permission.commands_level` で管理します(`status` / `test` は
既定0、`config` は既定1)。
