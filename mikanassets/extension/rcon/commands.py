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

## コマンド一覧 (全て /extension-rcon <name> で呼び出す)

- check                          RCON設定状況とサーバーへの疎通を確認する
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
- execute run/as/at/if-entity    execute コマンドのラッパー(非再帰・非反復のみ)
- execute custom                 execute の主要修飾子を型付き引数で組み立て(各修飾子1回まで)
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

from bot.utils import not_enough_permission, print_user, user_permission
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

# 通常のゲーム内操作コマンドに要求する最低権限レベル
REQUIRED_LEVEL = 1
# cmd(任意コマンド) と player ban/op 等の破壊的/管理操作に要求する最低権限レベル
REQUIRED_LEVEL_ADMIN = 2

_STATE_FILE = Path(__file__).parent / "state.json"
_DEFAULT_STATE = {"timeout_seconds": 5.0}


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return dict(_DEFAULT_STATE)
    try:
        with _STATE_FILE.open("r", encoding="utf-8") as f:
            return {**_DEFAULT_STATE, **json.load(f)}
    except Exception as e:
        logger.error(f"failed to load state, using defaults ({e})")
        return dict(_DEFAULT_STATE)


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


async def _check_permission(interaction: discord.Interaction, required: int) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < required:
        await not_enough_permission(interaction, logger)
        return False
    return True


async def _run(interaction: discord.Interaction, command: str, *, required: int = REQUIRED_LEVEL) -> None:
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
    await print_user(logger, interaction.user)
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


# ── cmd (raw) ────────────────────────────────────────────────────────────────

@tree.command(name="cmd", description="任意のコマンドをRCON経由で実行する(要上位権限)")
async def cmd_command(interaction: discord.Interaction, command: str) -> None:
    await _run(interaction, command.strip(), required=REQUIRED_LEVEL_ADMIN)


# ── list ─────────────────────────────────────────────────────────────────────

@tree.command(name="list", description="オンラインプレイヤー一覧を表示する")
async def list_command(interaction: discord.Interaction) -> None:
    await _run(interaction, "list", required=0)


# ── gamemode / weather / time / difficulty / say ────────────────────────────

@tree.command(name="gamemode", description="ゲームモードを変更する(selector省略時は@a=全員が対象)")
async def gamemode_command(
    interaction: discord.Interaction,
    mode: Literal["survival", "creative", "adventure", "spectator"],
    selector: str = "@a",
) -> None:
    await _run(interaction, f"gamemode {mode} {selector}")


@tree.command(name="weather", description="天候を変更する")
async def weather_command(
    interaction: discord.Interaction,
    condition: Literal["clear", "rain", "thunder"],
    seconds: int | None = None,
) -> None:
    command = f"weather {condition}" + (f" {seconds}" if seconds is not None else "")
    await _run(interaction, command)


@tree.command(name="time", description="時刻を変更する")
async def time_command(
    interaction: discord.Interaction,
    action: Literal["set", "add"],
    value: str,
) -> None:
    await _run(interaction, f"time {action} {value}")


@tree.command(name="difficulty", description="難易度を変更する")
async def difficulty_command(
    interaction: discord.Interaction,
    level: Literal["peaceful", "easy", "normal", "hard"],
) -> None:
    await _run(interaction, f"difficulty {level}")


@tree.command(name="say", description="サーバー内に一斉送信する")
async def say_command(interaction: discord.Interaction, message: str) -> None:
    sanitized = _sanitize_text(message)
    if not sanitized:
        await interaction.response.send_message("メッセージが空です", ephemeral=True)
        return
    await _run(interaction, f"say {sanitized}")


# ── tp / give / kill / xp / summon / setblock / title ───────────────────────

@tree.command(name="tp", description="指定座標へテレポートする")
async def tp_command(interaction: discord.Interaction, selector: str, x: float, y: float, z: float) -> None:
    await _run(interaction, f"tp {selector} {x} {y} {z}")


@tree.command(name="give", description="アイテムを付与する")
async def give_command(interaction: discord.Interaction, selector: str, item: str, count: int = 1) -> None:
    await _run(interaction, f"give {selector} {item} {max(1, count)}")


@tree.command(name="kill", description="対象を殺す")
async def kill_command(interaction: discord.Interaction, selector: str) -> None:
    await _run(interaction, f"kill {selector}", required=REQUIRED_LEVEL_ADMIN)


@tree.command(name="xp", description="経験値を付与する")
async def xp_command(
    interaction: discord.Interaction,
    selector: str,
    amount: int,
    unit: Literal["points", "levels"] = "points",
) -> None:
    await _run(interaction, f"xp add {selector} {amount} {unit}")


