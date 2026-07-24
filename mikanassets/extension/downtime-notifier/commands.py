"""
downtime-notifier — サーバープロセスが予期せず停止したままになっていないかを監視し、
猶予時間を超えて停止し続けている場合にDiscordへ通知する拡張機能。

注意: ctx.use_stop は「意図的な停止かクラッシュか」を示すフラグに見えるが、
クラッシュ時も server/stdout.py の読み取りスレッドが検知直後に True へ強制するため、
このextensionのように一定間隔でポーリングする側からは意図的な停止とクラッシュを
区別できない(タイミングの取り方次第で誤検知する)。そのため原因を断定する
"クラッシュ検知" は行わず、「止まったまま一定時間経過している」ことだけを
中立的に知らせる設計にしている。/restart はBotプロセスごと再起動されこの拡張の
ループも再ロードされるため、/restart 自体を誤検知することはない。

登録される全コマンド: /extension-downtime-notifier config
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import tasks

from bot.client import client
from bot.embeds import ModifiedEmbeds
from bot.extensions import append_task
from bot.utils import not_enough_permission, print_user, user_permission
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

REQUIRED_LEVEL = 1

_STATE_FILE = Path(__file__).parent / "state.json"
_TICK_SECONDS = 15
_DEFAULT_STATE = {
    "grace_seconds": 120,
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
_was_running = False
_stopped_since: datetime | None = None
_alerted = False


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
async def _watch_loop() -> None:
    global _was_running, _stopped_since, _alerted

    running = ctx.server_process.is_running()

    if running:
        if not _was_running and _stopped_since is not None and _alerted:
            await _notify_discord("✅ サーバーが起動状態に戻りました")
        _was_running = True
        _stopped_since = None
        _alerted = False
        return

    if _was_running:
        _stopped_since = datetime.now()
    _was_running = False

    if _stopped_since is None or _alerted:
        return

    elapsed = (datetime.now() - _stopped_since).total_seconds()
    if elapsed >= _state["grace_seconds"]:
        _alerted = True
        logger.info(f"downtime detected, {elapsed:.0f}s stopped")
        await _notify_discord(
            f"⚠️ サーバーが停止した状態が{int(elapsed)}秒続いています。"
            "意図的な停止か確認してください(このメッセージは意図的な停止でも表示されます)。"
        )


append_task(_watch_loop)


@tree.command(name="config", description="猶予時間・通知先チャンネルを設定する")
async def config_command(
    interaction: discord.Interaction,
    grace_seconds: int | None = None,
    channel: discord.TextChannel | None = None,
) -> None:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < REQUIRED_LEVEL:
        await not_enough_permission(interaction, logger)
        return

    if grace_seconds is not None:
        _state["grace_seconds"] = max(10, grace_seconds)
    if channel is not None:
        _state["discord_channel_id"] = channel.id
    _save_state()

    embed = ModifiedEmbeds.DefaultEmbed(title="downtime-notifier 設定")
    embed.add_field(name="猶予時間", value=f"{_state['grace_seconds']}秒", inline=True)
    embed.add_field(name="通知先チャンネルID", value=str(_state["discord_channel_id"]), inline=True)
    await interaction.response.send_message(embed=embed)
