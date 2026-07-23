# server-bot-extensions-pack

[server-bot-v3](https://github.com/sleeping-mikan/server-bot-v3) 用の拡張機能実装集です。
拡張機能の仕様は公式リポジトリ [server-bot-extensions](https://github.com/sleeping-mikan/server-bot-extensions) を参照してください。

## 構成

```
plan.json                                    ← 拡張機能の実装計画(アイデア一覧・API概要)
mikanassets/
  extension/
    quick-commands/
      commands.py                            ← 実装済み拡張機能
```

`mikanassets/` 以下は server-bot-v3 が実際に読み込むディレクトリ構成と同じです。
使いたい拡張機能のフォルダ (`mikanassets/extension/<拡張機能名>/`) を、
server-bot-v3 を配置しているディレクトリの `mikanassets/extension/` にそのままコピーしてください。

## 実装計画

10種類以上の拡張機能アイデアと、その実装状況を [plan.json](plan.json) にまとめています。
各アイデアには `status` (`implemented` / `planned` / `blocked`)・使用する拡張API・対応サーバー種別を記載しています。

## 実装済み拡張機能

### quick-commands

よく使うバニラ系コンソールコマンド (weather / time / gamemode / difficulty / say) を
選択式のスラッシュコマンドとしてラップする拡張機能です。

- `/extension-quick-commands weather <clear|rain|thunder>`
- `/extension-quick-commands time <day|noon|night|midnight>`
- `/extension-quick-commands gamemode <mode> <player>`
- `/extension-quick-commands difficulty <peaceful|easy|normal|hard>`
- `/extension-quick-commands say <message>`

vanilla / paper / spigot / fabric / forge など、バニラ互換コマンドを受け付けるサーバーで動作します。

詳細は [mikanassets/extension/quick-commands/commands.py](mikanassets/extension/quick-commands/commands.py) を参照してください。
