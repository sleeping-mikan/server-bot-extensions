"""
backup-watch — バックアップ未実施のリマインドと、古いバックアップの自動削除を行う拡張機能。

server/backup.py が作成するバックアップは ctx.backup_path 直下に
"YYYY-MM-DD_HH_MM_SS-<元フォルダ名>" というディレクトリ名で保存されるため、
このタイムスタンプを直接パースすれば手動記録なしで最終バックアップ時刻が分かる。

登録される全コマンド: /extension-backup-watch <last|prune-now|config>
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord.ext import tasks

from bot.extension_api import (
    ModifiedEmbeds,
    append_task,
    client,
    ctx,
    not_enough_permission,
    print_user,
    user_permission,
)

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

REQUIRED_LEVEL = 1
# prune-now は削除を伴うため、より高い権限を要求する
REQUIRED_LEVEL_PRUNE = 2

_STATE_FILE = Path(__file__).parent / "state.json"
_TS_FORMAT = "%Y-%m-%d_%H_%M_%S"
_DEFAULT_STATE = {
    "remind_after_hours": 48,
    "retention_days": 14,
    "auto_prune": False,
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
_last_seen_ts: datetime | None = None
_already_reminded = False


def _iter_backups() -> list[tuple[datetime, Path]]:
    if not ctx.backup_path.exists():
        return []
    result = []
    for entry in ctx.backup_path.iterdir():
        if not entry.is_dir():
            continue
        try:
            ts = datetime.strptime(entry.name[:19], _TS_FORMAT)
        except ValueError:
            continue
        result.append((ts, entry))
    result.sort(key=lambda x: x[0])
    return result


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


def _prune(retention_days: int) -> list[str]:
    """retention_days より古いバックアップを削除する。最新のものは残す。"""
    backups = _iter_backups()
    if len(backups) <= 1:
        return []
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = []
    for ts, entry in backups[:-1]:  # 最新の1件は保持
        if ts < cutoff:
            try:
                shutil.rmtree(entry)
                removed.append(entry.name)
                logger.info(f"pruned old backup -> {entry.name}")
            except OSError as e:
                logger.error(f"failed to prune {entry.name} ({e})")
    return removed


@tasks.loop(minutes=30)
async def _watch_loop() -> None:
    global _last_seen_ts, _already_reminded

    backups = _iter_backups()
    if not backups:
        return

    latest_ts = backups[-1][0]
    if _last_seen_ts is None or latest_ts > _last_seen_ts:
        _last_seen_ts = latest_ts
        _already_reminded = False

    elapsed_hours = (datetime.now() - latest_ts).total_seconds() / 3600
    if elapsed_hours >= _state["remind_after_hours"] and not _already_reminded:
        _already_reminded = True
        await _notify_discord(
            f"⏰ 最終バックアップから約{int(elapsed_hours)}時間経過しています "
            f"(最終: {latest_ts.strftime('%Y-%m-%d %H:%M:%S')})"
        )

    if _state["auto_prune"]:
        removed = _prune(_state["retention_days"])
        if removed:
            await _notify_discord(f"🗑️ 保持期間({_state['retention_days']}日)を超えたバックアップを{len(removed)}件削除しました")


append_task(_watch_loop)


async def _check_permission(interaction: discord.Interaction, required: int) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < required:
        await not_enough_permission(interaction, logger)
        return False
    return True


@tree.command(name="last", description="最終バックアップからの経過時間を表示する")
async def last_command(interaction: discord.Interaction) -> None:
    await print_user(logger, interaction.user)
    backups = _iter_backups()
    if not backups:
        embed = ModifiedEmbeds.ErrorEmbed(title="バックアップ状況", description="バックアップが見つかりません")
    else:
        latest_ts, latest_entry = backups[-1]
        elapsed = datetime.now() - latest_ts
        embed = ModifiedEmbeds.DefaultEmbed(title="バックアップ状況")
        embed.add_field(name="最終バックアップ", value=latest_entry.name, inline=False)
        embed.add_field(name="経過時間", value=f"約{int(elapsed.total_seconds() // 3600)}時間", inline=True)
        embed.add_field(name="保存数", value=f"{len(backups)}件", inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="prune-now", description="保持期間を超えたバックアップを即時削除する")
async def prune_now_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, REQUIRED_LEVEL_PRUNE):
        return
    await interaction.response.defer()
    removed = _prune(_state["retention_days"])
    if removed:
        embed = ModifiedEmbeds.DefaultEmbed(title=f"{len(removed)}件削除しました", description=", ".join(removed))
    else:
        embed = ModifiedEmbeds.DefaultEmbed(title="削除対象のバックアップはありませんでした")
    await interaction.followup.send(embed=embed)


@tree.command(name="config", description="リマインド閾値・保持日数・自動削除・通知先チャンネルを設定する")
async def config_command(
    interaction: discord.Interaction,
    remind_after_hours: int | None = None,
    retention_days: int | None = None,
    auto_prune: bool | None = None,
    channel: discord.TextChannel | None = None,
) -> None:
    if not await _check_permission(interaction, REQUIRED_LEVEL):
        return
    if remind_after_hours is not None:
        _state["remind_after_hours"] = max(1, remind_after_hours)
    if retention_days is not None:
        _state["retention_days"] = max(1, retention_days)
    if auto_prune is not None:
        _state["auto_prune"] = auto_prune
    if channel is not None:
        _state["discord_channel_id"] = channel.id
    _save_state()
    await last_command.callback(interaction)