@tree.command(name="summon", description="エンティティを召喚する")
async def summon_command(
    interaction: discord.Interaction,
    entity: str,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
) -> None:
    coords = f" {x} {y} {z}" if x is not None and y is not None and z is not None else ""
    await _run(interaction, f"summon {entity}{coords}")


@tree.command(name="setblock", description="指定座標にブロックを設置する")
async def setblock_command(interaction: discord.Interaction, x: int, y: int, z: int, block: str) -> None:
    await _run(interaction, f"setblock {x} {y} {z} {block}")


@tree.command(name="title", description="対象にタイトルを表示する")
async def title_command(interaction: discord.Interaction, selector: str, text: str) -> None:
    sanitized = _sanitize_text(text)
    await _run(interaction, f'title {selector} title {{"text":"{sanitized}"}}')


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
    await _run(interaction, f"effect give {selector} {effect} {seconds} {amplifier}")


@effect_group.command(name="clear", description="エフェクトを解除する")
async def effect_clear_command(interaction: discord.Interaction, selector: str, effect: str | None = None) -> None:
    await _run(interaction, f"effect clear {selector}" + (f" {effect}" if effect else ""))


tree.add_command(effect_group)


# ── whitelist (サブグループ) ──────────────────────────────────────────────────

whitelist_group = app_commands.Group(name="whitelist", description="ホワイトリスト操作")


@whitelist_group.command(name="add", description="ホワイトリストに追加する")
async def whitelist_add_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"whitelist add {player}")


@whitelist_group.command(name="remove", description="ホワイトリストから削除する")
async def whitelist_remove_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"whitelist remove {player}")


@whitelist_group.command(name="list", description="ホワイトリストを表示する")
async def whitelist_list_command(interaction: discord.Interaction) -> None:
    await _run(interaction, "whitelist list", required=0)


tree.add_command(whitelist_group)


# ── player (サブグループ、対象への管理操作) ───────────────────────────────────

player_group = app_commands.Group(name="player", description="プレイヤーへの管理操作(要上位権限)")


@player_group.command(name="ban", description="対象をBANする")
async def player_ban_command(interaction: discord.Interaction, player: str, reason: str | None = None) -> None:
    command = f"ban {player}" + (f" {_sanitize_text(reason)}" if reason else "")
    await _run(interaction, command, required=REQUIRED_LEVEL_ADMIN)


@player_group.command(name="pardon", description="対象のBANを解除する")
async def player_pardon_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"pardon {player}", required=REQUIRED_LEVEL_ADMIN)


@player_group.command(name="kick", description="対象をキックする")
async def player_kick_command(interaction: discord.Interaction, player: str, reason: str | None = None) -> None:
    command = f"kick {player}" + (f" {_sanitize_text(reason)}" if reason else "")
    await _run(interaction, command, required=REQUIRED_LEVEL_ADMIN)


@player_group.command(name="op", description="対象をOPにする")
async def player_op_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"op {player}", required=REQUIRED_LEVEL_ADMIN)


@player_group.command(name="deop", description="対象のOPを解除する")
async def player_deop_command(interaction: discord.Interaction, player: str) -> None:
    await _run(interaction, f"deop {player}", required=REQUIRED_LEVEL_ADMIN)


tree.add_command(player_group)


# ── execute (サブグループ) ────────────────────────────────────────────────────
# execute の機能そのものは run (任意のチェインをそのまま文字列で渡す) の時点で
# 100%再現できている(RCONにそのまま渡すだけなので、コンソールで打てることは全部打てる)。
# 再現できていないのはそこではなく、「chain全体を1個のDiscordスラッシュコマンドの
# 個別の型付き引数として表現する」方の完全さ。
#
# これは単に「引数を増やせば足りる」話ではない。execute の run が実行するコマンドには
# execute 自身も指定できるため、chain は理論上無限に再帰しうる:
#   execute as X at Y as Z run execute at W run execute ... run <command>
# (同じ修飾子の繰り返しも、execute-in-run の入れ子も、どちらも深さに上限が無い)
# 有限個のフィールドしか持てないDiscordスラッシュコマンドでは、この再帰構造を
# 原理的に表現しきれない。そこで custom では「各修飾子1回ずつ・固定順」という
# よく使う範囲だけを型付き引数として構造化し、繰り返しや入れ子が必要なケースは
# 素の文字列をそのまま通す run にフォールバックする、という2段構えにしている。

