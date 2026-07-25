"""wv_blockcolors — 実ブロックのテクスチャ色を、Mojang公式配布のクライアントjarから
「本当に必要になった時だけ」その場で取り出して色を求め、ローカルにキャッシュし続けるモジュール。

## 設計方針: 知らないブロックが出てきた時だけAPIを叩く。叩く時は全部持ってくる

ブロックID→色の対応表を手作業で用意すると、バージョンアップでブロックが増える度に
メンテナンスが必要になる。そこで、Mojangが誰でも無認証でダウンロードできる形で公開している
クライアントjar(https://piston-meta.mojang.com/ のバージョンマニフェスト経由、ランチャー
自体が使うのと同じ公式配布物)を情報源にしつつ、**jar全体(数十MB)を毎回ダウンロードする
ことはしない**。JARはZIP形式であり、MojangのCDNはHTTP Rangeリクエストに対応しているため、
`zipfile.ZipFile` にRangeリクエストで範囲だけ取ってくるファイル様オブジェクト
(`wv_mojangjar.HTTPRangeFile`)を渡すことで、jar全体をダウンロードせずに済む。
jar取得まわりの共通処理は `wv_itemtextures.py`(アイテムアイコン取得)と共有するため
`wv_mojangjar.py` に切り出してある。

ネットワークへは「まだ見たことが無いブロックに遭遇した時」だけアクセスする。ただし
一度アクセスする以上、ZIPの中央ディレクトリ(ファイル一覧)は既に取得済みなので、
**そのタイミングでその場にある全ブロックテクスチャをまとめて取得してキャッシュする**
(1個ずつ都度取得する場合との差は、ブロックテクスチャ1269個で実測+18秒・+3.4MB程度で、
中央ディレクトリ自体の取得コスト(約3.3MB)と比べて誤差程度。中央ディレクトリを開く
コストを1個のためだけに払うのはもったいないので、開いたなら全部持ってきてしまう方針)。
HTTP接続はkeep-aliveで使い回す(`http.client.HTTPSConnection`を1個のブロックテクスチャ
毎に使い捨てず持続させる)ことで、大量の小さいRangeリクエストでも高速に処理できる。

取得した色は既知のブロック(テクスチャ名)についてはバージョンをまたいで永続キャッシュ
(`cache/block_colors/colors.json`)に貯め続けるため、一度取得しきったバージョンでは
以後 `/map` を何度実行してもネットワークへ一切アクセスしない(テクスチャの基本色が
version間で変わることは稀なので、多少古いバージョンで解決した色を使い回しても実用上
問題にならない、という判断)。存在しないテクスチャ名(対応表のヒューリスティックが
外れた場合)は `missing` として記録する。

取得した色はテクスチャ画像そのものではなく1テクスチャにつきRGB1個の平均値のみで、
元の画像を再構成できる情報量ではない(著作物であるテクスチャそのものの再配布ではなく、
そこから導出した統計値をローカルにキャッシュしているだけ)。

## 色そのものについて

標高(高さ)による陰影は行わない。バイオームによる色補正は、草ブロック上面・葉・水などの
「バイオーム色を掛け合わせる前提のグレースケール マスク」テクスチャ(BIOME_TINTED_BLOCKS)
に限って適用する(それ以外の大多数のブロックは実テクスチャの平均色をそのまま使う)。

経緯: 当初は標高陰影・バイオーム補正の両方を撤去し、テクスチャの平均色を常にそのまま
使う実装にしていたが、オーナー本人が実機で確認したところ、草ブロック上面・葉・水などの
素の平均色は緑や青ではなく中間的なグレーになる(これらはMinecraft本体側でも常に
バイオーム色を掛け合わせる前提の未着色テクスチャであるため)ため、マップ全体が
「緑が少なすぎる」灰色寄りの見た目になってしまった。オーナー本人から『バイオームだけ
戻してほしい』という指定を受け、標高陰影は撤去したまま、バイオームによるティントのみ
BIOME_TINTED_BLOCKSに該当するブロックに限定して復元した。
"""

from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

from core.state import ctx

import wv_mojangjar
import wv_serverfiles
from wv_imaging import Image
from wv_nbt import NBTReader

logger = ctx.extension_logger

# __file__ は lib/wv_blockcolors.py なので、拡張ルート直下の cache/ を指すには1階層上がる
# (コードとキャッシュ/生成物を分離するため、キャッシュは lib/ の外に置く)。
_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "block_colors"
_COLORS_FILE = _CACHE_DIR / "colors.json"

