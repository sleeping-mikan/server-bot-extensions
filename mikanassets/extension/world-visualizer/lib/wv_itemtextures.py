"""wv_itemtextures — アイテムのアイコン画像そのものを、Mojang公式配布のクライアントjarから
「本当に必要になった時だけ」その場で取り出してキャッシュするモジュール。ネットワーク
アクセスとjar操作の考え方は `wv_blockcolors.py` と共通(実体は `wv_mojangjar.py` を共有)
だが、こちらはテクスチャの平均色1個ではなく **PNGファイルそのもの** をディスクへ
キャッシュする点が異なる(インベントリ画像に実サイズで貼り付けるため)。

## アイコンの選び方: item/ を優先し、無ければ block/ の平面テクスチャで代用する

`minecraft:diamond_sword` のような「道具・食料」系アイテムは
`assets/minecraft/textures/item/diamond_sword.png` にGUI表示そのままのアイコンPNGが
存在するため、これをそのまま使う。

一方 `minecraft:dirt` のような「ブロックをそのまま置けるアイテム」は、実際のインベントリ
アイコンはブロックモデルを斜め上から描画した3Dアイコンであり、そのままの静止画は
jar内に存在しない(クライアント側でその場でレンダリングしている)。これを再現するのは
NBT/ZIP読み取りだけで完結する範囲を超えるため、`wv_blockcolors.py` がマップ描画で
採用しているのと同じ簡略化(3D等角ではなく `assets/minecraft/textures/block/` の平面
テクスチャをそのまま使う)を踏襲する。候補名の優先順位も `wv_blockcolors.TEXTURE_NAME_OVERRIDES`
(上面テクスチャの対応表)を流用する。

## 取得戦略

jarを開く(ZIP中央ディレクトリを取得する)コストを払う以上、その場で
`assets/minecraft/textures/item/` 配下の全PNG(item/ 側は大半のアイテムがここで解決する
本命なので一括取得する価値が高い)を取得してキャッシュする。ブロック代用が必要なキーは、
中央ディレクトリ自体は既に手元にあるため追加コスト無しで存在確認でき、該当する
`textures/block/*.png` だけを個別に取得する(block/ 側は稀にしか要らないため、
`wv_blockcolors.py` のように1269枚全部を毎回持ってくるほどの価値は無いと判断した)。

キャッシュは `cache/item_textures/textures/<texture_key>.png`(実PNGファイル)と
`cache/item_textures/index.json`(解決済み/解決不能キーの記録)。一度解決/missing判定
したキーは、以後 `/inventory` を何度実行してもネットワークへ一切アクセスしない。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from bot.extension_api import ctx

import wv_blockcolors
import wv_mojangjar
from wv_imaging import Image

logger = ctx.extension_logger

# __file__ は lib/wv_itemtextures.py なので、拡張ルート直下の cache/ を指すには1階層上がる
# (コードとキャッシュ/生成物を分離するため、キャッシュは lib/ の外に置く)。
_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "item_textures"
_TEXTURES_DIR = _CACHE_DIR / "textures"
_INDEX_FILE = _CACHE_DIR / "index.json"

_ITEM_TEXTURE_PREFIX = "assets/minecraft/textures/item/"
_BLOCK_TEXTURE_PREFIX = "assets/minecraft/textures/block/"


def icon_key(item_id: str) -> str:
    """アイテムID(例: "minecraft:diamond_sword")からキャッシュキー(例: "diamond_sword")
    を作る。名前空間以外の変換は行わない(block/ 側への代用候補は resolve_missing_icons
    が別途 wv_blockcolors.TEXTURE_NAME_OVERRIDES 等を使って探す)。"""
    return item_id.split(":", 1)[-1]


def _load_index() -> dict:
    if not _INDEX_FILE.exists():
        return {"resolved": [], "missing": []}
    try:
        with _INDEX_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {"resolved": data.get("resolved", []), "missing": data.get("missing", [])}
    except Exception as e:
        logger.error(f"failed to read item texture index ({e})")
        return {"resolved": [], "missing": []}


def _save_index(index: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _INDEX_FILE.open("w", encoding="utf-8") as f:
        json.dump(index, f)


def load_index() -> dict:
    return _load_index()


def clear_icon_cache() -> None:
    if _INDEX_FILE.exists():
        _INDEX_FILE.unlink()
    if _TEXTURES_DIR.exists():
        for f in _TEXTURES_DIR.glob("*.png"):
            f.unlink()
    jar_urls_file = _CACHE_DIR / "jar_urls.json"
    if jar_urls_file.exists():
        jar_urls_file.unlink()


def get_cached_icon(key: str) -> "Image.Image | None":
    """ネットワークへは一切アクセスせず、既にディスクへキャッシュ済みのアイコンだけを返す。
    先に resolve_missing_icons で解決しておく必要がある。"""
    path = _TEXTURES_DIR / f"{key}.png"
    if not path.exists():
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception as e:
        logger.error(f"failed to read cached item icon {key} ({e})")
        return None


def get_icon_for_item(item_id: str) -> "Image.Image | None":
    return get_cached_icon(icon_key(item_id))


def _first_frame(image: "Image.Image") -> "Image.Image":
    """アニメーション付きテクスチャ(正方形フレームを縦に連結したスプライトシート)は
    先頭フレームだけを使う(wv_blockcolors._average_texture_color と同じ考え方)。"""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    frame_h = min(h, w) if w > 0 else h
    if frame_h <= 0 or frame_h >= h:
        return rgba
    return rgba.crop((0, 0, w, frame_h))


def _save_icon(key: str, image: "Image.Image") -> None:
    _TEXTURES_DIR.mkdir(parents=True, exist_ok=True)
    _first_frame(image).save(_TEXTURES_DIR / f"{key}.png", format="PNG")


def _block_fallback_candidates(key: str) -> list[str]:
    override = wv_blockcolors.TEXTURE_NAME_OVERRIDES.get(key)
    candidates = [override] if override else []
    candidates += [f"{key}_top", key]
    return candidates


def resolve_missing_icons(version: str, item_ids: set[str]) -> int:
    """item_ids の中に解決済み/missingのどちらでもないキーが1つでもあれば、クライアントjar
    を開いて **assets/minecraft/textures/item/ 配下の全PNG** を一括取得し、キャッシュへ
    保存する(item/ が本命のため一括取得する価値が高い)。それでも見つからないキーは、
    同じ中央ディレクトリの中から block/ 側の代用テクスチャを個別に探して取得する
    (block/ は稀にしか要らないため一括取得はしない)。戻り値は新規に解決した件数。

    全キーが既知/missing判定済みならネットワークには一切触れない。
    重い処理(ネットワークI/O)を伴うので、呼び出し側で asyncio.to_thread に包むこと。
    """
    index = _load_index()
    resolved = set(index["resolved"])
    missing = set(index["missing"])
    keys = {icon_key(item_id) for item_id in item_ids}
    trigger = {key for key in keys if key not in resolved and key not in missing}
    if not trigger:
        return 0

    try:
        zf, remote = wv_mojangjar.open_remote_jar(version, _CACHE_DIR)
    except Exception as e:
        logger.error(f"failed to open remote client jar for {version} ({e})")
        return 0  # ネットワーク/バージョン解決の失敗はmissing扱いにしない(一時的な失敗の可能性があるため)

    newly_resolved = 0
    try:
        names = zf.namelist()
        item_names = {Path(n).stem: n for n in names if n.startswith(_ITEM_TEXTURE_PREFIX) and n.endswith(".png")}
        for key, name in item_names.items():
            if key in resolved or key in missing:
                continue
            try:
                with zf.open(name) as f:
                    image = Image.open(io.BytesIO(f.read()))
                _save_icon(key, image)
            except Exception as e:
                logger.error(f"failed to fetch/decode item texture {name} ({e})")
                continue  # 一時的な失敗の可能性があるのでmissingにはしない、次回また試す
            resolved.add(key)
            newly_resolved += 1

        block_names = {Path(n).stem: n for n in names if n.startswith(_BLOCK_TEXTURE_PREFIX) and n.endswith(".png")}
        for key in trigger:
            if key in resolved or key in missing:
                continue
            name = next((block_names[c] for c in _block_fallback_candidates(key) if c in block_names), None)
            if name is None:
                missing.add(key)
                continue
            try:
                with zf.open(name) as f:
                    image = Image.open(io.BytesIO(f.read()))
                _save_icon(key, image)
            except Exception as e:
                logger.error(f"failed to fetch/decode block-fallback texture {name} ({e})")
                continue
            resolved.add(key)
            newly_resolved += 1

        # trigger のうち、item/ 一括取得・block/ 個別取得のどちらでも見つからなかったものは
        # 対応表のヒューリスティックが外れて実在しない名前を探していたということなので
        # missing に記録する(そうしないと、次に同じアイテムに遭遇する度にjarを開き直す)。
        for key in trigger:
            if key not in resolved and key not in missing:
                missing.add(key)
    finally:
        remote.close()

    _save_index({"resolved": sorted(resolved), "missing": sorted(missing)})
    logger.info(f"resolved {newly_resolved} item icon(s) for {version} (full item texture set fetched, {len(resolved)} total cached)")
    return newly_resolved
