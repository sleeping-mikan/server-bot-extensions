"""
restart-warning-timer — 再起動予定時刻までの段階的な警告をサーバー内にbroadcastする拡張機能。

コアBotの /restart は即時実行のみで予告機能が無い。実際の停止/再起動操作はコアBotの
/restart や /server stop に委ね、この拡張はカウントダウン警告のbroadcastだけを行う。

登録される全コマンド: /extension-restart-warning-timer <schedule|cancel>
"""

from __future__ import annotations

from datetime import datetime, timedelta

import discord
from discord.ext import tasks

from bot.embeds import ModifiedEmbeds
from bot.extensions import append_task, write_server_in
from bot.utils import not_enough_permission, print_user, user_permission
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

REQUIRED_LEVEL = 1

# 残り時間がこの分数以下になった時点で1回ずつ警告を送る(大きい順)
_CHECKPOINTS_MIN = [10, 5, 1]

_target_time: datetime | None = None
_sent_checkpoints: set[int] = set()


def _broadcast_warning(text: str) -> None:
    ok, reason = write_server_in(f"say [再起動予告] {text}")
    if not ok:
        logger.info(f"warning broadcast skipped ({reason})")
    else:
        logger.info(f"warning sent -> {text}")


@tasks.loop(seconds=15)
async def _timer_loop() -> None:
    global _target_time, _sent_checkpoints

    if _target_time is None:
        return

    remaining = (_target_time - datetime.now()).total_seconds()

    for m in _CHECKPOINTS_MIN:
        if remaining <= m * 60 and m not in _sent_checkpoints:
            _sent_checkpoints.add(m)
            _broadcast_warning(f"あと約{m}分で再起動予定です")

    if remaining <= 0:
        _broadcast_warning("再起動予定時刻になりました")
        _target_time = None
        _sent_checkpoints = set()


append_task(_timer_loop)


async def _check_permission(interaction: discord.Interaction) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < REQUIRED_LEVEL:
        await not_enough_permission(interaction, logger)
        return False
    return True


@tree.command(name="schedule", description="再起動予告タイマーを開始する")
async def schedule_command(interaction: discord.Interaction, minutes: int) -> None:
    global _target_time, _sent_checkpoints

    if not await _check_permission(interaction):
        return
    if not (1 <= minutes <= 180):
        embed = ModifiedEmbeds.ErrorEmbed(title="minutes は1〜180の範囲で指定してください")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    _target_time = datetime.now() + timedelta(minutes=minutes)
    _sent_checkpoints = {m for m in _CHECKPOINTS_MIN if m >= minutes}

    embed = ModifiedEmbeds.DefaultEmbed(
        title="再起動予告タイマーを開始しました",
        description=f"{minutes}分後 ({_target_time.strftime('%H:%M:%S')}) を目安に段階的な警告をサーバー内へ送信します。\n"
        "実際の再起動は /restart などを別途実行してください。",
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="cancel", description="再起動予告タイマーを中止する")
async def cancel_command(interaction: discord.Interaction) -> None:
    global _target_time, _sent_checkpoints

    if not await _check_permission(interaction):
        return
    if _target_time is None:
        embed = ModifiedEmbeds.ErrorEmbed(title="現在動作中のタイマーはありません")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    _target_time = None
    _sent_checkpoints = set()
    _broadcast_warning("再起動予告はキャンセルされました")
    embed = ModifiedEmbeds.DefaultEmbed(title="タイマーを中止しました")
    await interaction.response.send_message(embed=embed)
