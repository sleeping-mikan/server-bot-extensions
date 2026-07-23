# server-bot-extensions-pack

[server-bot-v3](https://github.com/sleeping-mikan/server-bot-v3) 用の拡張機能実装集です。
拡張機能の仕様は公式リポジトリ [server-bot-extensions](https://github.com/sleeping-mikan/server-bot-extensions) を参照してください。

## 構成

```
plan.json                                    ← 拡張機能の実装計画(アイデア一覧・API概要)
mikanassets/
  extension/
    auto-announce/
      commands.py                            ← 実装済み拡張機能
      state.json                             ← 初回コマンド実行時に自動生成される設定ファイル
```

`mikanassets/` 以下は server-bot-v3 が実際に読み込むディレクトリ構成と同じです。
使いたい拡張機能のフォルダ (`mikanassets/extension/<拡張機能名>/`) を、
server-bot-v3 を配置しているディレクトリの `mikanassets/extension/` にそのままコピーしてください。

## 実装計画

10種類以上の拡張機能アイデアと、その実装状況を [plan.json](plan.json) にまとめています。
各アイデアには `status` (`implemented` / `planned` / `blocked`)・使用する拡張API・対応サーバー種別を記載しています。

## 実装済み拡張機能

### auto-announce

設定した間隔で、定型メッセージをサーバー内(コンソールの `say`)と Discord チャンネルの
両方(または片方)に自動送信する拡張機能です。

- `/extension-auto-announce add <message>` — アナウンスするメッセージを追加
- `/extension-auto-announce remove <index>` — 番号指定で削除
- `/extension-auto-announce list` — 現在の設定と登録済みメッセージを表示
- `/extension-auto-announce config` — 間隔(分)・送信先(サーバー/Discordチャンネル)を設定

サーバーの種類を問わず動作します(サーバー内送信を使う場合はコンソールに `say <message>` を
受け付けるサーバーが対象)。

詳細は [mikanassets/extension/auto-announce/commands.py](mikanassets/extension/auto-announce/commands.py) を参照してください。

### 却下した案: quick-commands

最初に weather/time/gamemode/difficulty/say を選択式スラッシュコマンド化する
`quick-commands` を実装しましたが、コアBotの `/cmd serverin` とターミナルチャンネル機能で
既に同じことができると指摘を受けて削除しました。経緯は [plan.json](plan.json) の
`quick-commands` エントリ (`status: "rejected"`) を参照してください。
