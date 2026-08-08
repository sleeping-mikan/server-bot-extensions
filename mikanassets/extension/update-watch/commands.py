"""
update-watch — Minecraftの新しいバージョンをMojang公式のバージョンマニフェストで定期監視し、
見つかった場合はDiscordへ通知、要求があれば 停止→バックアップ→server.jar差し替え→起動 を
自動実行してバージョンアップを完了させる拡張機能。

## なぜ作ったか

world-visualizer拡張のバージョン自動検出を実機のサーバーディレクトリに対して検証した際、
`versions/` 配下に新しいバージョン(1.21.11)のjarが既に置かれているにもかかわらず、実際に
稼働しているワールドは古いバージョン(1.19.4)のまま保存され続けていることが判明した。
つまり「新しいバージョンを取得はしたが、実際に切り替える作業(停止→バックアップ→jar差し替え→
起動)が手間で後回しになっている」状態そのものが実際に観測された運用コストであり、これを
自動化・半自動化することが本拡張の目的。

## 何をするか

- 一定間隔(既定24時間)でMojangのバージョンマニフェストを取得し、現在ワールドが最後に
  保存されたバージョン(world-visualizer拡張と同じ、`<level-name>/level.dat` の
  Data.Version.Name を読む方式。version_history.json は生成されないサーバー構成が実機で
  確認されているため使わない)と比較する。新しいバージョンが見つかった場合のみDiscordへ
  通知する(既に通知済みのバージョンでは再通知しない)。
- `/extension-update-watch apply` で実際の切り替え(停止→バックアップ→jar差し替え→起動)を
  半自動実行できる。バックアップは必ず取ってから差し替えるため、万が一問題があれば
  コアBotの `/backup apply` で戻せる(本拡張はロールバックまでは自動化しない、詳細は
  下記「安全策について」参照)。

## バージョン比較の方法

Minecraftのバージョン文字列(例: "1.21.10" と "1.21.4")は単純な文字列比較や自作のsemver風
パースでは大小関係を誤りやすい("1.21.10" は文字列比較だと "1.21.4" より小さくなってしまう)。
そこで自前でパースせず、バージョンマニフェスト自身が持つ `releaseTime`(ISO8601、全エントリ
共通のタイムゾーン形式)を比較に使う。マニフェストの `versions` 配列自体も新しい順に並んでいる
ため、`include_snapshots` が有効なら単純に先頭要素を「最新」として扱う。

## 安全策について

- **jarの差し替えはサーバー停止後にのみ行う**: Windows環境ではJVMがserver.jarファイルを
  実行中ずっと開いたままにするため、稼働中に上書き/移動しようとするとファイルがロックされて
  失敗する(実機のBotプロセスがWindows上で動いていることを確認済み)。そのためダウンロード・
  sha1検証まではサーバー稼働中に済ませておき、実際にファイルを差し替える直前でのみ停止する
  (停止時間を最小化する狙いも兼ねる)。
- **差し替え前に必ずバックアップを取る**: `server.backup.create_backup`(scheduled-backup
  拡張と同じ、コアBotの `/backup create` と完全に同じ実装)をオプション無しで必ず実行する。
  対象を選ばせない(常にserver_path全体)のは、バージョンアップは通常のバックアップ運用より
  リスクが高い操作であり、対象を絞る余地を与えない方が安全なため。
- **Paper/Spigot/Forge/Fabric等、Mojang配布の素のserver.jarと構成が異なるサーバーでは
  `apply` を拒否する**: `ctx.server_path` 直下に `plugins/`(Paper/Spigot系)や
  `mods/`(Forge/Fabric系)が存在する場合、Mojangのvanilla server.jarへ黙って差し替えてしまうと
  導入済みのプラグイン/MODが使えなくなる重大な事故になるため、事前チェックで検出したら
  `apply` 自体を拒否する(このBotの運用対象はバニラJava版1台という前提のため、Paper等は
  自前でのアップデートに委ねる)。検出/通知(status・check-now)自体はサーバー種別を問わず
  動作する(level.dat の読み取りだけなので影響が無い)。
- **自動ロールバックはしない**: 差し替え後に起動が失敗しても、それが「新バージョン側の
  問題」か「起動に時間がかかっているだけ」かをBot側から確実に判定する手段が無い
  (known_limitations参照: プロセス状態のポーリングだけではクラッシュと意図的操作を区別
  できない、というdowntime-notifier拡張と同じ制約)。誤ったタイミングで自動的に前バージョンへ
  戻す方が事故のリスクが高いと判断し、直前に取ったバックアップの場所を結果embedへ明記して
  管理者が状況を見て `/backup apply` を判断できるようにするだけに留めた。

## 権限レベル

rcon/scheduled-backup拡張と同じく、権限レベルは state.json ではなく **.config** の
discord_commands.permission.commands_level で管理する(_KNOWN_PERMISSIONS参照)。
拡張ロード時に不足キーを自動登録しファイルへ書き戻す(_register_missing_permission_keys)。

    extension-update-watch status      0 (読み取り専用、ネットワークアクセス無し)
    extension-update-watch check-now   0 (読み取り専用だがMojangへネットワークアクセスする)
    extension-update-watch apply       2 (サーバー停止・jar差し替えを伴う破壊的操作)
    extension-update-watch config      2

登録される全コマンド: /extension-update-watch <status|check-now|apply|config>
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import shutil
import struct
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks

from bot.extension_api import (
    ModifiedEmbeds,
    ProgressCallback,
    StartResult,
    StopResult,
    append_task,
    client,
    create_backup,
    ctx,
    not_enough_permission,
    print_user,
    rewrite_config,
    start_server,
    stop_server,
    user_permission,
)

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
HTTP_TIMEOUT = 30.0
HTTP_HEADERS = {"User-Agent": "server-bot-extensions-pack/update-watch"}
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


# ── NBT最小リーダー ──────────────────────────────────────────────────────────
# world-visualizer拡張の wv_nbt.NBTReader と全く同じロジック。拡張機能は互いに依存せず
# 自己完結させる方針(このリポジトリの一貫した規約、rconが自前のRCONクライアントを、
# world-visualizerが自前のNBT/リージョンパーサを持つのと同じ理由)のため、あえて
# 共有せずここにも同じ実装を持つ。level.dat から Data.Version.Name を読むためだけに使う。

class _NBTReader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _read(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        if len(chunk) < n:
            raise ValueError("unexpected end of NBT data")
        self.pos += n
        return chunk

    def read_ubyte(self) -> int:
        return self._read(1)[0]

    def read_string(self) -> str:
        (length,) = struct.unpack(">H", self._read(2))
        return self._read(length).decode("utf-8", errors="replace")

    def read_payload(self, tag_type: int) -> Any:
        if tag_type == 1:
            return struct.unpack(">b", self._read(1))[0]
        if tag_type == 2:
            return struct.unpack(">h", self._read(2))[0]
        if tag_type == 3:
            return struct.unpack(">i", self._read(4))[0]
        if tag_type == 4:
            return struct.unpack(">q", self._read(8))[0]
        if tag_type == 5:
            return struct.unpack(">f", self._read(4))[0]
        if tag_type == 6:
            return struct.unpack(">d", self._read(8))[0]
        if tag_type == 7:
            (n,) = struct.unpack(">i", self._read(4))
            return list(struct.unpack(f">{n}b", self._read(n)))
        if tag_type == 8:
            return self.read_string()
        if tag_type == 9:
            item_type = self.read_ubyte()
            (n,) = struct.unpack(">i", self._read(4))
            if item_type == 0 or n <= 0:
                return []
            return [self.read_payload(item_type) for _ in range(n)]
        if tag_type == 10:
            compound: dict[str, Any] = {}
            while True:
                t = self.read_ubyte()
                if t == 0:
                    break
                name = self.read_string()
                compound[name] = self.read_payload(t)
            return compound
        if tag_type == 11:
            (n,) = struct.unpack(">i", self._read(4))
            return list(struct.unpack(f">{n}i", self._read(4 * n)))
        if tag_type == 12:
            (n,) = struct.unpack(">i", self._read(4))
            return list(struct.unpack(f">{n}q", self._read(8 * n)))
        raise ValueError(f"unsupported NBT tag type {tag_type}")

    def read_root(self) -> dict[str, Any]:
        tag_type = self.read_ubyte()
        if tag_type == 0:
            return {}
        self.read_string()
        payload = self.read_payload(tag_type)
        return payload if isinstance(payload, dict) else {}


# ── サーバーファイル読み取り(rcon/world-visualizerと同じロジックを自己完結で持つ) ──────

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


def _level_name() -> str:
    return _read_server_properties().get("level-name", "world") or "world"


def _detect_installed_version() -> str | None:
    """`<level-name>/level.dat` (gzip圧縮NBT) の Data.Version.Name を読む。
    ワールドが1回でも保存されていれば必ず存在する値で、version_history.json より
    信頼できる(world-visualizer拡張での実機検証と同じ結論、詳細はモジュールdocstring参照)。"""
    path = ctx.server_path / _level_name() / "level.dat"
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            data = gzip.decompress(f.read())
        root = _NBTReader(data).read_root()
        version = root.get("Data", {}).get("Version", {}).get("Name")
        return str(version) if version else None
    except Exception as e:
        logger.error(f"failed to read level.dat version ({e})")
        return None


# ── Mojangバージョンマニフェスト ────────────────────────────────────────────────

def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_manifest() -> dict:
    return _http_get_json(VERSION_MANIFEST_URL)


def _find_version_entry(manifest: dict, version_id: str) -> dict | None:
    return next((v for v in manifest.get("versions", []) if v.get("id") == version_id), None)


def _pick_target_entry(manifest: dict, include_snapshots: bool) -> dict | None:
    """『最新』として扱うバージョンのマニフェストエントリを返す。include_snapshots が
    偽なら releaseのみ、真なら種別を問わずマニフェスト先頭(=最新)を対象にする。"""
    if include_snapshots:
        versions = manifest.get("versions", [])
        return versions[0] if versions else None
    latest_release_id = manifest.get("latest", {}).get("release")
    if not latest_release_id:
        return None
    return _find_version_entry(manifest, latest_release_id)


# ── state.json ────────────────────────────────────────────────────────────

_STATE_FILE = Path(__file__).parent / "state.json"
_DEFAULT_STATE: dict = {
    "enabled": True,
    "interval_hours": 24,
    "discord_channel_id": None,
    "include_snapshots": False,
    "server_jar_filename": "server.jar",
    "stop_timeout_seconds": 60,
    # 直近のチェック結果(定期チェック・check-now共通)。呼び出し毎にネットワークへ
    # アクセスせずに /status を即答するためのキャッシュ。
    "last_checked_ts": None,
    "last_known_latest_id": None,
    # このバージョンについては既に通知済み、というマーク。同じ最新バージョンのまま
    # 何度もチェックが走っても再通知しないための重複排除。
    "last_notified_version": None,
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


# ── 権限(.config管理、rcon/scheduled-backupと同じ方式) ───────────────────────

_KNOWN_PERMISSIONS: dict[str, int] = {
    "extension-update-watch status": 0,
    "extension-update-watch check-now": 0,
    "extension-update-watch apply": 2,
    "extension-update-watch config": 2,
}


def _register_missing_permission_keys() -> None:
    added = False
    for key, default in _KNOWN_PERMISSIONS.items():
        if key not in ctx.text.command_permission:
            ctx.text.command_permission[key] = default
            added = True
    if added:
        logger.info("registered missing update-watch permission keys, writing to .config")
        asyncio.run(rewrite_config())


_register_missing_permission_keys()


def _perm(key: str) -> int:
    return ctx.text.command_permission.get(key, _KNOWN_PERMISSIONS[key])


async def _check_permission(interaction: discord.Interaction, required: int) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < required:
        await not_enough_permission(interaction, logger)
        return False
    return True


# ── 通知 ──────────────────────────────────────────────────────────────────

async def _notify(text: str) -> None:
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


# ── 定期チェック ──────────────────────────────────────────────────────────────

def _check_once_sync() -> tuple[str | None, dict | None]:
    """installed version と最新エントリを1回取得する(ネットワークアクセスを伴うので
    呼び出し側で asyncio.to_thread に包むこと)。"""
    installed = _detect_installed_version()
    manifest = _fetch_manifest()
    target = _pick_target_entry(manifest, _state["include_snapshots"])
    return installed, target


@tasks.loop(minutes=30)
async def _watch_loop() -> None:
    if not _state["enabled"]:
        return
    last_checked = datetime.fromisoformat(_state["last_checked_ts"]) if _state["last_checked_ts"] else None
    if last_checked is not None and datetime.now() - last_checked < timedelta(hours=_state["interval_hours"]):
        return

    try:
        installed, target = await asyncio.to_thread(_check_once_sync)
    except Exception as e:
        logger.error(f"periodic version check failed ({e})")
        return

    _state["last_checked_ts"] = datetime.now().isoformat()
    if target is not None:
        _state["last_known_latest_id"] = target["id"]
    _save_state()

    if target is None or installed is None or installed == target["id"]:
        return
    if _state["last_notified_version"] == target["id"]:
        return

    _state["last_notified_version"] = target["id"]
    _save_state()
    await _notify(
        f"🆕 Minecraftの新しいバージョンが利用可能です: `{installed}` → `{target['id']}`\n"
        f"`/extension-update-watch apply` で更新できます(停止→バックアップ→jar差し替え→起動)。"
    )


append_task(_watch_loop)


# ── status ────────────────────────────────────────────────────────────────

def _status_embed() -> ModifiedEmbeds.DefaultEmbed:
    embed = ModifiedEmbeds.DefaultEmbed(title="Minecraftバージョン更新監視")
    installed = _detect_installed_version()
    embed.add_field(name="現在のワールドのバージョン", value=installed or "不明(level.dat未検出)", inline=True)

    latest_id = _state["last_known_latest_id"]
    if latest_id:
        is_outdated = installed is not None and installed != latest_id
        embed.add_field(name="既知の最新バージョン", value=latest_id, inline=True)
        embed.add_field(name="更新", value="必要です" if is_outdated else "不要です(最新)", inline=True)
    else:
        embed.add_field(name="既知の最新バージョン", value="未チェック(check-nowで確認できます)", inline=True)

    last_checked = _state["last_checked_ts"]
    embed.add_field(
        name="直近チェック",
        value=datetime.fromisoformat(last_checked).strftime("%Y-%m-%d %H:%M:%S") if last_checked else "なし",
        inline=True,
    )
    embed.add_field(
        name="自動チェック",
        value=(f"有効 ({_state['interval_hours']}時間ごと)" if _state["enabled"] else "無効"),
        inline=True,
    )
    embed.add_field(
        name="通知先",
        value=f"<#{_state['discord_channel_id']}>" if _state["discord_channel_id"] else "未設定",
        inline=True,
    )
    embed.add_field(name="対象チャンネル", value="スナップショット含む" if _state["include_snapshots"] else "リリースのみ", inline=True)
    embed.add_field(name="対象jarファイル名", value=f"`{_state['server_jar_filename']}`", inline=True)
    return embed


@tree.command(name="status", description="検出中のMinecraftバージョンと更新状況を表示する")
async def status_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, _perm("extension-update-watch status")):
        return
    await interaction.response.send_message(embed=_status_embed())


# ── check-now ─────────────────────────────────────────────────────────────

@tree.command(name="check-now", description="今すぐMojangのバージョンマニフェストを確認する")
async def check_now_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, _perm("extension-update-watch check-now")):
        return
    await interaction.response.defer()

    try:
        installed, target = await asyncio.to_thread(_check_once_sync)
    except Exception as e:
        logger.error(f"check-now failed ({e})")
        embed = ModifiedEmbeds.ErrorEmbed(title="バージョン情報の取得に失敗しました", description=str(e))
        await interaction.followup.send(embed=embed)
        return

    _state["last_checked_ts"] = datetime.now().isoformat()
    if target is not None:
        _state["last_known_latest_id"] = target["id"]
    # 手動チェックで既に見えているので、直後の定期チェックが同じバージョンで
    # 再通知しないよう合わせて既読扱いにする
    if target is not None and installed is not None and installed != target["id"]:
        _state["last_notified_version"] = target["id"]
    _save_state()

    await interaction.followup.send(embed=_status_embed())


# ── apply ─────────────────────────────────────────────────────────────────

def _unsafe_to_apply_reason() -> str | None:
    if (ctx.server_path / "plugins").exists():
        return "server_path 直下に `plugins/` があります(Paper/Spigot系と思われるため、Mojang配布のvanilla jarへの差し替えは行いません)"
    if (ctx.server_path / "mods").exists():
        return "server_path 直下に `mods/` があります(Forge/Fabric系と思われるため、Mojang配布のvanilla jarへの差し替えは行いません)"
    return None


def _resolve_target_sync(version: str | None) -> tuple[dict | None, str | None]:
    """(バージョンマニフェストのエントリ, エラーメッセージ) を返す。"""
    manifest = _fetch_manifest()
    if version is None:
        entry = _pick_target_entry(manifest, _state["include_snapshots"])
        if entry is None:
            return None, "最新バージョンを解決できませんでした"
        return entry, None
    entry = _find_version_entry(manifest, version)
    if entry is None:
        return None, f"バージョン `{version}` がMojangのバージョンマニフェストに見つかりません"
    return entry, None


def _download_and_verify_sync(version_entry: dict, tmp_path: Path) -> tuple[bool, str]:
    """version_entry のserver.jarを tmp_path へダウンロードし、sha1/サイズを検証する。
    ダウンロード先はまだ本番のjarパスではない(差し替えはサーバー停止後に別途行う、
    理由はモジュールdocstring「安全策について」参照)。"""
    version_meta = _http_get_json(version_entry["url"])
    server_info = version_meta.get("downloads", {}).get("server")
    if not server_info:
        return False, "このバージョンにはサーバー配布物がありません"

    url = server_info["url"]
    expected_sha1 = server_info.get("sha1")
    expected_size = server_info.get("size")

    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha1()
    total = 0
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp, tmp_path.open("wb") as f:
        while True:
            chunk = resp.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)
            hasher.update(chunk)
            total += len(chunk)

    if expected_size is not None and total != expected_size:
        tmp_path.unlink(missing_ok=True)
        return False, f"ダウンロードサイズが一致しません(期待値{expected_size}バイト、実際{total}バイト)"
    if expected_sha1 and hasher.hexdigest() != expected_sha1:
        tmp_path.unlink(missing_ok=True)
        return False, "ダウンロードしたファイルのsha1が一致しません(破損の可能性があるため中止しました)"

    return True, f"{total / 1024 / 1024:.1f} MB を取得・検証しました"


_apply_running = False


@tree.command(name="apply", description="Minecraftサーバーを 停止→バックアップ→server.jar差し替え→起動 で更新する")
@app_commands.describe(version="更新先バージョン(例: 1.21.4)。省略時は最新バージョンを使う")
async def apply_command(interaction: discord.Interaction, version: str | None = None) -> None:
    global _apply_running

    if not await _check_permission(interaction, _perm("extension-update-watch apply")):
        return

    unsafe_reason = _unsafe_to_apply_reason()
    if unsafe_reason:
        embed = ModifiedEmbeds.ErrorEmbed(title="この構成では自動更新できません", description=unsafe_reason)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if _apply_running:
        embed = ModifiedEmbeds.ErrorEmbed(title="既に更新処理が実行中です")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if ctx.is_backup_in_progress:
        embed = ModifiedEmbeds.ErrorEmbed(title="他のバックアップ処理が進行中のため中止しました")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = ModifiedEmbeds.DefaultEmbed(title="Minecraftサーバーの更新を開始します")
    embed.add_field(name="状況", value="バージョンを確認しています...", inline=False)
    await interaction.response.send_message(embed=embed)

    _apply_running = True
    try:
        target_entry, error = await asyncio.to_thread(_resolve_target_sync, version)
        if error:
            embed.clear_fields()
            embed.add_field(name="失敗", value=error, inline=False)
            await interaction.edit_original_response(embed=embed)
            return

        installed = _detect_installed_version()
        target_id = target_entry["id"]
        if installed == target_id:
            embed.clear_fields()
            embed.add_field(name="結果", value=f"既に最新です(`{installed}`)。何もしませんでした。", inline=False)
            await interaction.edit_original_response(embed=embed)
            return

        embed.clear_fields()
        embed.add_field(name="状況", value=f"`{installed or '不明'}` → `{target_id}` のjarをダウンロード中...", inline=False)
        await interaction.edit_original_response(embed=embed)

        jar_path = ctx.server_path / _state["server_jar_filename"]
        tmp_path = jar_path.with_name(jar_path.name + f".update-{target_id}.tmp")
        ok, message = await asyncio.to_thread(_download_and_verify_sync, target_entry, tmp_path)
        if not ok:
            embed.clear_fields()
            embed.add_field(name="失敗", value=message, inline=False)
            await interaction.edit_original_response(embed=embed)
            return

        was_running = not ctx.server_process.is_stopped()
        if was_running:
            embed.clear_fields()
            embed.add_field(name="状況", value="ダウンロード完了。サーバーを停止しています...", inline=False)
            await interaction.edit_original_response(embed=embed)

            stop_result = stop_server()
            if stop_result == StopResult.SUCCESS:
                for _ in range(_state["stop_timeout_seconds"]):
                    if ctx.server_process.is_stopped():
                        break
                    await asyncio.sleep(1)
                if not ctx.server_process.is_stopped():
                    tmp_path.unlink(missing_ok=True)
                    embed.clear_fields()
                    embed.add_field(
                        name="失敗",
                        value="サーバーの停止がタイムアウトしたため中止しました(jarは差し替えていません)。手動で状態を確認してください。",
                        inline=False,
                    )
                    await interaction.edit_original_response(embed=embed)
                    return

        embed.clear_fields()
        embed.add_field(name="状況", value="更新前バックアップを取得しています...", inline=False)
        await interaction.edit_original_response(embed=embed)

        async def on_progress(copied: int, total: int, copied_bytes: int, total_bytes: int) -> None:
            send_sens = max(1, total // 20)
            if copied % send_sens != 0 and copied != total:
                return
            bar_width = 30
            ratio = copied / total if total else 0
            filled = max(0, int(ratio * bar_width) - 1)
            bar = "=" * filled + "-" * (bar_width - filled - 1)
            embed.clear_fields()
            embed.add_field(
                name="更新前バックアップ中" if copied != total else "更新前バックアップ完了",
                value=f"```{bar}\n{copied:5} / {total:5} ({copied_bytes / 1024 ** 3:.2f} / {total_bytes / 1024 ** 3:.2f} GB)```",
                inline=False,
            )
            await interaction.edit_original_response(embed=embed)

        backup_dst = await create_backup(str(ctx.server_path), on_progress=on_progress)

        # jarの差し替えはサーバー停止後・バックアップ後にのみ行う(理由はdocstring参照)
        shutil.move(str(tmp_path), str(jar_path))

        start_note = ""
        if was_running:
            start_result = start_server(ctx.server_logger)
            if start_result != StartResult.SUCCESS:
                start_note = f"\n⚠️ サーバーの再起動に失敗しました ({start_result.name})。手動で /start を実行してください。"

        _state["last_known_latest_id"] = target_id
        _state["last_notified_version"] = target_id
        _state["last_checked_ts"] = datetime.now().isoformat()
        _save_state()

        embed.clear_fields()
        embed.add_field(name="結果", value=f"✅ `{installed or '不明'}` → `{target_id}` へ更新しました{start_note}", inline=False)
        embed.add_field(name="更新前バックアップ", value=f"`{backup_dst}`", inline=False)
        await interaction.edit_original_response(embed=embed)
        await _notify(f"✅ Minecraftサーバーを `{installed or '不明'}` → `{target_id}` へ更新しました{start_note}\n`{backup_dst}`")
    finally:
        _apply_running = False


# ── config ────────────────────────────────────────────────────────────────

@tree.command(name="config", description="バージョン監視の間隔・通知先・対象チャンネル・対象jarファイル名を設定する")
@app_commands.describe(
    enabled="定期チェックを有効にするか",
    interval_hours="何時間ごとにチェックするか",
    channel="通知先チャンネル(未指定時は現状維持、未設定なら実行チャンネル)",
    include_snapshots="スナップショットも最新扱いに含めるか(既定はfalseでリリースのみ)",
    server_jar_filename="applyで差し替える対象のjarファイル名(server_path直下、既定はserver.jar)",
    stop_timeout_seconds="applyでの停止コマンド送信後、完了をここで指定した秒数まで待つ",
)
async def config_command(
    interaction: discord.Interaction,
    enabled: bool | None = None,
    interval_hours: int | None = None,
    channel: discord.TextChannel | None = None,
    include_snapshots: bool | None = None,
    server_jar_filename: str | None = None,
    stop_timeout_seconds: int | None = None,
) -> None:
    if not await _check_permission(interaction, _perm("extension-update-watch config")):
        return

    if enabled is not None:
        _state["enabled"] = enabled
    if interval_hours is not None:
        _state["interval_hours"] = max(1, interval_hours)
    if channel is not None:
        _state["discord_channel_id"] = channel.id
    if include_snapshots is not None:
        _state["include_snapshots"] = include_snapshots
    if server_jar_filename is not None:
        stripped = server_jar_filename.strip()
        if stripped:
            _state["server_jar_filename"] = stripped
    if stop_timeout_seconds is not None:
        _state["stop_timeout_seconds"] = max(1, stop_timeout_seconds)

    # 通知が意味を持つのはenabled時なので、scheduled-backupと同じく未設定なら
    # このコマンドを実行したチャンネルへ自動フォールバックする(既存値は上書きしない)
    if _state["enabled"] and _state["discord_channel_id"] is None and interaction.channel is not None:
        _state["discord_channel_id"] = interaction.channel.id

    _save_state()
    await interaction.response.send_message(embed=_status_embed())
