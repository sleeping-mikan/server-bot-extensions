"""
scheduled-backup — 一定間隔でサーバーを停止 → バックアップ → 起動する拡張機能。

コアBotの `/backup create` はサーバーが停止中でないと実行できない
(`bot/commands/backup.py` の `is_running_server` チェックで弾かれる)。
そのため稼働中のサーバーを定期的にバックアップするには、停止・バックアップ・
起動を順番に手動実行する必要があり、これを自動化するのがこの拡張の役割。

Discordコマンド層を経由せず、`server/control.py` の `start_server` / `stop_server`
と `server/backup.py` の `create_backup` を直接呼ぶ(`/start` `/stop` `/backup create`
と全く同じ実装を使うため挙動が完全に一致する)。停止後は `/stop` と同じく
`ctx.server_process.is_stopped()` を毎秒ポーリングして完了を待つ。

Minecraft以外のサーバーでも使えるよう、停止前に**サーバーのstdinへ**送る警告コマンドは
Minecraft固有の "say" 等をハードコードせず、state.json 側の任意文字列
(既定は空文字列 = 送信しない)として扱う。設定名は server_warning_command /
server_warning_minutes_before とし、Discord通知(notify_discord / discord_channel_id)
と紛れないようにしている。

サイクル実行時にサーバーが既に停止していた場合、バックアップ自体は変わらず実行する
(create_backup はファイルコピーするだけでサーバー状態を問わないため、停止中を理由に
バックアップまで見送る理由が無い)。ただし起動は行わない(管理者が意図的に停止している
可能性があるため、拡張側から無条件に起動はしない)。

次回実行時刻は「前回サイクルが完了した時刻」ではなく「本来狙っていた理想スロット
(trigger_time)」を起点に計算する(_next_anchor 参照)。サイクルの所要時間(停止待ち+
バックアップ+起動)をそのままインターバルに上乗せしてしまうと、5分間隔のはずが実質6分
間隔になり、それが繰り返されて理想スケジュールから際限なくずれ続けてしまうため。処理が
interval 以内に終わった通常時は起点がずれず、長引いた場合のみ取りこぼした分のスロットを
飛ばして次に到来する未来のスロットへ合わせる(2回連続実行はしない)。

コマンドの権限レベルは state.json ではなく .config の
discord_commands.permission.commands_level で管理する(rcon拡張と同じ方式)。
それ以外の設定(間隔・対象・通知・警告)は他拡張と同じく state.json に保存する。

登録される全コマンド: /extension-scheduled-backup <status|run-now|config|show-config>
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from bot.client import client
from bot.embeds import ModifiedEmbeds
from bot.extensions import append_task, write_server_in
from bot.utils import not_enough_permission, print_user, rewrite_config, user_permission
from core.path_utils import is_path_within_scope
from core.state import ctx
from server.backup import ProgressCallback, create_backup
from server.control import StartResult, StopResult, start_server, stop_server

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

# rcon拡張と同じ方式: 権限レベルは .config の commands_level に登録する(唯一の定義元)
_KNOWN_PERMISSIONS: dict[str, int] = {
    "extension-scheduled-backup status": 0,
    "extension-scheduled-backup run-now": 2,
    "extension-scheduled-backup config": 2,
    "extension-scheduled-backup show-config": 0,
}

_STATE_FILE = Path(__file__).parent / "state.json"
_DEFAULT_STATE = {
    "enabled": False,
    "interval_minutes": 360,
    # ctx.server_path 基準の相対パス。空文字なら server_path 全体をバックアップ対象にする
    "target": "",
    "notify_discord": True,
    "discord_channel_id": None,
    # 以下2つは「サーバーのstdinへ送る」設定。Discordへの通知(notify_discord/discord_channel_id)とは別物
    "server_warning_minutes_before": 5,
    # 停止の server_warning_minutes_before 分前にサーバーstdinへ送る任意コマンド。
    # 空文字なら送信しない(Minecraft固有の"say"等を決め打ちしないための設計)
    "server_warning_command": "",
    "stop_timeout_seconds": 60,
    # 直近に実際に完了したサイクル(バックアップのみ実行した場合を含む)の時刻。
    # 一度も実行していなければ None (「前回実行: なし」と表示する)
    "last_run_ts": None,
    # enabled が True になった時刻。初回実行前の「次回予定」はこの起点 + interval_minutes で計算する
    "schedule_anchor_ts": None,
    # /config の next_run_in_minutes で明示指定した「次回」の時刻。設定されていれば
    # last_run_ts/schedule_anchor_ts より優先され、実際にその回が実行されたら None に戻る
    "next_run_override_ts": None,
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
if _state["enabled"] and _state["last_run_ts"] is None and _state["schedule_anchor_ts"] is None:
    # 有効なのに一度も実行しておらず起点も無い(state.jsonを直接編集した/旧バージョンからの移行等)
    # 場合のみ、ロード時刻を起点として補完する。通常は /config で enabled:true にした時点で
    # 設定される(config_command 参照)ため、ここを通るのは異常系のフォールバックのみ。
    _state["schedule_anchor_ts"] = datetime.now().isoformat()
    _save_state()

_warning_sent = False
_cycle_running = False


def _register_missing_permission_keys() -> None:
    """.config に無いこの拡張の権限キーを _KNOWN_PERMISSIONS の値で登録し、即座に .config へ書き戻す。

    拡張のロードは main.py の起動シーケンス中、イベントループが始まる前の
    同期フェーズで行われるため、rcon拡張と同じく asyncio.run(rewrite_config()) を
    ロード時点で直接呼んでも安全(rcon拡張での実機検証済みパターンを踏襲)。
    """
    added = False
    for key, default in _KNOWN_PERMISSIONS.items():
        if key not in ctx.text.command_permission:
            ctx.text.command_permission[key] = default
            added = True
    if added:
        logger.info("registered missing scheduled-backup permission keys, writing to .config")
        asyncio.run(rewrite_config())


_register_missing_permission_keys()


def _perm(key: str) -> int:
    return ctx.text.command_permission.get(key, _KNOWN_PERMISSIONS[key])


def _last_run() -> datetime | None:
    """直近に実際に完了したサイクルの時刻。一度も実行していなければ None。"""
    return datetime.fromisoformat(_state["last_run_ts"]) if _state["last_run_ts"] else None


def _next_run() -> datetime | None:
    """次回実行予定時刻。無効化中、または起点が全く無い異常系では None。

    優先順位: next_run_override_ts(/config next_run_in_minutes で明示指定) >
    last_run_ts + interval(既に一度実行済み) > schedule_anchor_ts + interval(初回実行前)。
    """
    if not _state["enabled"]:
        return None
    if _state["next_run_override_ts"]:
        return datetime.fromisoformat(_state["next_run_override_ts"])
    anchor = _state["last_run_ts"] or _state["schedule_anchor_ts"]
    if anchor is None:
        return None
    return datetime.fromisoformat(anchor) + timedelta(minutes=_state["interval_minutes"])


def _next_anchor(trigger_time: datetime, interval: timedelta, completion: datetime) -> datetime:
    """サイクル完了後、次回の起点(last_run_ts)として使う時刻を求める。

    理想グリッド(trigger_time, trigger_time+interval, trigger_time+2*interval, ...)上で
    completion より後になる直近のスロットを探し、その1つ前を返す。こうすると
    _next_run() = 返り値 + interval が「completion より後になる最初のスロット」になる。

    処理が interval 以内に終わった通常時は trigger_time がそのまま返る(=起点はずれない)。
    処理が長引いて次のスロットも過ぎてしまった場合のみ、取りこぼした分を飛ばして
    「次に到来する未来のスロット」に合わせる。これをしないと、サイクル自体の所要時間が
    毎回そのままインターバルに上乗せされ、理想スケジュールから際限なくずれ続けてしまう
    (例: 5分間隔でもバックアップに1分かかれば実質6分間隔になり、それが繰り返される)。
    """
    slot = trigger_time + interval
    while slot <= completion:
        slot += interval
    return slot - interval


async def _notify(text: str) -> None:
    if not _state["notify_discord"]:
        return
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


def _target_path() -> str:
    return str(ctx.server_path / _state["target"]) if _state["target"] else str(ctx.server_path)


async def _run_cycle(progress: ProgressCallback | None = None, trigger_time: datetime | None = None) -> str:
    """停止 → バックアップ → 起動のサイクルを1回実行する。結果を短い文字列で返す。

    trigger_time: このサイクルが本来狙っていた理想スロット(スケジューラ起動時のみ渡される)。
    /run-now による手動実行では None のままでよい(手動実行はその時点を起点にリセットする)。
    """
    global _cycle_running, _warning_sent

    if _cycle_running:
        return "already_running"
    if ctx.is_backup_in_progress:
        return "backup_in_progress"

    _cycle_running = True
    try:
        was_already_stopped = ctx.server_process.is_stopped()

        if not was_already_stopped:
            logger.info("scheduled backup cycle started (stopping server)")
            stop_result = stop_server()
            if stop_result == StopResult.SUCCESS:
                for _ in range(_state["stop_timeout_seconds"]):
                    if ctx.server_process.is_stopped():
                        break
                    await asyncio.sleep(1)
                if not ctx.server_process.is_stopped():
                    logger.error("server did not stop in time, aborting cycle without backup")
                    await _notify(
                        "⚠️ サーバーの停止がタイムアウトしたため定期バックアップを中止しました。"
                        "手動で状態を確認してください。"
                    )
                    return "stop_timeout"
        else:
            logger.info("scheduled backup cycle started (server already stopped, backup only)")

        from_path = _target_path()
        dst = await create_backup(from_path, on_progress=progress)
        logger.info(f"scheduled backup done -> {dst}")

        if was_already_stopped:
            # 管理者が意図的に停止している可能性があるため、拡張側から無条件に起動はしない。
            # バックアップ自体は停止中でも問題なく行えるため、こちらは通常通り実行する。
            await _notify(f"✅ サーバー停止中でしたが、そのままバックアップを実行しました(起動はしていません)\n`{dst}`")
            return "backup_only_was_stopped"

        start_result = start_server(ctx.server_logger)
        if start_result != StartResult.SUCCESS:
            logger.error(f"failed to restart server after backup: {start_result}")
            await _notify(
                f"⚠️ バックアップは完了しましたが、サーバーの再起動に失敗しました ({start_result.name})。"
                "手動で /start を実行してください。\n"
                f"`{dst}`"
            )
            return "backup_ok_start_failed"

        await _notify(f"✅ 定期バックアップが完了しました\n`{dst}`")
        return "success"
    finally:
        completion = datetime.now()
        if trigger_time is not None:
            anchor = _next_anchor(trigger_time, timedelta(minutes=_state["interval_minutes"]), completion)
        else:
            anchor = completion
        _state["last_run_ts"] = anchor.isoformat()
        _state["next_run_override_ts"] = None
        _save_state()
        _warning_sent = False
        _cycle_running = False


@tasks.loop(minutes=1)
async def _cycle_loop() -> None:
    global _warning_sent

    if not _state["enabled"] or _cycle_running:
        return

    next_run = _next_run()
    if next_run is None:
        return
    remaining = (next_run - datetime.now()).total_seconds()

    warn_seconds = _state["server_warning_minutes_before"] * 60
    if (
        _state["server_warning_command"]
        and not _warning_sent
        and 0 < remaining <= warn_seconds
        and not ctx.server_process.is_stopped()
    ):
        _warning_sent = True
        ok, reason = write_server_in(_state["server_warning_command"])
        if not ok:
            logger.info(f"pre-backup warning skipped ({reason})")
        else:
            logger.info("pre-backup warning sent")

    if remaining <= 0:
        await _run_cycle(trigger_time=next_run)


append_task(_cycle_loop)


async def _check_permission(interaction: discord.Interaction, required: int) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < required:
        await not_enough_permission(interaction, logger)
        return False
    return True


def _status_embed() -> ModifiedEmbeds.DefaultEmbed:
    embed = ModifiedEmbeds.DefaultEmbed(title="定期バックアップ設定")
    embed.add_field(name="有効", value="はい" if _state["enabled"] else "いいえ", inline=True)
    embed.add_field(name="間隔", value=f"{_state['interval_minutes']}分ごと", inline=True)
    embed.add_field(name="対象", value=f"`{_target_path()}`", inline=False)
    if _state["enabled"]:
        next_run = _next_run()
        embed.add_field(name="次回予定", value=next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "算出中", inline=True)
    last_run = _last_run()
    embed.add_field(name="前回実行", value=last_run.strftime("%Y-%m-%d %H:%M:%S") if last_run else "なし(未実行)", inline=True)
    embed.add_field(
        name="サーバーへの事前警告(stdin)",
        value=(
            f"{_state['server_warning_minutes_before']}分前に `{_state['server_warning_command']}`"
            if _state["server_warning_command"]
            else "送信しない"
        ),
        inline=False,
    )
    embed.add_field(
        name="Discordへの通知",
        value=("有効" + (f" (<#{_state['discord_channel_id']}>)" if _state["discord_channel_id"] else " (チャンネル未設定)")) if _state["notify_discord"] else "無効",
        inline=False,
    )
    return embed


@tree.command(name="status", description="定期バックアップの設定と次回予定を表示する")
async def status_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, _perm("extension-scheduled-backup status")):
        return
    await interaction.response.send_message(embed=_status_embed())


def _config_values_embed() -> ModifiedEmbeds.DefaultEmbed:
    """_status_embed と同じ「日本語ラベル+フィールドを並べる」作法で、/config の各引数に
    対応する現在値を表示する読み取り専用embed。ラベル文言も _status_embed と揃える
    (有効/間隔/対象/Discordへの通知は _status_embed と同じ表記を使う)。
    stop_timeout_seconds 等、_status_embed には出てこない項目も含め全項目を表示する。"""
    embed = ModifiedEmbeds.DefaultEmbed(title="定期バックアップ設定 (config引数形式)")
    embed.add_field(name="有効", value="はい" if _state["enabled"] else "いいえ", inline=True)
    embed.add_field(name="間隔", value=f"{_state['interval_minutes']}分ごと", inline=True)
    embed.add_field(name="対象", value=_state["target"] or "-", inline=True)
    embed.add_field(name="Discordへの通知", value="有効" if _state["notify_discord"] else "無効", inline=True)
    embed.add_field(name="通知先チャンネル", value=f"<#{_state['discord_channel_id']}>" if _state["discord_channel_id"] else "-", inline=True)
    embed.add_field(name="事前警告(何分前)", value=f"{_state['server_warning_minutes_before']}分前", inline=True)
    embed.add_field(name="事前警告コマンド(stdin)", value=_state["server_warning_command"] or "-", inline=True)
    embed.add_field(name="停止待機秒数", value=f"{_state['stop_timeout_seconds']}秒", inline=True)
    return embed


@tree.command(name="show-config", description="現在の設定値を/configと同じ項目名でそのまま表示する(変更は行わない)")
async def show_config_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, _perm("extension-scheduled-backup show-config")):
        return
    await interaction.response.send_message(embed=_config_values_embed())


def _make_progress_callback(interaction: discord.Interaction, embed: ModifiedEmbeds.DefaultEmbed) -> ProgressCallback:
    async def on_progress(copied: int, total: int, copied_bytes: int, total_bytes: int) -> None:
        send_sens = max(1, total // 20)
        if copied % send_sens != 0 and copied != total:
            return
        bar_width = 30
        ratio = copied / total if total else 0
        filled = max(0, int(ratio * bar_width) - 1)
        bar = "=" * filled
        space = "-" * (bar_width - filled - 1)
        embed.clear_fields()
        embed.add_field(
            name="バックアップ中" if copied != total else "バックアップ完了",
            value=f"```{bar}☆{space}\n{copied:5} / {total:5} ({copied_bytes / 1024 ** 3:.2f} / {total_bytes / 1024 ** 3:.2f} GB)```",
            inline=False,
        )
        await interaction.edit_original_response(embed=embed)

    return on_progress


@tree.command(name="run-now", description="スケジュールを待たず今すぐ 停止→バックアップ→起動 のサイクルを実行する")
async def run_now_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, _perm("extension-scheduled-backup run-now")):
        return
    if _cycle_running:
        embed = ModifiedEmbeds.ErrorEmbed(title="既にサイクルが実行中です")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = ModifiedEmbeds.DefaultEmbed(title="定期バックアップサイクルを開始します")
    await interaction.response.send_message(embed=embed)
    progress = _make_progress_callback(interaction, embed)
    result = await _run_cycle(progress=progress)

    result_text = {
        "success": "✅ 完了しました",
        "backup_only_was_stopped": "✅ サーバー停止中でしたが、そのままバックアップを実行しました(起動なし)",
        "stop_timeout": "⚠️ サーバーの停止がタイムアウトしたため中止しました",
        "backup_ok_start_failed": "⚠️ バックアップは完了しましたが、サーバーの再起動に失敗しました",
        "backup_in_progress": "⚠️ 他のバックアップ処理が進行中のため中止しました",
        "already_running": "⚠️ 既にサイクルが実行中です",
    }.get(result, result)
    embed.clear_fields()
    embed.add_field(name="結果", value=result_text, inline=False)
    await interaction.edit_original_response(embed=embed)


@tree.command(name="config", description="定期バックアップの間隔・対象・通知・事前警告を設定する")
@app_commands.describe(
    enabled="定期サイクルを有効にするか",
    interval_minutes="停止→バックアップ→起動を実行する間隔(分)",
    target="バックアップ対象。server_path基準の相対パス(空欄でserver_path全体)",
    notify_discord="サイクルの結果をDiscordへ通知するか",
    channel="Discord通知の送信先チャンネル(未指定時は現状維持、未設定なら実行チャンネル)",
    server_warning_minutes_before="サーバー停止の何分前に警告コマンドを送るか",
    server_warning_command="停止の直前にサーバーのstdinへ送るコマンド(Discordには送らない。空文字で送信しない)",
    stop_timeout_seconds="停止コマンド送信後、完了をここで指定した秒数まで待つ",
    next_run_in_minutes="次回1回だけ、今から何分後に実行するかを指定する(その回が終わると通常の間隔に戻る)",
)
async def config_command(
    interaction: discord.Interaction,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
    target: str | None = None,
    notify_discord: bool | None = None,
    channel: discord.TextChannel | None = None,
    server_warning_minutes_before: int | None = None,
    server_warning_command: str | None = None,
    stop_timeout_seconds: int | None = None,
    next_run_in_minutes: int | None = None,
) -> None:
    if not await _check_permission(interaction, _perm("extension-scheduled-backup config")):
        return

    if target is not None:
        candidate = str(ctx.server_path / target) if target else str(ctx.server_path)
        if not is_path_within_scope(candidate):
            embed = ModifiedEmbeds.ErrorEmbed(title="target が server_path の範囲外です", description=f"`{candidate}`")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        _state["target"] = target

    if next_run_in_minutes is not None:
        if next_run_in_minutes < 0:
            embed = ModifiedEmbeds.ErrorEmbed(title="next_run_in_minutes は0以上を指定してください")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        _state["next_run_override_ts"] = (datetime.now() + timedelta(minutes=next_run_in_minutes)).isoformat()

    if enabled is not None:
        _state["enabled"] = enabled
        # 一度も実行しておらず(このコマンド内で last_backup_minutes_ago を指定した場合も除く)、
        # next_run_in_minutes による明示指定も無い場合のみ、「有効にした今この瞬間」を
        # 初回実行までのカウントダウンの起点にする
        if enabled and _state["last_run_ts"] is None and _state["next_run_override_ts"] is None:
            _state["schedule_anchor_ts"] = datetime.now().isoformat()
    if interval_minutes is not None:
        _state["interval_minutes"] = max(1, interval_minutes)
    if notify_discord is not None:
        _state["notify_discord"] = notify_discord
    if channel is not None:
        _state["discord_channel_id"] = channel.id
    if server_warning_minutes_before is not None:
        _state["server_warning_minutes_before"] = max(0, server_warning_minutes_before)
    if server_warning_command is not None:
        _state["server_warning_command"] = server_warning_command
    if stop_timeout_seconds is not None:
        _state["stop_timeout_seconds"] = max(1, stop_timeout_seconds)

    # 通知は有効なのにチャンネルが未設定のままだと _notify() が黙ってスキップし続けるだけになるため、
    # 既存設定が無い場合に限りこのコマンドを実行したチャンネルへ自動でフォールバックする
    # (既に設定済みのチャンネルがあれば上書きしない)
    if _state["notify_discord"] and _state["discord_channel_id"] is None and interaction.channel is not None:
        _state["discord_channel_id"] = interaction.channel.id

    _save_state()
    await interaction.response.send_message(embed=_status_embed())
