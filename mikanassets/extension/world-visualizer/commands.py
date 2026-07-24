"""
world-visualizer — ワールド全体のマップ画像生成と、プレイヤー所持アイテムの画像表示を行う拡張機能。

## 何をする拡張か

- `/extension-world-visualizer map` — ワールドのリージョンファイル(.mca)を直接読み、
  チャンクごとの実ブロック色(草/葉/水など一部はバイオーム色を補正、標高による陰影は無し)
  から真上から見た俯瞰マップ画像を1枚合成して返す。
- `/extension-world-visualizer inventory` — 指定プレイヤーの playerdata(.dat)を直接読み、
  所持アイテム(メインインベントリ+ホットバー)をグリッド画像として、防具/オフハンドを
  embedのテキストとして返す。
- `/extension-world-visualizer config` — マップ描画に使うMinecraftバージョンの手動指定/
  確認、ブロック色キャッシュのクリアを行う(ネットワークへは一切アクセスしない)。

いずれもRCONやサーバーの標準出力に一切依存せず、`ctx.server_path` 配下のワールド保存ファイルを
直接パースする読み取り専用の実装(サーバー本体のファイルには一切書き込まない)。そのため
サーバーが停止中でも動作する(ただし当然、直近のオートセーブ以降の変更は反映されない)。

## ファイル構成

このディレクトリは単一の commands.py に収めず、責務ごとに分割している。

    commands.py           このファイル。Discordスラッシュコマンドの定義とstate.jsonのみ
    wv_nbt.py             チャンク/playerdata共通のNBTバイナリリーダー
    wv_imaging.py         Pillowの有無判定(他モジュールへ共有)
    wv_serverfiles.py      server.properties からのワールド名取得
    wv_worldmap.py         リージョンファイル読み取り・マップ画像合成
    wv_blockcolors.py      ブロック色の取得・キャッシュ(下記「配色の仕組み」参照)
    wv_playerdata.py       usercache.json/playerdata読み取り・アイテム抽出
    wv_inventoryimage.py   インベントリのグリッド画像合成

拡張フォルダ名 "world-visualizer" はハイフンを含み正式なPythonパッケージ名にできないため
(他拡張も含めこのリポジトリの全フォルダがハイフン区切りで、コアBotのロード機構は
commands.py を単体ファイルとして直接読み込んでいると見られる)、相対importではなく
このファイルの先頭で自分のディレクトリを sys.path に足したうえで、"wv_" prefix付きの
一意な名前で単純importしている。sys.pathへの追加はBotプロセス全体に影響するため、
他拡張機能やpipパッケージの同名モジュールと衝突しないよう prefix を付けている。

## 前提: Pillow (PIL) が必要

画像合成に Pillow を使う。Botの実行環境に無ければ

    pip install Pillow

を実行してから拡張を読み込み直すこと。未インストールの場合、全コマンドとも
「Pillow (PIL) がインストールされていません」というエラーembedを返すだけで、
他の拡張機能やBot本体には影響しない(wv_imaging.py で ImportError を握りつぶしている)。

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
- チャンクデータ形式は 1.18 以降を前提にしている(詳細は wv_worldmap.py 冒頭を参照)。

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
(`block_color_cache/colors.json` へ永続キャッシュ、バージョンをまたいで使い回す)。
存在しないテクスチャ名も `missing` として記録し、無限に再試行しない。

ネットワークに一切アクセスできない/Minecraftバージョンを検出できない環境では、解決できな
かったブロックは灰色(`wv_worldmap.UNKNOWN_BLOCK_COLOR`)で描画される(embedの「配色」欄で
確認できる)。詳細な設計意図は wv_blockcolors.py 冒頭のdocstringを参照。

## inventory の仕組みと制約

- `usercache.json` からプレイヤー名→UUIDを引き、playerdataを直接読む。RCONの
  `data get entity` と違い**オフライン中のプレイヤーでも参照できる**(サーバーが
  起動している必要すらない)。詳細は wv_playerdata.py 冒頭を参照。

## 権限レベル

いずれも読み取り専用のため、`whitelist-ops-viewer` と同様に権限チェックを設けていない
(誰でも実行可能)。個人〜身内数人規模のサーバー運用を前提としたこのリポジトリの他の
読み取り専用コマンドと揃えている。

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
from bot.utils import print_user
from core.state import ctx

# 自分のディレクトリを sys.path へ追加してから "wv_" prefix 付きの兄弟モジュールをimportする
# (理由は本docstring「ファイル構成」節を参照)。
_EXTENSION_DIR = Path(__file__).resolve().parent
if str(_EXTENSION_DIR) not in sys.path:
    sys.path.append(str(_EXTENSION_DIR))

import wv_blockcolors  # noqa: E402
import wv_inventoryimage  # noqa: E402
import wv_playerdata  # noqa: E402
import wv_worldmap  # noqa: E402
from wv_imaging import PIL_AVAILABLE  # noqa: E402

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

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

@tree.command(name="config", description="マップ描画に使うMinecraftバージョンの設定/確認、ブロック色キャッシュのクリアを行う")
@app_commands.describe(
    minecraft_version="手動で使うバージョン(例: 1.21.4)。省略時は version_history.json による自動検出を使う",
    clear_cache="蓄積したブロック色キャッシュを削除する(次回以降また必要な分だけ取得し直す)",
)
async def config_command(
    interaction: discord.Interaction,
    minecraft_version: str | None = None,
    clear_cache: bool = False,
) -> None:
    # このコマンドはローカルのファイル操作のみでネットワークへは一切アクセスしないため、
    # defer() は不要(即座に応答できる)。
    await print_user(logger, interaction.user)

    if minecraft_version is not None:
        _state["minecraft_version"] = minecraft_version.strip() or None
        _save_state()
    if clear_cache:
        wv_blockcolors.clear_color_cache()

    version = wv_blockcolors.detect_minecraft_version(_state)
    cache = wv_blockcolors.load_color_cache()

    embed = ModifiedEmbeds.DefaultEmbed(title="world-visualizer 設定")
    if version:
        embed.add_field(name="Minecraftバージョン", value=f"{version}" + (" (手動指定)" if _state.get("minecraft_version") else " (自動検出)"), inline=True)
    else:
        embed.add_field(name="Minecraftバージョン", value="未検出(minecraft_versionで手動指定してください)", inline=True)
    embed.add_field(name="キャッシュ済み色数", value=str(len(cache["colors"])), inline=True)
    embed.add_field(name="解決不能と判定済み", value=str(len(cache["missing"])), inline=True)
    await interaction.response.send_message(embed=embed)


# ── inventory コマンド ───────────────────────────────────────────────────────

@tree.command(name="inventory", description="プレイヤーの所持アイテムを画像で表示する")
@app_commands.describe(player="対象プレイヤー名")
async def inventory_command(interaction: discord.Interaction, player: str) -> None:
    await print_user(logger, interaction.user)
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
    try:
        png_bytes = await asyncio.to_thread(wv_inventoryimage.build_inventory_image, items)
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
    embed.set_footer(text=f"メイン欄+ホットバーのアイテム数: {len(items) - len(armor_lines)} (最終セーブ時点のデータです)")
    embed.set_image(url="attachment://inventory.png")
    await interaction.followup.send(embed=embed, file=file)
