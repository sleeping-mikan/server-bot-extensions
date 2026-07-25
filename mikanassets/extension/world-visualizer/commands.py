"""
world-visualizer — ワールド全体のマップ画像生成と、プレイヤー所持アイテムの画像表示を行う拡張機能。

## 何をする拡張か

- `/extension-world-visualizer map` — ワールドのリージョンファイル(.mca)を直接読み、
  チャンクごとの実ブロック色(草/葉/水など一部はバイオーム色を補正、標高による陰影は無し)
  から真上から見た俯瞰マップ画像を1枚合成して返す。
- `/extension-world-visualizer inventory` — 指定プレイヤーの playerdata(.dat)を直接読み、
  所持アイテム(メインインベントリ+ホットバー)を実アイテムアイコン付きのグリッド画像として、
  防具/オフハンドを embedのテキストとして返す。
- `/extension-world-visualizer config` — マップ描画/アイコン取得に使うMinecraftバージョンの
  手動指定/確認、ブロック色/アイテムアイコンキャッシュのクリアを行う(ネットワークへは
  一切アクセスしない)。

いずれもRCONやサーバーの標準出力に一切依存せず、`ctx.server_path` 配下のワールド保存ファイルを
直接パースする読み取り専用の実装(サーバー本体のファイルには一切書き込まない)。そのため
サーバーが停止中でも動作する(ただし当然、直近のオートセーブ以降の変更は反映されない)。

## ファイル構成

このディレクトリは単一の commands.py に収めず、責務ごとに分割している。ファイル数が
増えてきたため(2026-07-25、アイテムアイコン対応で lib/ が9ファイルへ)、実装本体は
lib/ へ、Mojangキャッシュ等の実行時生成物は cache/ へ、それぞれ commands.py 直下から
分離した(このリポジトリの他拡張は単一 commands.py + state.json のみで完結しており、
サブディレクトリを持つのは規模が大きくなった world-visualizer だけ)。

    commands.py             このファイル。Discordスラッシュコマンドの定義のみ
    state.json               実行時に自動生成される設定(minecraft_versionの手動指定)。
                              他拡張機能と同じ場所に置く規約(.gitignoreの
                              `mikanassets/extension/*/state.json` パターンに合わせる)
    lib/                     実装本体(下記)
    cache/                   Mojangクライアントjarから取得した結果の永続キャッシュ(下記)

    lib/wv_nbt.py             チャンク/playerdata共通のNBTバイナリリーダー
    lib/wv_imaging.py         Pillowの有無判定(他モジュールへ共有)
    lib/wv_mojangjar.py       Mojangクライアントjarへのアクセス共通処理(HTTP Range読み取り等)
    lib/wv_serverfiles.py     server.properties からのワールド名取得
    lib/wv_worldmap.py        リージョンファイル読み取り・マップ画像合成
    lib/wv_blockcolors.py     ブロック色の取得・キャッシュ(下記「配色の仕組み」参照)
    lib/wv_playerdata.py      usercache.json/playerdata読み取り・アイテム抽出
    lib/wv_itemtextures.py    アイテムアイコン画像の取得・キャッシュ(下記「inventory の仕組み」参照)
    lib/wv_inventoryimage.py  インベントリのグリッド画像合成(実アイコン優先、無ければ色付き矩形)

    cache/block_colors/       wv_blockcolors.py が貯めるブロック色キャッシュ(colors.json等)
    cache/item_textures/      wv_itemtextures.py が貯めるアイテムアイコンPNGキャッシュ

拡張フォルダ名 "world-visualizer" はハイフンを含み正式なPythonパッケージ名にできないため
(他拡張も含めこのリポジトリの全フォルダがハイフン区切りで、コアBotのロード機構は
commands.py を単体ファイルとして直接読み込んでいると見られる)、相対importではなく
このファイルの先頭で lib/ を sys.path に足したうえで、"wv_" prefix付きの一意な名前で
単純importしている。sys.pathへの追加はBotプロセス全体に影響するため、他拡張機能や
pipパッケージの同名モジュールと衝突しないよう prefix を付けている(lib/ というサブ
ディレクトリへ分離した後もこの衝突回避の考え方は変わらないため、ディレクトリを分けた
だけでモジュール名から "wv_" prefix を外すことはしていない)。

## 前提: Pillow (PIL) が必要

画像合成に Pillow を使う。Botの実行環境に無ければ

    pip install Pillow

を実行してから拡張を読み込み直すこと。未インストールの場合、全コマンドとも
「Pillow (PIL) がインストールされていません」というエラーembedを返すだけで、
他の拡張機能やBot本体には影響しない(lib/wv_imaging.py で ImportError を握りつぶしている)。

## map の仕組みと制約

- `<level-name>/region/*.mca`(ネザーは `DIM-1/region`、エンドは `DIM1/region`、現行
  バージョンでは `<level-name>/dimensions/minecraft/<id>/region/` に統一されておりそちらを
  優先する)を走査し、1チャンク(16x16ブロック)につき1ピクセルとして、チャンク中央付近の
  列のハイトマップ最上段のブロック(1個)をサンプリングし、その実テクスチャ色を基本に
  ピクセル色を決める真上から見た平面図(Dynmapの平面表示や旧来の地図アイテムに近い)。
  草ブロック上面・葉・水などバイオーム色を掛け合わせる前提のテクスチャのみバイオーム色を
  補正し、それ以外(石・土・木材など大多数)は実テクスチャの色をそのまま使う。標高による
  陰影は行わない。
- 探索範囲が大きい(400チャンク四方を超える)場合は自動的に間引いてサンプリングする
  (embedの「サンプリング間隔」欄で確認できる)。間引きは大まかな全体像を保ちつつ
  処理時間・画像サイズを抑えるための措置で、間引き無し(stride=1)なら1ピクセル=1チャンク。
- チャンクデータ形式は 1.18 以降を前提にしている(詳細は lib/wv_worldmap.py 冒頭を参照)。

## 配色の仕組み: 知らないブロックが出てきた時だけAPIを叩く。叩く時は全部持ってくる

ブロックID→色の対応表を手作業で用意すると、バージョンアップでブロックが増える度に
メンテナンスが必要になる。そこで、Mojangが誰でも無認証でダウンロードできる形で公開している
クライアントjar(ランチャー自体が使うのと同じ公式配布物)からブロックテクスチャの色を
自動抽出するが、**jar全体(数十MB)を毎回ダウンロードすることはしない**。MojangのCDNは
HTTP Rangeリクエストに対応しているため、ZIPの中央ディレクトリだけ取得してファイル一覧を
把握できる。ネットワークへは「まだ見たことが無いブロックに遭遇した時」だけアクセスするが、
一度アクセスする以上は中央ディレクトリの取得コストを払い済みなので、**その場で
assets/minecraft/textures/block/ 配下の全ブロックテクスチャ(実測1269枚、約3.4MB・約18秒)を
まとめて取得してキャッシュする**(1個ずつ都度取得するより効率的なため)。一度取得しきった
バージョンでは以後 `/map` を何度実行してもネットワークへ一切アクセスしない
(`cache/block_colors/colors.json` へ永続キャッシュ、バージョンをまたいで使い回す)。
存在しないテクスチャ名も `missing` として記録し、無限に再試行しない。

ネットワークに一切アクセスできない/Minecraftバージョンを検出できない環境では、解決できな
かったブロックは灰色(`wv_worldmap.UNKNOWN_BLOCK_COLOR`)で描画される(embedの「配色」欄で
確認できる)。詳細な設計意図は lib/wv_blockcolors.py 冒頭のdocstringを参照。

## inventory の仕組みと制約

- `usercache.json` からプレイヤー名→UUIDを引き、playerdataを直接読む。RCONの
  `data get entity` と違い**オフライン中のプレイヤーでも参照できる**(サーバーが
  起動している必要すらない)。詳細は lib/wv_playerdata.py 冒頭を参照。
- アイテムアイコンは map の配色と同じ考え方(知らないアイテムに遭遇した時だけMojangの
  クライアントjarへアクセスし、その場で `assets/minecraft/textures/item/` 配下を一括取得
  してキャッシュする)で取得する。道具・食料等は本物のGUIアイコンPNGがそのまま存在するが、
  ブロックをそのまま置けるアイテム(土・石など)は3Dの等角アイコンをjar内の静止画だけから
  再現できないため、`assets/minecraft/textures/block/` の平面テクスチャで代用する(mapの
  簡略化と同様の妥協)。マップ用のバージョン設定(`config` コマンド)をそのまま使うため、
  バージョンが未検出の場合はアイコンを取得できず、従来通りハッシュ色の矩形+テキスト
  ラベルにフォールバックする。詳細は lib/wv_itemtextures.py 冒頭を参照。

## 権限レベル

各コマンドの要求権限レベルは、拡張機能側の state.json ではなく **.config** の
discord_commands.permission.commands_level に "extension-world-visualizer <サブコマンド名>":
<レベル> というキーで管理する(rcon拡張と同じ方式、詳細は rcon/commands.py 冒頭を参照)。
デフォルト値は _KNOWN_PERMISSIONS にまとめてあり、拡張ロード時に .config にまだ無い
キーがあれば自動的にこのデフォルト値で書き足し、その場で .config ファイルへ即座に反映する
(_register_missing_permission_keys() 参照)。いずれも読み取り専用の処理であることから
デフォルトは全て 0(誰でも実行可能、`whitelist-ops-viewer` と同じ従来の挙動)としているが、
Mojangへのネットワークアクセスを伴う点が他の読み取り専用コマンドと異なるため、管理者は
.config 側で必要に応じて引き上げられる。

    extension-world-visualizer map          0
    extension-world-visualizer inventory     0
    extension-world-visualizer config        0

登録される全コマンド: /extension-world-visualizer <map|inventory|config>
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

from bot.embeds import ModifiedEmbeds
from bot.utils import not_enough_permission, print_user, rewrite_config, user_permission
from core.state import ctx

# lib/ を sys.path へ追加してから "wv_" prefix 付きの兄弟モジュールをimportする
# (理由は本docstring「ファイル構成」節を参照)。
_EXTENSION_DIR = Path(__file__).resolve().parent
_LIB_DIR = _EXTENSION_DIR / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.append(str(_LIB_DIR))

import wv_blockcolors  # noqa: E402
import wv_inventoryimage  # noqa: E402
import wv_itemtextures  # noqa: E402
import wv_playerdata  # noqa: E402
import wv_worldmap  # noqa: E402
from wv_imaging import PIL_AVAILABLE  # noqa: E402

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

# world-visualizer の各コマンドが要求する権限レベルのデフォルト値(唯一の定義元)。
# .config の discord_commands.permission.commands_level に同名キーが無ければここに登録し、
# そのままファイルへも書き戻す(_register_missing_permission_keys 参照、rcon拡張と同じ方式)。
# いずれも読み取り専用のためデフォルトは全て0(誰でも実行可能)。
_KNOWN_PERMISSIONS: dict[str, int] = {
    "extension-world-visualizer map": 0,
    "extension-world-visualizer inventory": 0,
    "extension-world-visualizer config": 0,
}


def _register_missing_permission_keys() -> None:
    """.config に無い world-visualizer の権限キーを _KNOWN_PERMISSIONS の値で登録し、
    即座に .config へ書き戻す(詳細な経緯は rcon/commands.py の同名関数のdocstringを参照)。"""
    added = False
    for key, default in _KNOWN_PERMISSIONS.items():
        if key not in ctx.text.command_permission:
            ctx.text.command_permission[key] = default
            added = True
    if added:
        logger.info("registered missing world-visualizer permission keys, writing to .config")
        asyncio.run(rewrite_config())


_register_missing_permission_keys()


def _perm(key: str) -> int:
    return ctx.text.command_permission.get(key, _KNOWN_PERMISSIONS[key])


async def _check_permission(interaction: discord.Interaction, required: int) -> bool:
    if await user_permission(interaction.user) < required:
        await not_enough_permission(interaction, logger)
        return False
    return True


_STATE_FILE = Path(__file__).parent / "state.json"
_DEFAULT_STATE: dict = {"minecraft_version": None}


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


def _pillow_missing_embed() -> discord.Embed:
    return ModifiedEmbeds.ErrorEmbed(
        title="Pillow (PIL) がインストールされていません",
        description="この拡張機能の画像生成には Pillow が必要です。"
        "Botの実行環境で `pip install Pillow` を実行し、拡張を読み込み直してください。",
    )


# ── map コマンド ─────────────────────────────────────────────────────────────

@tree.command(name="map", description="ワールド全体のマップ画像を生成する(実ブロック色+バイオーム+標高によるレンダリング)")
@app_commands.describe(dimension="対象ディメンション")
async def map_command(
    interaction: discord.Interaction,
    dimension: Literal["overworld", "nether", "end"] = "overworld",
) -> None:
    await print_user(logger, interaction.user)
    if not await _check_permission(interaction, _perm("extension-world-visualizer map")):
        return
    if not PIL_AVAILABLE:
        await interaction.response.send_message(embed=_pillow_missing_embed(), ephemeral=True)
        return

    await interaction.response.defer()

    def _work() -> tuple[tuple[bytes, int, int, int, int, int] | None, str | None]:
        version = wv_blockcolors.detect_minecraft_version(_state)
        cache = wv_blockcolors.load_color_cache() if version else None
        result = wv_worldmap.build_map_image(dimension, cache, version)
        return result, version

    try:
        result, version = await asyncio.to_thread(_work)
    except Exception as e:
        logger.error(f"failed to build map image ({dimension}) ({e})")
        embed = ModifiedEmbeds.ErrorEmbed(title="マップ画像の生成に失敗しました", description=str(e))
        await interaction.followup.send(embed=embed)
        return

    if result is None:
        embed = ModifiedEmbeds.ErrorEmbed(
            title="マップ画像を生成できませんでした",
            description="対象ディメンションのリージョンファイルが見つかりませんでした(まだ生成されていない可能性があります)",
        )
        await interaction.followup.send(embed=embed)
        return

    png_bytes, chunk_count, stride, grid_w, grid_h, newly_resolved = result
    file = discord.File(io.BytesIO(png_bytes), filename="map.png")
    embed = ModifiedEmbeds.DefaultEmbed(title=f"ワールドマップ ({dimension})")
    embed.add_field(name="描画チャンク数", value=str(chunk_count), inline=True)
    embed.add_field(name="サンプリング間隔", value=(f"{stride}チャンクごと" if stride > 1 else "全チャンク"), inline=True)
    embed.add_field(name="範囲(概算)", value=f"約 {grid_w * stride * 16} x {grid_h * stride * 16} ブロック", inline=True)
    if version:
        color_desc = f"実ブロック色 (MC {version})"
        if newly_resolved:
            color_desc += f" ※新規{newly_resolved}種類を取得"
    else:
        color_desc = "色情報なし・灰色で表示(バージョン未検出。/extension-world-visualizer config で手動指定できます)"
    embed.add_field(name="配色", value=color_desc, inline=True)
    embed.set_image(url="attachment://map.png")
    await interaction.followup.send(embed=embed, file=file)


# ── config コマンド ──────────────────────────────────────────────────────────

@tree.command(name="config", description="マップ描画/アイコン取得に使うMinecraftバージョンの設定/確認、ブロック色/アイテムアイコンキャッシュのクリアを行う")
@app_commands.describe(
    minecraft_version="手動で使うバージョン(例: 1.21.4)。省略時は world/level.dat 等による自動検出を使う",
    clear_cache="蓄積したブロック色/アイテムアイコンキャッシュを削除する(次回以降また必要な分だけ取得し直す)",
)
async def config_command(
    interaction: discord.Interaction,
    minecraft_version: str | None = None,
    clear_cache: bool = False,
) -> None:
    # このコマンドはローカルのファイル操作のみでネットワークへは一切アクセスしないため、
    # defer() は不要(即座に応答できる)。
    await print_user(logger, interaction.user)
    if not await _check_permission(interaction, _perm("extension-world-visualizer config")):
        return

    if minecraft_version is not None:
        _state["minecraft_version"] = minecraft_version.strip() or None
        _save_state()
    if clear_cache:
        wv_blockcolors.clear_color_cache()
        wv_itemtextures.clear_icon_cache()

    version = wv_blockcolors.detect_minecraft_version(_state)
    cache = wv_blockcolors.load_color_cache()
    icon_index = wv_itemtextures.load_index()

    embed = ModifiedEmbeds.DefaultEmbed(title="world-visualizer 設定")
    if version:
        embed.add_field(name="Minecraftバージョン", value=f"{version}" + (" (手動指定)" if _state.get("minecraft_version") else " (自動検出)"), inline=True)
    else:
        embed.add_field(name="Minecraftバージョン", value="未検出(minecraft_versionで手動指定してください)", inline=True)
    embed.add_field(name="キャッシュ済み色数", value=str(len(cache["colors"])), inline=True)
    embed.add_field(name="解決不能と判定済み(色)", value=str(len(cache["missing"])), inline=True)
    embed.add_field(name="キャッシュ済みアイコン数", value=str(len(icon_index["resolved"])), inline=True)
    embed.add_field(name="解決不能と判定済み(アイコン)", value=str(len(icon_index["missing"])), inline=True)
    await interaction.response.send_message(embed=embed)


# ── inventory コマンド ───────────────────────────────────────────────────────

@tree.command(name="inventory", description="プレイヤーの所持アイテムを画像で表示する")
@app_commands.describe(player="対象プレイヤー名")
async def inventory_command(interaction: discord.Interaction, player: str) -> None:
    await print_user(logger, interaction.user)
    if not await _check_permission(interaction, _perm("extension-world-visualizer inventory")):
        return
    if not PIL_AVAILABLE:
        await interaction.response.send_message(embed=_pillow_missing_embed(), ephemeral=True)
        return

    uuid = wv_playerdata.resolve_uuid(player)
    if uuid is None:
        embed = ModifiedEmbeds.ErrorEmbed(
            title="プレイヤーが見つかりません",
            description=f"usercache.json に `{player}` の記録がありません(一度もサーバーに参加していない可能性があります)",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer()
    player_nbt = await asyncio.to_thread(wv_playerdata.load_player_nbt, uuid)
    if player_nbt is None:
        embed = ModifiedEmbeds.ErrorEmbed(title="プレイヤーデータが見つかりません", description=f"playerdata/{uuid}.dat が見つかりませんでした")
        await interaction.followup.send(embed=embed)
        return

    items = wv_playerdata.extract_items(player_nbt)

    version = wv_blockcolors.detect_minecraft_version(_state)
    icons: dict[str, object] = {}
    newly_resolved = 0
    if version:
        item_ids = {item["id"] for item in items}
        newly_resolved = await asyncio.to_thread(wv_itemtextures.resolve_missing_icons, version, item_ids)
        icons = {iid: icon for iid in item_ids if (icon := wv_itemtextures.get_icon_for_item(iid)) is not None}

    try:
        png_bytes = await asyncio.to_thread(wv_inventoryimage.build_inventory_image, items, icons)
    except Exception as e:
        logger.error(f"failed to render inventory image for {player} ({e})")
        embed = ModifiedEmbeds.ErrorEmbed(title="画像の生成に失敗しました", description=str(e))
        await interaction.followup.send(embed=embed)
        return

    file = discord.File(io.BytesIO(png_bytes), filename="inventory.png")
    embed = ModifiedEmbeds.DefaultEmbed(title=f"{player} の所持アイテム")
    armor_lines = wv_playerdata.extract_armor_lines(items)
    if armor_lines:
        embed.add_field(name="防具・オフハンド", value="\n".join(armor_lines), inline=False)
    if version:
        icon_desc = f"実アイコン (MC {version})"
        if newly_resolved:
            icon_desc += f" ※新規{newly_resolved}種類を取得"
    else:
        icon_desc = "アイコンなし・色付き矩形で表示(バージョン未検出。/extension-world-visualizer config で手動指定できます)"
    embed.add_field(name="アイコン", value=icon_desc, inline=False)
    embed.set_footer(text=f"メイン欄+ホットバーのアイテム数: {len(items) - len(armor_lines)} (最終セーブ時点のデータです)")
    embed.set_image(url="attachment://inventory.png")
    await interaction.followup.send(embed=embed, file=file)