# ブロック名(minecraft:接頭辞を除いたもの)からテクスチャファイル名が単純一致しない
# もの、または上面(このマップは真上から見た図なので上面のテクスチャが欲しい)を
# 明示的に指定する対応表。ここに無いブロックは「テクスチャファイル名 == ブロック名」を
# 前提に直接引く(将来追加される大半の新規ブロックはこの命名規則に従うため、
# この対応表を都度更新しなくてもある程度は自動的に拾える)。
TEXTURE_NAME_OVERRIDES: dict[str, str] = {
    "grass_block": "grass_block_top",
    "podzol": "podzol_top",
    "mycelium": "mycelium_top",
    "dirt_path": "dirt_path_top",
    "farmland": "farmland",
    "water": "water_still",
    "lava": "lava_still",
    "frosted_ice": "frosted_ice_0",
    "muddy_mangrove_roots": "muddy_mangrove_roots_side",
}

# 実テクスチャがグレースケールの「マスク」で、実際の表示色はバイオーム毎の色を
# 掛け合わせて決まるブロック群(草ブロック上面・葉・水など)。Mojangのcolormap PNG
# (バイオームの温度/湿度から正確なティント色を引く仕組み)までは再現せず、
# wv_worldmap.BIOME_COLORS を代用のティント色源として乗算する簡易近似。
BIOME_TINTED_BLOCKS: set[str] = {
    "grass_block", "short_grass", "tall_grass", "fern", "large_fern", "grass",
    "oak_leaves", "spruce_leaves", "birch_leaves", "jungle_leaves",
    "acacia_leaves", "dark_oak_leaves", "mangrove_leaves",
    "vine", "lily_pad", "water", "sugar_cane", "kelp", "seagrass", "tall_seagrass",
}


# ── Mojang版マニフェストからバージョン検出/jar URL解決(結果はキャッシュする) ──────

def _detect_version_from_level_dat() -> str | None:
    """ワールドの `<level-name>/level.dat`(gzip圧縮NBT)から Data.Version.Name を読む。
    これは/mapが読みに行くワールドセーブ自体が持つ「実際に最後に保存された時点の
    バージョン」情報であり、ワールドが1回でも保存されていれば(=/mapがそもそも
    動作する前提として)必ず存在する。version_history.json はサーバーの構成によっては
    生成されない場合がある(実機検証で、起動・プレイ済みの vanilla サーバーでも
    version_history.json が存在しないケースを確認済み)ため、こちらを優先する。"""
    path = ctx.server_path / wv_serverfiles.level_name() / "level.dat"
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            data = gzip.decompress(f.read())
        root = NBTReader(data).read_root()
        version = root.get("Data", {}).get("Version", {}).get("Name")
        return str(version) if version else None
    except Exception as e:
        logger.error(f"failed to read level.dat version ({e})")
        return None


def detect_minecraft_version(state: dict) -> str | None:
    """マップ描画に使うMinecraftバージョン文字列を決める。ネットワークは使わない。

    1. state.json に手動設定(/extension-world-visualizer config)があればそれを最優先する。
    2. 無ければ world/level.dat の Data.Version.Name を読む(最も信頼できる自動検出手段、
       詳細は _detect_version_from_level_dat 参照)。
    3. それも読めなければ version_history.json の currentVersion を試す(生成される
       サーバー構成であれば有効な補助手段)。
    4. いずれも無ければ None(呼び出し側でブロック色取得を諦める)。
    """
    manual = state.get("minecraft_version")
    if manual:
        return str(manual)

    from_level_dat = _detect_version_from_level_dat()
    if from_level_dat:
        return from_level_dat

    path = ctx.server_path / "version_history.json"
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            version = data.get("currentVersion")
            if version:
                return str(version)
        except Exception as e:
            logger.error(f"failed to read version_history.json ({e})")

    return None


def _average_texture_color(image: "Image.Image") -> tuple[int, int, int] | None:
    """テクスチャ1枚の代表色(平均RGB)を計算する。water_still等のアニメーション
    テクスチャは正方形のフレームを縦に連結したスプライトシートなので、先頭フレーム
    (画像の幅を1辺とする正方形)だけを使う。完全透明なピクセルは平均から除外する。"""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    if w <= 0 or h <= 0:
        return None
    frame_h = min(h, w)
    rgba = rgba.crop((0, 0, w, frame_h))
    r_total = g_total = b_total = count = 0
    for r, g, b, a in rgba.getdata():
        if a < 16:
            continue
        r_total += r
        g_total += g
        b_total += b
        count += 1
    if count == 0:
        return None
    return r_total // count, g_total // count, b_total // count


