"""
auto-announce — 定型メッセージを一定間隔でサーバー内 / Discord に自動送信する拡張機能。

コアBotの /cmd serverin やターミナルチャンネルは「人が都度打ち込む」一発コマンドしか
提供しないため、"N分おきに繰り返し送る" という自動化はできない。これは append_task
(discord.ext.tasks によるバックグラウンドループ) を使わないと実現できず、拡張機能を
作る意味がある領域。

各メッセージには任意で date (MM-DD) を指定できる。指定した場合、その日付と一致する
日にしか送信されない(季節イベント告知を兼ねる)。

登録される全コマンド: /extension-auto-announce <add|remove|list|config>
"""

from __future__ import annotations

import json
import re
from datetime import date as date_cls
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from bot.client import client
from bot.extensions import append_task, write_server_in
from bot.utils import not_enough_permission, print_user, user_permission
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

# 設定変更 (add/remove/config) に要求する最低権限レベル
REQUIRED_LEVEL = 1

_STATE_FILE = Path(__file__).parent / "state.json"
_TICK_SECONDS = 30
_DATE_PATTERN = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
_DEFAULT_STATE = {
    "interval_minutes": 30,
    "to_server": True,
    "to_discord": False,
    "discord_channel_id": None,
    "messages": [],  # [{"text": str, "date": "MM-DD" | None}, ...]
}


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return dict(_DEFAULT_STATE)
    try:
        with _STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        merged = {**_DEFAULT_STATE, **data}
        # 旧バージョン(文字列のみのリスト)からの互換読み込み
        merged["messages"] = [
            {"text": m, "date": None} if isinstance(m, str) else m
            for m in merged["messages"]
        ]
        return merged
    except Exception as e:
        logger.error(f"failed to load state, using defaults ({e})")
        return dict(_DEFAULT_STATE)


def _save_state() -> None:
    with _STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(_state, f, indent=2, ensure_ascii=False)


_state = _load_state()
_elapsed_seconds = 0
_cursor = 0


async def _broadcast(message: str) -> None:
    if _state["to_server"]:
        ok, reason = write_server_in(f"say {message}")
        if not ok:
            logger.info(f"server broadcast skipped ({reason})")

    if _state["to_discord"] and _state["discord_channel_id"]:
        channel = client.get_channel(_state["discord_channel_id"])
        if channel is None:
            try:
                channel = await client.fetch_channel(_state["discord_channel_id"])
            except discord.HTTPException as e:
                logger.error(f"discord announce channel fetch failed ({e})")
                channel = None
        if channel is not None:
            await channel.send(message)


def _active_messages() -> list[dict]:
    today = date_cls.today().strftime("%m-%d")
    return [m for m in _state["messages"] if m["date"] in (None, today)]


@tasks.loop(seconds=_TICK_SECONDS)
async def _announce_loop() -> None:
    global _elapsed_seconds, _cursor

    _elapsed_seconds += _TICK_SECONDS
    interval_seconds = max(1, _state["interval_minutes"]) * 60
    if _elapsed_seconds < interval_seconds:
        return
    _elapsed_seconds = 0

    active = _active_messages()
    if not active:
        return

    entry = active[_cursor % len(active)]
    _cursor += 1
    logger.info(f"announce -> {entry['text']}")
    await _broadcast(entry["text"])


append_task(_announce_loop)


def _sanitize(text: str) -> str:
    # say コマンドとして流すため改行は必ず除去する(write_server_in は改行を
    # 別コマンドの区切りとして解釈してしまうため)
    return " ".join(text.splitlines()).strip()[:200]


async def _check_permission(interaction: discord.Interaction) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < REQUIRED_LEVEL:
        await not_enough_permission(interaction, logger)
        return False
    return True


@tree.command(name="add", description="定期アナウンスするメッセージを追加する")
@app_commands.describe(date="季節限定にする場合のみ指定 (MM-DD形式、例: 12-25)")
async def add_command(interaction: discord.Interaction, message: str, date: str | None = None) -> None:
    if not await _check_permission(interaction):
        return
    sanitized = _sanitize(message)
    if not sanitized:
        await interaction.response.send_message("メッセージが空です", ephemeral=True)
        return
    if date is not None and not _DATE_PATTERN.match(date):
        await interaction.response.send_message("date は MM-DD 形式で指定してください (例: 12-25)", ephemeral=True)
        return
    _state["messages"].append({"text": sanitized, "date": date})
    _save_state()
    suffix = f" ({date}限定)" if date else ""
    await interaction.response.send_message(f"追加しました ({len(_state['messages'])}件目){suffix}: {sanitized}")


@tree.command(name="remove", description="登録済みメッセージを削除する")
async def remove_command(interaction: discord.Interaction, index: int) -> None:
    if not await _check_permission(interaction):
        return
    if not (1 <= index <= len(_state["messages"])):
        await interaction.response.send_message("その番号のメッセージはありません", ephemeral=True)
        return
    removed = _state["messages"].pop(index - 1)
    _save_state()
    await interaction.response.send_message(f"削除しました: {removed['text']}")


@tree.command(name="list", description="設定と登録済みメッセージを表示する")
async def list_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="auto-announce 設定", color=discord.Color.blurple())
    embed.add_field(name="間隔", value=f"{_state['interval_minutes']}分", inline=True)
    embed.add_field(name="サーバー内送信", value=str(_state["to_server"]), inline=True)
    embed.add_field(name="Discord送信", value=str(_state["to_discord"]), inline=True)
    body = "\n".join(
        f"{i}. {m['text']}" + (f" ({m['date']}限定)" if m["date"] else "")
        for i, m in enumerate(_state["messages"], start=1)
    ) or "(未登録)"
    embed.add_field(name="メッセージ一覧", value=body, inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="config", description="間隔・送信先を設定する")
async def config_command(
    interaction: discord.Interaction,
    interval_minutes: int | None = None,
    to_server: bool | None = None,
    to_discord: bool | None = None,
    discord_channel: discord.TextChannel | None = None,
) -> None:
    if not await _check_permission(interaction):
        return
    if interval_minutes is not None:
        _state["interval_minutes"] = max(1, min(interval_minutes, 1440))
    if to_server is not None:
        _state["to_server"] = to_server
    if to_discord is not None:
        _state["to_discord"] = to_discord
    if discord_channel is not None:
        _state["discord_channel_id"] = discord_channel.id
    _save_state()
    await list_command.callback(interaction)
