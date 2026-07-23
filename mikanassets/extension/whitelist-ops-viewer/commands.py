"""
whitelist-ops-viewer — whitelist.json / ops.json / banned-players.json を
整形してDiscordに表示する拡張機能。

コアBotの /cmd stdin ls や send-discord はファイルの存在確認や転送はできるが、
中身のJSONを整形して見せる手段が無い。読み取り専用でサーバー状態には一切書き込まない。

登録される全コマンド: /extension-whitelist-ops-viewer <whitelist|ops|bans>
"""

from __future__ import annotations

import json
from typing import Callable

import discord

from bot.utils import print_user
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger


def _read_json_list(filename: str) -> list[dict] | None:
    path = ctx.server_path / filename
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else None
    except Exception as e:
        logger.error(f"failed to read {filename} ({e})")
        return None


async def _reply_list(interaction: discord.Interaction, filename: str, title: str, line: Callable[[dict], str]) -> None:
    await print_user(logger, interaction.user)
    entries = _read_json_list(filename)
    embed = discord.Embed(title=title, color=discord.Color.gold())
    if entries is None:
        embed.description = f"{filename} が見つからないか読み込めませんでした"
    elif not entries:
        embed.description = "(0件)"
    else:
        embed.description = "\n".join(line(e) for e in entries)[:4000]
        embed.set_footer(text=f"{len(entries)}件")
    await interaction.response.send_message(embed=embed)


@tree.command(name="whitelist", description="whitelist.json の内容を表示する")
async def whitelist_command(interaction: discord.Interaction) -> None:
    await _reply_list(interaction, "whitelist.json", "ホワイトリスト", lambda e: f"- {e.get('name', '?')}")


@tree.command(name="ops", description="ops.json の内容を表示する")
async def ops_command(interaction: discord.Interaction) -> None:
    await _reply_list(
        interaction,
        "ops.json",
        "OP一覧",
        lambda e: f"- {e.get('name', '?')} (level {e.get('level', '?')})",
    )


@tree.command(name="bans", description="banned-players.json の内容を表示する")
async def bans_command(interaction: discord.Interaction) -> None:
    def _line(e: dict) -> str:
        reason = e.get("reason", "")
        expires = e.get("expires", "forever")
        return f"- {e.get('name', '?')} (期限: {expires}) {reason}".rstrip()

    await _reply_list(interaction, "banned-players.json", "BAN一覧", _line)
