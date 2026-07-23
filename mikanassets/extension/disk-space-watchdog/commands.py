"""
disk-space-watchdog — サーバー設置先ドライブの空き容量を定期監視し、閾値を下回ったら
Discordへ警告する拡張機能。

自宅サーバー運用ではログ/バックアップ/ワールドデータ肥大化によるディスク枯渇が
実際に起こる失敗モードだが、コアBotには監視機能が無いため append_task で補う。

登録される全コマンド: /extension-disk-space-watchdog <status|config>
"""

from __future__ import annotations

import json
import shutil
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
_TICK_SECONDS = 60
_DEFAULT_STATE = {
    "threshold_gb": 5.0,
    "interval_minutes": 15,
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
_elapsed_seconds = 0
_already_alerted = False


def _free_gb() -> float:
    return shutil.disk_usage(ctx.server_path).free / (1024**3)


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
async def _watchdog_loop() -> None:
    global _elapsed_seconds, _already_alerted

    _elapsed_seconds += _TICK_SECONDS
    interval_seconds = max(1, _state["interval_minutes"]) * 60
    if _elapsed_seconds < interval_seconds:
        return
    _elapsed_seconds = 0

    free_gb = _free_gb()
    if free_gb < _state["threshold_gb"]:
        if not _already_alerted:
            _already_alerted = True
            logger.error(f"free space low: {free_gb:.2f}GB < {_state['threshold_gb']}GB")
            await _notify_discord(f"⚠️ ディスク空き容量が閾値を下回りました: {free_gb:.2f}GB (閾値 {_state['threshold_gb']}GB)")
    else:
        _already_alerted = False


append_task(_watchdog_loop)


async def _check_permission(interaction: discord.Interaction) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < REQUIRED_LEVEL:
        await not_enough_permission(interaction, logger)
        return False
    return True


@tree.command(name="status", description="現在の空き容量を表示する")
async def status_command(interaction: discord.Interaction) -> None:
    await print_user(logger, interaction.user)
    free_gb = _free_gb()
    embed = discord.Embed(title="ディスク空き容量", color=discord.Color.green() if free_gb >= _state["threshold_gb"] else discord.Color.red())
    embed.add_field(name="空き容量", value=f"{free_gb:.2f} GB", inline=True)
    embed.add_field(name="閾値", value=f"{_state['threshold_gb']} GB", inline=True)
    embed.add_field(name="チェック間隔", value=f"{_state['interval_minutes']}分", inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="config", description="閾値・間隔・通知先チャンネルを設定する")
async def config_command(
    interaction: discord.Interaction,
    threshold_gb: float | None = None,
    interval_minutes: int | None = None,
    channel: discord.TextChannel | None = None,
) -> None:
    if not await _check_permission(interaction):
        return
    if threshold_gb is not None:
        _state["threshold_gb"] = max(0.1, threshold_gb)
    if interval_minutes is not None:
        _state["interval_minutes"] = max(1, min(interval_minutes, 1440))
    if channel is not None:
        _state["discord_channel_id"] = channel.id
    _save_state()
    await status_command.callback(interaction)