execute_group = app_commands.Group(name="execute", description="execute コマンドのラッパー")


@execute_group.command(name="run", description="execute の続き(as/at/if等のチェイン)をそのまま実行する")
async def execute_run_command(interaction: discord.Interaction, chain: str) -> None:
    await _run(interaction, f"execute {_sanitize_text(chain)}")


@execute_group.command(name="as", description="execute as <selector> run <command> のショートカット")
async def execute_as_command(interaction: discord.Interaction, selector: str, command: str) -> None:
    await _run(interaction, f"execute as {selector} run {_sanitize_text(command)}")


@execute_group.command(name="at", description="execute at <selector> run <command> のショートカット")
async def execute_at_command(interaction: discord.Interaction, selector: str, command: str) -> None:
    await _run(interaction, f"execute at {selector} run {_sanitize_text(command)}")


@execute_group.command(name="if-entity", description="execute if entity <selector> run <command> のショートカット")
async def execute_if_entity_command(interaction: discord.Interaction, selector: str, command: str) -> None:
    await _run(interaction, f"execute if entity {selector} run {_sanitize_text(command)}")


def _build_execute_chain(
    command: str,
    as_: str | None,
    at: str | None,
    positioned: str | None,
    rotated: str | None,
    facing: str | None,
    anchored: str | None,
    align: str | None,
    in_: str | None,
    if_entity: str | None,
    unless_entity: str | None,
    if_block: str | None,
    if_score: str | None,
) -> str:
    # Minecraft自体は多くの並びを受け付けるが、ここでは以下の固定順で組み立てる:
    # as → at → positioned → rotated → facing → anchored → align → in → if/unless → run
    # (実行対象/位置文脈を先に確定させ、その上で条件判定する、という一般的な使い方に沿った順序)
    parts: list[str] = []
    if as_:
        parts.append(f"as {as_}")
    if at:
        parts.append(f"at {at}")
    if positioned:
        parts.append(f"positioned {positioned}")
    if rotated:
        parts.append(f"rotated {rotated}")
    if facing:
        parts.append(f"facing {facing}")
    if anchored:
        parts.append(f"anchored {anchored}")
    if align:
        parts.append(f"align {align}")
    if in_:
        parts.append(f"in {in_}")
    if if_entity:
        parts.append(f"if entity {if_entity}")
    if unless_entity:
        parts.append(f"unless entity {unless_entity}")
    if if_block:
        parts.append(f"if block {if_block}")
    if if_score:
        parts.append(f"if score {if_score}")
    parts.append(f"run {command}")
    return "execute " + " ".join(parts)


@execute_group.command(
    name="custom",
    description="よく使うexecute修飾子を個別入力で組み立てる(1種類ずつのみ。複数回の重ねがけはrunを使う)",
)
@app_commands.rename(as_="as", in_="in")
@app_commands.describe(
    command="最終的に実行するコマンド",
    as_="実行者を変更するセレクター (execute as)",
    at="位置/次元/向きの基準にするセレクター (execute at)",
    positioned="基準位置からの相対/絶対座標 例: ~ ~1 ~ (execute positioned)",
    rotated="向き 例: 90 0 (execute rotated)",
    facing="向く先 例: 0 64 0 または entity @p eyes (execute facing)",
    anchored="位置基準を目/足のどちらにするか (execute anchored)",
    align="位置を整列させる軸 例: xyz (execute align)",
    in_="対象ディメンション 例: the_nether (execute in)",
    if_entity="この条件のエンティティが存在すれば実行 (execute if entity)",
    unless_entity="この条件のエンティティが存在しなければ実行 (execute unless entity)",
    if_block="'x y z block' 形式。指定座標が該当ブロックなら実行 (execute if block)",
    if_score="'score'の後に続く部分をそのまま。例: @s obj matches 1.. (execute if score)",
)
async def execute_custom_command(
    interaction: discord.Interaction,
    command: str,
    as_: str | None = None,
    at: str | None = None,
    positioned: str | None = None,
    rotated: str | None = None,
    facing: str | None = None,
    anchored: Literal["eyes", "feet"] | None = None,
    align: str | None = None,
    in_: str | None = None,
    if_entity: str | None = None,
    unless_entity: str | None = None,
    if_block: str | None = None,
    if_score: str | None = None,
) -> None:
    chain = _build_execute_chain(
        _sanitize_text(command),
        as_, at, positioned, rotated, facing, anchored, align, in_,
        if_entity, unless_entity, if_block, if_score,
    )
    await _run(interaction, chain)


tree.add_command(execute_group)
