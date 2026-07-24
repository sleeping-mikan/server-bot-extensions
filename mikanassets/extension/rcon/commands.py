"""
rcon — Minecraft の RCON プロトコルで直接コマンドを送り、確実にその応答を受け取る拡張機能。

## なぜRCONが必要か

このBotの既存コマンドは、サーバーの応答を「コマンド実行直後に次に届いた標準出力の1行」
として扱っている。実装は bot/commands/cmd.py の /cmd serverin と server/stdout.py の
中継 (ctx.is_back_discord / ctx.cmd_logs) で、コマンド送信直前に is_back_discord = True
にしてから最大3秒間 cmd_logs をポーリングし、次に来た行を無条件に「その結果」として
返している。これはサーバーの他のログ出力(他プレイヤーの発言、別スレッドのログ等)が
たまたま割り込むと誤った行を返し、複数行にまたがる応答は先頭行しか拾えず、応答が
3秒以内に来なければ黙ってタイムアウトする、という正確性の問題を抱えている。

拡張機能側の write_server_in() に至っては応答を一切拾わず fire-and-forget で
stdin に書き込むだけ (bot/extensions.py)。

RCON (Source RCON プロトコル、Minecraft Java サーバーが標準対応) は、送ったコマンドと
その実行結果が同じTCP往復の中で1対1に対応する専用プロトコルなので、上記のような
「たまたま次に出た行」問題が原理的に起こらない。本拡張は標準ライブラリの asyncio/struct
のみでRCONクライアントを実装し(追加のpipインストール不要)、確実な結果取得を提供する。

## 前提: RCONの有効化

サーバーの server.properties で以下を設定し、サーバーを再起動しておくこと。

    enable-rcon=true
    rcon.port=25575
    rcon.password=<空でない値>

/extension-rcon check でこの設定状況を確認できる。

## 権限レベル

各コマンドの要求権限レベルは、拡張機能側の state.json ではなく **.config** の
discord_commands.permission.commands_level に "rcon <サブコマンド名>": <レベル> という
キーで管理する。これはコアBotの他のコマンド(stop, backup apply 等)と全く同じ
場所・同じ形式。デフォルト値は _KNOWN_PERMISSIONS にまとめてあり(下記)、
拡張ロード時に .config にまだ無いキーがあれば自動的にこのデフォルト値で
書き足し、その場で .config ファイルへ即座に反映する(詳細は
_register_missing_permission_keys() / _perm() を参照)。つまり管理者は
.config を開けば "rcon cmd" 等のキーが既に存在した状態になっており、
値を書き換えるだけでよい(何もしなければデフォルトのまま動く)。

    rcon check              0
    rcon config              2
    rcon cmd                 2  (任意コマンドの無条件実行)
    rcon list                0
    rcon whitelist list      0
    rcon gamemode/weather/time/difficulty/say/tp/give/xp/summon/setblock/title/
    rcon effect give/clear/whitelist add/remove                              1
    rcon kill                2
    rcon player ban/pardon/kick/op/deop                                       2

## コマンド一覧 (全て /extension-rcon <name> で呼び出す)

- check                          RCON設定状況とサーバーへの疎通を確認する
- config                         RCON接続/応答のタイムアウト秒数を設定する (要上位権限)
- cmd <command>                  任意のコマンドをそのまま送信する (要上位権限)
- list                           オンラインプレイヤー一覧 (/list)
- gamemode <mode> [selector]     ゲームモード変更 (selector省略時は @a)
- weather <condition> [seconds]  天候変更
- time <action> <value>          時刻変更 (set: day/noon/night/midnight/tick数, add: tick数)
- difficulty <level>             難易度変更
- say <message>                  サーバー内一斉送信
- tp <selector> <x> <y> <z>      座標へテレポート
- give <selector> <item> [count] アイテム付与
- kill <selector>                対象を殺す
- xp <selector> <amount> [unit]  経験値付与 (unit: points/levels)
- summon <entity> [x] [y] [z]    エンティティ召喚
- setblock <x> <y> <z> <block>   ブロック設置
- title <selector> <text>        タイトル表示
- effect give/clear              エフェクト付与/解除
- whitelist add/remove/list      ホワイトリスト操作
- player ban/pardon/kick/op/deop 対象への管理操作 (要上位権限)

execute チェイン(as/at/if等)専用のエイリアスは用意していない。cmd がRCONへの
生コマンド送信そのものなので、`cmd command:"execute as @a at @s run say hi"` の
ように execute チェインをそのまま渡せば足りるため、専用の execute グループは冗長と
判断し実装しなかった(詳細は plan.json の rcon エントリの notes を参照)。
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

from bot.utils import not_enough_permission, print_user, rewrite_config, user_permission
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

# rcon の各コマンドが要求する権限レベルのデフォルト値(唯一の定義元)。
# .config の discord_commands.permission.commands_level に同名キーが無ければ
# ここに登録し、そのままファイルへも書き戻す(_register_missing_permission_keys 参照)。
_KNOWN_PERMISSIONS: dict[str, int] = {
    "rcon check": 0,
    "rcon config": 2,
    "rcon cmd": 2,
    "rcon list": 0,
    "rcon gamemode": 1,
    "rcon weather": 1,
    "rcon time": 1,
    "rcon difficulty": 1,
    "rcon say": 1,
    "rcon tp": 1,
    "rcon give": 1,
    "rcon kill": 2,
    "rcon xp": 1,
    "rcon summon": 1,
    "rcon setblock": 1,
    "rcon title": 1,
    "rcon effect give": 1,
    "rcon effect clear": 1,
    "rcon whitelist add": 1,
    "rcon whitelist remove": 1,
    "rcon whitelist list": 0,
    "rcon player ban": 2,
    "rcon player pardon": 2,
    "rcon player kick": 2,
    "rcon player op": 2,
    "rcon player deop": 2,
}

_STATE_FILE = Path(__file__).parent / "state.json"
_DEFAULT_STATE = {"timeout_seconds": 5.0}
# 各コマンドの要求権限レベルは state.json ではなく .config 側(_KNOWN_PERMISSIONS / _perm() 参照)で管理する


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


# ── RCON クライアント (Source RCON プロトコル、標準ライブラリのみ) ──────────────
# 参考: https://wiki.vg/RCON (Minecraftはこのサブセットを実装している)

_PKT_RESPONSE_OR_EXEC = 0  # SERVERDATA_RESPONSE_VALUE / SERVERDATA_EXECCOMMAND
_PKT_AUTH_RESPONSE = 2  # SERVERDATA_AUTH_RESPONSE (== EXECCOMMANDと同値だが用途で区別)
_PKT_AUTH = 3  # SERVERDATA_AUTH


class RconError(Exception):
    """RCON通信全般のエラー。"""


class RconAuthError(RconError):
    """RCONのパスワード認証に失敗した。"""


async def _rcon_write_packet(writer: asyncio.StreamWriter, request_id: int, pkt_type: int, payload: str) -> None:
    body = struct.pack("<ii", request_id, pkt_type) + payload.encode("utf-8") + b"\x00\x00"
    writer.write(struct.pack("<i", len(body)) + body)
    await writer.drain()


async def _rcon_read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    (length,) = struct.unpack("<i", await reader.readexactly(4))
    data = await reader.readexactly(length)
    request_id, pkt_type = struct.unpack("<ii", data[:8])
    payload = data[8:-2].decode("utf-8", errors="replace")
    return request_id, pkt_type, payload


async def rcon_execute(host: str, port: int, password: str, command: str, timeout: float) -> str:
    """RCON でコマンドを実行し、サーバーからの応答文字列を返す。"""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    try:
        await _rcon_write_packet(writer, 1, _PKT_AUTH, password)
        auth_id, _, _ = await asyncio.wait_for(_rcon_read_packet(reader), timeout=timeout)
        if auth_id == -1:
            raise RconAuthError("authentication failed (check rcon.password)")

        await _rcon_write_packet(writer, 2, _PKT_RESPONSE_OR_EXEC, command)
        _, _, payload = await asyncio.wait_for(_rcon_read_packet(reader), timeout=timeout)
        return payload
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ── server.properties からのRCON接続情報取得 ──────────────────────────────────

def _read_server_properties() -> dict[str, str]:
    path = ctx.server_path / "server.properties"
    props: dict[str, str] = {}
    if not path.exists():
        return props
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


def _rcon_connection_info() -> tuple[bool, int, str]:
    """(enabled, port, password) を server.properties から読み取る。BotとMCサーバーは
    同一マシン上で動く前提のため接続先ホストは常に127.0.0.1固定。"""
    props = _read_server_properties()
    enabled = props.get("enable-rcon", "false").lower() == "true"
    try:
        port = int(props.get("rcon.port", "25575") or 25575)
    except ValueError:
        port = 25575
    password = props.get("rcon.password", "")
    return enabled, port, password


# ── Discordコマンドから共通で呼ぶヘルパー ────────────────────────────────────

def _sanitize_text(text: str) -> str:
    # RCONは1コマンド1パケットなのでwrite_server_inのような改行注入は原理的に起きないが、
    # say/title等の自由入力は防御的に改行を除去しておく。
    return " ".join(text.splitlines()).strip()


def _register_missing_permission_keys() -> None:
    """.config に無い rcon の権限キーを _KNOWN_PERMISSIONS の値で登録し、即座に .config へ書き戻す。

    コアBotは core/config_loader.py が起動時に INITIAL_COMMAND_PERMISSION の
    未登録キーを .config へ補完しているが、これは対象がコア command_desc に
    登録されたコマンドに限られ、拡張機能のキーは補完されない。同じ体験(.configを
    開けばキーが既に存在し、値を書き換えるだけでよい)を拡張機能側でも再現するため、
    ロード時に不足しているキーを ctx.text.command_permission (= .config の
    commands_level と同一のdict) へ直接書き込む。

    拡張のロード (bot/setup.py の setup_commands() -> bot/extensions.py の load())
    は main.py の起動シーケンス中、client.run() でDiscordのイベントループが
    始まる前の完全に同期的なフェーズで行われる(main.py: 151行目の
    asyncio.run(load_text()) は setup_commands() 呼び出し時点で既に完了・
    クローズ済み)。つまりこの時点で実行中のイベントループは存在しないため、
    async def だが中身は同期的なファイル書き込みでしかない rewrite_config() を
    asyncio.run() で問題なく呼べる。よってコマンド実行を待たず、ロード完了と
    同時に .config へ反映する。
    """
    added = False
    for key, default in _KNOWN_PERMISSIONS.items():
        if key not in ctx.text.command_permission:
            ctx.text.command_permission[key] = default
            added = True
    if added:
        logger.info("registered missing rcon permission keys, writing to .config")
        asyncio.run(rewrite_config())


_register_missing_permission_keys()


def _perm(key: str) -> int:
    """コマンドごとの要求権限レベルを .config から読む(未登録キーは有り得ない前提)。

    _register_missing_permission_keys() でロード時に .config 側へ全キーを
    登録済みのため、通常はここで KeyError は起きない。デフォルト値の定義元は
    _KNOWN_PERMISSIONS 一箇所のみ(呼び出し側に数値を重複させない)。
    """
    return ctx.text.command_permission.get(key, _KNOWN_PERMISSIONS[key])


async def _check_permission(interaction: discord.Interaction, required: int) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < required:
        await not_enough_permission(interaction, logger)
        return False
    return True


async def _run(interaction: discord.Interaction, command: str, *, required: int) -> None:
    """権限チェック → RCON実行 → 結果をembedで返す共通処理。"""
    if not await _check_permission(interaction, required):
        return

    enabled, port, password = _rcon_connection_info()
    if not enabled or not password:
        await interaction.response.send_message(
            "RCONが有効になっていません。server.properties の `enable-rcon=true` と "
            "`rcon.password` を設定し、サーバーを再起動してから再度お試しください。"
            "(`/extension-rcon check` で現在の設定状況を確認できます)",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    try:
        result = await rcon_execute("127.0.0.1", port, password, command, timeout=_state["timeout_seconds"])
    except RconAuthError:
        logger.error("rcon auth failed")
        await interaction.followup.send("RCON認証に失敗しました (rcon.password を確認してください)")
        return
    except asyncio.TimeoutError:
        logger.error(f"rcon timeout -> {command!r}")
        await interaction.followup.send("RCON接続がタイムアウトしました。サーバーが起動しているか確認してください。")
        return
    except OSError as e:
        logger.error(f"rcon connection failed ({e}) -> {command!r}")
        await interaction.followup.send(f"RCONへの接続に失敗しました ({e})")
        return

    logger.info(f"rcon -> {command!r} -> {result!r}")
    embed = discord.Embed(title=f"/{command}", color=discord.Color.green())
    embed.add_field(name="結果", value=f"```{(result or '(応答なし)')[:1000]}```", inline=False)
    await interaction.followup.send(embed=embed)


# ── check ────────────────────────────────────────────────────────────────────

@tree.command(name="check", description="RCONの設定状況と疎通を確認する")
async def check_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, _perm("rcon check")):
        return
    enabled, port, password = _rcon_connection_info()

    embed = discord.Embed(title="RCON設定状況", color=discord.Color.blurple())
    embed.add_field(name="enable-rcon", value=str(enabled), inline=True)
    embed.add_field(name="rcon.port", value=str(port), inline=True)
    embed.add_field(name="rcon.password", value="設定済み" if password else "未設定", inline=True)

    if not enabled or not password:
        embed.add_field(
            name="疎通確認",
            value="server.properties の enable-rcon / rcon.password を設定しサーバーを再起動してください",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.defer()
    try:
        await rcon_execute("127.0.0.1", port, password, "list", timeout=_state["timeout_seconds"])
        embed.add_field(name="疎通確認", value="✅ 接続・認証に成功しました", inline=False)
    except RconAuthError:
        embed.add_field(name="疎通確認", value="❌ 認証失敗 (rcon.password不一致)", inline=False)
    except (asyncio.TimeoutError, OSError) as e:
        embed.add_field(name="疎通確認", value=f"❌ 接続失敗 ({e})", inline=False)
    await interaction.followup.send(embed=embed)


@tree.command(name="config", description="RCON接続/応答のタイムアウト秒数を設定する(要上位権限)")
@app_commands.describe(timeout_seconds="RCON接続/応答のタイムアウト秒数")
async def config_command(interaction: discord.Interaction, timeout_seconds: float) -> None:
    if not await _check_permission(interaction, _perm("rcon config")):
        return

    _state["timeout_seconds"] = max(0.5, timeout_seconds)
    _save_state()

    embed = discord.Embed(title="rcon 設定", color=discord.Color.blurple())
    embed.add_field(name="timeout_seconds", value=str(_state["timeout_seconds"]), inline=True)
    embed.set_footer(text="権限レベルは .config の discord_commands.permission.commands_level で設定してください")
    await interaction.response.send_message(embed=embed)


# ── cmd (raw) ────────────────────────────────────────────────────────────────

@tree.command(name="cmd", description="任意のコマンドをRCON経由で実行する(要上位権限)")
async def cmd_command(interaction: discord.Interaction, command: str) -> None:
    await _run(interaction, command.strip(), required=_perm("rcon cmd"))


# ── list ─────────────────────────────────────────────────────────────────────

@tree.command(name="list", description="オンラインプレイヤー一覧を表示する")
async def list_command(interaction: discord.Interaction) -> None:
    await _run(interaction, "list", required=_perm("rcon list"))


# ── gamemode / weather / time / difficulty / say ────────────────────────────

@tree.command(name="gamemode", description="ゲームモードを変更する(selector省略時は@a=全員が対象)")
async def gamemode_command(
    interaction: discord.Interaction,
    mode: Literal["survival", "creative", "adventure", "spectator"],
    selector: str = "@a",
) -> None:
    await _run(interaction, f"gamemode {mode} {selector}", required=_perm("rcon gamemode"))


@tree.command(name="weather", description="天候を変更する")
async def weather_command(
    interaction: discord.Interaction,
    condition: Literal["clear", "rain", "thunder"],
    seconds: int | None = None,
) -> None:
    command = f"weather {condition}" + (f" {seconds}" if seconds is not None else "")
    await _run(interaction, command, required=_perm("rcon weather"))


@tree.command(name="time", description="時刻を変更する")
async def time_command(
    interaction: discord.Interaction,
    action: Literal["set", "add"],
    value: str,
) -> None:
    await _run(interaction, f"time {action} {value}", required=_perm("rcon time"))


@tree.command(name="difficulty", description="難易度を変更する")
async def difficulty_command(
    interaction: discord.Interaction,
    level: Literal["peaceful", "easy", "normal", "hard"],
) -> None:
    await _run(interaction, f"difficulty {level}", required=_perm("rcon difficulty"))


@tree.command(name="say", description="サーバー内に一斉送信する")
async def say_command(interaction: discord.Interaction, message: str) -> None:
    sanitized = _sanitize_text(message)
    if not sanitized:
        await interaction.response.send_message("メッセージが空です", ephemeral=True)
        return
    await _run(interaction, f"say {sanitized}", required=_perm("rcon say"))


# ── tp / give / kill / xp / summon / setblock / title ───────────────────────

@tree.command(name="tp", description="指定座標へテレポートする")
async def tp_command(interaction: discord.Interaction, selector: str, x: float, y: float, z: float) -> None:
    await _run(interaction, f"tp {selector} {x} {y} {z}", required=_perm("rcon tp"))


@tree.command(name="give", description="アイテムを付与する")
async def give_command(interaction: discord.Interaction, selector: str, item: str, count: int = 1) -> None:
    await _run(interaction, f"give {selector} {item} {max(1, count)}", required=_perm("rcon give"))


@tree.command(name="kill", description="対象を殺す")
async def kill_command(interaction: discord.Interaction, selector: str) -> None:
    await _run(interaction, f"kill {selector}", required=_perm("rcon kill"))


@tree.command(name="xp", description="経験値を付与する")
async def xp_command(
    interaction: discord.Interaction,
    selector: str,
    amount: int,
    unit: Literal["points", "levels"] = "points",
) -> None:
    await _run(interaction, f"xp add {selector} {amount} {unit}", required=_perm("rcon xp"))


@tree.command(name="summon", description="エンティティを召喚する")
async def summon_command(
    interaction: discord.Interaction,
    entity: str,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
) -> None:
    coords = f" {x} {y} {z}" if x is not None and y is not None and z is not None else ""
    await _run(interaction, f"summon {entity}{coords}", required=_perm("rcon summon"))


@tree.command(name="setblock", description="指定座標にブロックを設置する")
async def setblock_command(interaction: discord.Interaction, x: int, y: int, z: int, block: str) -> None:
    await _run(interaction, f"setblock {x} {y} {z} {block}", required=_perm("rcon setblock"))


@tree.command(name="title", description="対象にタイトルを表示する")
async def title_command(interaction: discord.Interaction, selector: str, text: str) -> None:
    sanitized = _sanitize_text(text)
    await _run(interaction, f'title {selector} title {{"text":"{sanitized}"}}', required=_perm("rcon title"))


# ── effect (サブグループ) ─────────────────────────────────────────────────────

effect_group = app_commands.Group(name="effect", description="ステータスエフェクト操作")


@effect_group.command(name="give", description="エフェクトを付与する")
async def effect_give_command(
    interaction: discord.Interaction,
    selector: str,
    effect: str,
    seconds: int = 30,
    amplifier: int = 0,
) -> None:
    await _run(interaction, f"effect give {selector} {effect} {seconds} {amplifier}", required=_perm("rcon effect give"))


@effect_group.command(name="clear", description="エフェクトを解除する")
async def effect_clear_command(interaction: discord.Interaction, selector: str, effect: str | None = None) -> None:
    await _run(interaction, f"effect clear {selector}" + (f" {effect}" if effect else ""), required=_perm("rcon effect clear"))


tree.add_command(effect_group)


# ── whitelist (サブグループ) ──────────────────────────────────────────────────

whitelist_group = app_commands.Group(name="whitelist", description="ホワイトリスト操作")


@whitelist_group.command(name="add", description="ホワイトリストに追加する")
async def whitelist_add_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"whitelist add {player}", required=_perm("rcon whitelist add"))


@whitelist_group.command(name="remove", description="ホワイトリストから削除する")
async def whitelist_remove_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"whitelist remove {player}", required=_perm("rcon whitelist remove"))


@whitelist_group.command(name="list", description="ホワイトリストを表示する")
async def whitelist_list_command(interaction: discord.Interaction) -> None:
    await _run(interaction, "whitelist list", required=_perm("rcon whitelist list"))


tree.add_command(whitelist_group)


# ── player (サブグループ、対象への管理操作) ───────────────────────────────────

player_group = app_commands.Group(name="player", description="プレイヤーへの管理操作(要上位権限)")


@player_group.command(name="ban", description="対象をBANする")
async def player_ban_command(interaction: discord.Interaction, player: str, reason: str | None = None) -> None:
    command = f"ban {player}" + (f" {_sanitize_text(reason)}" if reason else "")
    await _run(interaction, command, required=_perm("rcon player ban"))


@player_group.command(name="pardon", description="対象のBANを解除する")
async def player_pardon_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"pardon {player}", required=_perm("rcon player pardon"))


@player_group.command(name="kick", description="対象をキックする")
async def player_kick_command(interaction: discord.Interaction, player: str, reason: str | None = None) -> None:
    command = f"kick {player}" + (f" {_sanitize_text(reason)}" if reason else "")
    await _run(interaction, command, required=_perm("rcon player kick"))


@player_group.command(name="op", description="対象をOPにする")
async def player_op_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"op {player}", required=_perm("rcon player op"))


@player_group.command(name="deop", description="対象のOPを解除する")
async def player_deop_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"deop {player}", required=_perm("rcon player deop"))


tree.add_command(player_group)

