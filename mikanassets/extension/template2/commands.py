import discord
# メインに存在するコマンド群をimport(import from __main__ if want to add discord commands) 
from __main__ import extension_commands_group as tree

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