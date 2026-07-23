"""
idle-hours-notice — 設定した時間帯の開始時刻に1日1回、監視が手薄になる旨をDiscordへ
通知する拡張機能。身内で遊ぶ時間を合わせたり、「この時間から先は誰も見ていない」を
明示するための最小構成の告知機能。

登録される全コマンド: /extension-idle-hours-notice config
"""

from __future__ import annotations

import json
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import tasks

from bot.client import client
from bot.extensions import append_task
from bot.utils import not_enough_permission, print_user, user_permission
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

REQUIRED_LEVEL = 1

_STATE_FILE = Path(__file__).parent / "state.json"
_TICK_SECONDS = 300
_DEFAULT_STATE = {
    "start_hour": 0,
    "discord_channel_id": None,
}


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return dict(_DEFAULT_STATE)
    try:
        with _STATE_FILE.open("r", encoding="utf-8") as f:
            return {**_DEFAULT_STATE, **json.load(f)}
    except Exception as e:
        logger.error(f"failed to load state, using defaults ({e})")
        return dict(_DEFAULT_STATE)


def _save_state() -> None:
    with _STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(_state, f, indent=2, ensure_ascii=False)


_state = _load_state()
_last_notified_date: str | None = None


async def _notify_discord(text: str) -> None:
    channel_id = _state["discord_channel_id"]
    if not channel_id:
        logger.info(f"discord channel not configured, skip notify: {text}")
        return
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.HTTPException as e:
            logger.error(f"discord channel fetch failed ({e})")
            return
    await channel.send(text)


@tasks.loop(seconds=_TICK_SECONDS)
async def _notice_loop() -> None:
    global _last_notified_date

    now = datetime.now()
    today = date_cls.today().isoformat()
    if now.hour != _state["start_hour"] or _last_notified_date == today:
        return

    _last_notified_date = today
    await _notify_discord(f"🌙 {_state['start_hour']}時になりました。この時間帯は監視が手薄になります。")


append_task(_notice_loop)


@tree.command(name="config", description="静かな時間帯の開始時刻・通知先チャンネルを設定する")
async def config_command(
    interaction: discord.Interaction,
    start_hour: int | None = None,
    channel: discord.TextChannel | None = None,
) -> None:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < REQUIRED_LEVEL:
        await not_enough_permission(interaction, logger)
        return

    if start_hour is not None:
        if not (0 <= start_hour <= 23):
            await interaction.response.send_message("start_hour は0〜23で指定してください", ephemeral=True)
            return
        _state["start_hour"] = start_hour
    if channel is not None:
        _state["discord_channel_id"] = channel.id
    _save_state()

    embed = discord.Embed(title="idle-hours-notice 設定", color=discord.Color.blurple())
    embed.add_field(name="開始時刻", value=f"{_state['start_hour']}時", inline=True)
    embed.add_field(name="通知先チャンネルID", value=str(_state["discord_channel_id"]), inline=True)
    await interaction.response.send_message(embed=embed)