# ── 色キャッシュ(バージョンをまたいで永続、colors + missing の2セクション) ────────

def load_color_cache() -> dict:
    if not _COLORS_FILE.exists():
        return {"colors": {}, "missing": []}
    try:
        with _COLORS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {"colors": data.get("colors", {}), "missing": data.get("missing", [])}
    except Exception as e:
        logger.error(f"failed to read block color cache ({e})")
        return {"colors": {}, "missing": []}


def save_color_cache(cache: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _COLORS_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f)


def clear_color_cache() -> None:
    if _COLORS_FILE.exists():
        _COLORS_FILE.unlink()
    jar_urls_file = _CACHE_DIR / "jar_urls.json"
    if jar_urls_file.exists():
        jar_urls_file.unlink()


def resolve_unknown_textures(version: str, texture_keys: set[str], cache: dict) -> None:
    """texture_keys の中に cache["colors"]/missing のどちらにも無いものが1つでもあれば、
    クライアントjarを開いて **assets/minecraft/textures/block/ 配下の全PNG** を一括取得し、
    まだキャッシュに無いものを cache["colors"] へ追加してディスクへ保存する。texture_keys
    自体は「取得しに行くべきかどうか」の判定にのみ使い、実際に取得する範囲はそれより広い
    (中央ディレクトリを開くコストを1個のためだけに払うのはもったいないため)。
    全キーが既知/missing判定済みならネットワークには一切触れない。呼び出し側で cache は
    使い回すこと(この関数は cache を直接書き換える)。

    重い処理(ネットワークI/O)を伴うので、呼び出し側で asyncio.to_thread に包むこと。
    """
    colors = cache.setdefault("colors", {})
    missing = set(cache.setdefault("missing", []))
    trigger = {key for key in texture_keys if key not in colors and key not in missing}
    if not trigger:
        return

    try:
        zf, remote = wv_mojangjar.open_remote_jar(version, _CACHE_DIR)
    except Exception as e:
        logger.error(f"failed to open remote client jar for {version} ({e})")
        return  # ネットワーク/バージョン解決の失敗はmissing扱いにしない(一時的な失敗の可能性があるため)

    try:
        names = [n for n in zf.namelist() if n.startswith("assets/minecraft/textures/block/") and n.endswith(".png")]
        newly_resolved = 0
        for name in names:
            key = Path(name).stem
            if key in colors or key in missing:
                continue
            try:
                with zf.open(name) as f:
                    image = Image.open(io.BytesIO(f.read()))
                color = _average_texture_color(image)
            except Exception as e:
                logger.error(f"failed to fetch/decode texture {name} ({e})")
                continue  # 一時的な失敗の可能性があるのでmissingにはしない、次回また試す
            if color is None:
                missing.add(key)
                continue
            colors[key] = list(color)
            newly_resolved += 1

        # trigger のうち、全ブロックテクスチャを一括取得し終えてもなお colors に
        # 無いものは、対応表のヒューリスティックが外れて実在しないテクスチャ名を
        # 探していたということなので missing に記録する(そうしないと、次に同じ
        # ブロックに遭遇する度に「未知のキーがある」と誤判定してjarを開き直して
        # しまう)。
        for key in trigger:
            if key not in colors and key not in missing:
                missing.add(key)
    finally:
        remote.close()

    cache["missing"] = sorted(missing)
    logger.info(f"resolved {newly_resolved} block texture color(s) for {version} (full block texture set fetched, {len(colors)} total cached)")
    save_color_cache(cache)


def resolve_block_color(
    block_name: str, colors: dict[str, list[int]], biome_color: tuple[int, int, int]
) -> tuple[tuple[int, int, int] | None, str]:
    """(色, texture_key) を返す。色が None の場合、texture_key は「まだ解決できていない
    ため後で resolve_unknown_textures に渡すべきキー」を表す。BIOME_TINTED_BLOCKS に
    該当するブロック(草ブロック上面・葉・水など)のみ biome_color を掛け合わせ、
    それ以外はテクスチャの平均色をそのまま返す。"""
    short = block_name.split(":", 1)[-1]
    texture_key = TEXTURE_NAME_OVERRIDES.get(short, short)
    for key in (texture_key, f"{short}_top", f"{short}_still", short):
        raw = colors.get(key)
        if raw is not None:
            r, g, b = raw
            if short in BIOME_TINTED_BLOCKS:
                br, bg, bb = biome_color
                return ((r * br) // 255, (g * bg) // 255, (b * bb) // 255), texture_key
            return (int(r), int(g), int(b)), texture_key
    return None, texture_key
