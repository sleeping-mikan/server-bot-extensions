"""
quick-commands — よく使うバニラ系コンソールコマンドをスラッシュコマンド化する拡張機能。

対応: vanilla / paper / spigot / fabric / forge など、バニラ互換の
weather・time・gamemode・difficulty・say コマンドを受け付けるサーバー。

登録される全コマンド: /extension-quick-commands <weather|time|gamemode|difficulty|say>
"""

from __future__ import annotations

import re
from typing import Literal

import discord

from bot.extensions import write_server_in
from bot.utils import not_enough_permission, print_user, user_permission
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

# このサーバーの状態を変更するコマンド群なので最低限のモデレーター権限を要求する
REQUIRED_LEVEL = 1

# Minecraft のプレイヤー名 (1-16文字の英数字/アンダースコア) か、
# @a/@p/@e/@r のターゲットセレクターのみ許可する
_PLAYER_PATTERN = re.compile(r"^([A-Za-z0-9_]{1,16}|@[aeprs])$")

WeatherType = Literal["clear", "rain", "thunder"]
TimeValue = Literal["day", "noon", "night", "midnight"]
GameMode = Literal["survival", "creative", "adventure", "spectator"]
Difficulty = Literal["peaceful", "easy", "normal", "hard"]


async def _run(interaction: discord.Interaction, command: str, summary: str) -> None:
    """権限チェック → write_server_in → 結果を ephemeral で返す共通処理。"""
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < REQUIRED_LEVEL:
        await not_enough_permission(interaction, logger)
        return

    ok, reason = write_server_in(command)
    if not ok:
        logger.info(f"write_server_in failed ({reason}) -> {command}")
        await interaction.response.send_message(f"実行できませんでした ({reason})", ephemeral=True)
        return

    logger.info(f"sent -> {command}")
    embed = discord.Embed(title=summary, description=f"```{command}```", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)


@tree.command(name="weather", description="天候を変更する")
async def weather_command(interaction: discord.Interaction, condition: WeatherType) -> None:
    await _run(interaction, f"weather {condition}", f"天候を {condition} に変更")


@tree.command(name="time", description="時刻を変更する")
async def time_command(interaction: discord.Interaction, value: TimeValue) -> None:
    await _run(interaction, f"time set {value}", f"時刻を {value} に変更")


@tree.command(name="gamemode", description="プレイヤーのゲームモードを変更する")
async def gamemode_command(interaction: discord.Interaction, mode: GameMode, player: str) -> None:
    if not _PLAYER_PATTERN.match(player):
        await interaction.response.send_message("プレイヤー名が不正です (英数字/アンダースコア、または @a @p @e @r)", ephemeral=True)
        return
    await _run(interaction, f"gamemode {mode} {player}", f"{player} のゲームモードを {mode} に変更")


@tree.command(name="difficulty", description="難易度を変更する")
async def difficulty_command(interaction: discord.Interaction, level: Difficulty) -> None:
    await _run(interaction, f"difficulty {level}", f"難易度を {level} に変更")


@tree.command(name="say", description="サーバー内に一斉送信する")
async def say_command(interaction: discord.Interaction, message: str) -> None:
    # write_server_in はコマンド文字列に "\n" を付けて stdin へ書き込むだけなので、
    # message 内に改行が残っていると別コマンドとして注入されてしまう。ここで必ず除去する。
    sanitized = " ".join(message.splitlines()).strip()[:200]
    if not sanitized:
        await interaction.response.send_message("メッセージが空です", ephemeral=True)
        return
    await _run(interaction, f"say {sanitized}", "サーバー内にメッセージを送信")
