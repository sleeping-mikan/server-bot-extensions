# server-bot-extensions
How to use server-bot-v2's extension directory

## 前提

 - 本リポジトリに配置されているmikanassetsはserver-bot-v2正常起動時に生成されるディレクトリです。

## 使い方

 - 拡張機能を追加する際は/mikanassets/extension/<拡張機能名>を追加するか、配布されている拡張機能を該当ディレクトリに配置してください。

## サポートされている機能

 - コマンドの追加
 - (予定)既存コマンドの実行終了時の文字列の改変/追加操作
   - 例えば、/ipの実行で返されるipアドレスは本来discordに返されますが、そのipをextensionにわたし、特定のサーバー向けに作られたプロパティからportを読み出し、その文字列と結合したものを返すことができます。
  
## コード

 - 以下のコードは/mikanassets/extension/template/commands.pyの内容を多少改変したものです。
   - 基本的には以下の記法を用いてコマンドを追加できます。

```py
import discord
# メインに存在するコマンド群をimport(import from __main__ if want to add discord commands) 
from __main__ import extension_commands_group as tree
# もし、管理しているサーバープロセスに対して操作をしたいならprocessをimport
from __main__ import process

# スラッシュコマンドを追加する例(example of adding slash commands)
# このコマンドはdiscord上で/templates helloとして表示される(displayed on discord as /templates hello)
# /templatesの名前はextension下のディレクトリ名に依存する(depend on extension directory name)
@tree.command(name="hello", description="Say hello")
async def hello_command(interaction: discord.Interaction):
    await interaction.response.send_message("Hello, world!")



# サブコマンドグループの例(example of sub command group)
my_group = discord.app_commands.Group(name="group", description="A group of commands")

# サブコマンドの例(example of sub command)
# このコマンドはdiscord上で/templates group say <message>として表示される(displayed on discord as /templates group say <message>)
@my_group.command(name="say", description="Say something")
async def say_command(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message)

# グループを登録(register group)
tree.add_command(my_group)
```

