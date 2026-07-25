"""wv_mojangjar — Mojang公式クライアントjarへ、jar全体をダウンロードせずHTTP Rangeリクエスト
だけでアクセスするための共通処理。

`wv_blockcolors.py`(ブロックのテクスチャ平均色を抽出)と `wv_itemtextures.py`(アイテムの
テクスチャそのものを抽出)の両方が、同じ「バージョン→クライアントjar URL解決」「ZIPの
中央ディレクトリだけをRangeリクエストで取得し、必要なエントリだけ個別に取り出す」処理を
必要とするため、このモジュールに一本化した。設計意図(なぜ都度全体ダウンロードしないか等)は
`wv_blockcolors.py` 冒頭のdocstringを参照。
"""

from __future__ import annotations

import http.client
import json
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from core.state import ctx

logger = ctx.extension_logger

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
HTTP_TIMEOUT = 30.0
HTTP_HEADERS = {"User-Agent": "server-bot-extensions-pack/world-visualizer"}


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_client_jar_url(version: str, cache_dir: Path) -> str:
    """指定バージョンのクライアントjar URLを返す。一度解決したら `cache_dir/jar_urls.json`
    へ永続キャッシュし、以後同じバージョンではMojangのバージョンマニフェストへ再アクセスしない。
    呼び出し側(ブロック色/アイテムアイコン)ごとに別のキャッシュディレクトリを渡す想定。"""
    jar_urls_file = cache_dir / "jar_urls.json"
    cached: dict[str, str] = {}
    if jar_urls_file.exists():
        try:
            with jar_urls_file.open("r", encoding="utf-8") as f:
                cached = json.load(f)
        except Exception as e:
            logger.error(f"failed to read jar url cache ({e})")

    if version in cached:
        return cached[version]

    manifest = _http_get_json(VERSION_MANIFEST_URL)
    entry = next((v for v in manifest.get("versions", []) if v.get("id") == version), None)
    if entry is None:
        raise ValueError(f"Minecraft version {version!r} not found in Mojang's version manifest")
    version_meta = _http_get_json(entry["url"])
    client_url = version_meta["downloads"]["client"]["url"]

    cached[version] = client_url
    cache_dir.mkdir(parents=True, exist_ok=True)
    with jar_urls_file.open("w", encoding="utf-8") as f:
        json.dump(cached, f)
    return client_url


class HTTPRangeFile:
    """zipfile.ZipFile が要求した範囲だけをHTTP Rangeリクエストで都度取得する。
    central directory(ファイル一覧、バージョン毎に1回)と、実際にopen()した個々の
    エントリの圧縮データだけがネットワーク越しに転送され、jar全体は取得しない。
    大量の小さいRangeリクエストを連続で発行する用途のため、接続はkeep-aliveで持続する。"""

    def __init__(self, url: str) -> None:
        parts = urlsplit(url)
        self._path = parts.path + (f"?{parts.query}" if parts.query else "")
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        self._conn = conn_cls(parts.hostname, parts.port, timeout=HTTP_TIMEOUT)
        self._pos = 0
        self._size = self._fetch_range(0, 0)[0]

    def _fetch_range(self, start: int, end: int) -> tuple[int, bytes]:
        headers = {**HTTP_HEADERS, "Range": f"bytes={start}-{end}"}
        self._conn.request("GET", self._path, headers=headers)
        resp = self._conn.getresponse()
        data = resp.read()
        if resp.status not in (200, 206):
            raise ValueError(f"unexpected HTTP status {resp.status} for ranged request to {self._path}")
        content_range = resp.getheader("Content-Range")
        if not content_range:
            raise ValueError(f"server did not respond with Content-Range for {self._path} (Range requests unsupported?)")
        total = int(content_range.split("/")[-1])
        return total, data

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._size + offset
        else:
            raise ValueError(f"unsupported whence: {whence}")
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            end = self._size - 1
        elif n == 0:
            return b""
        else:
            end = min(self._pos + n - 1, self._size - 1)
        if self._pos > end or self._pos >= self._size:
            return b""
        _, data = self._fetch_range(self._pos, end)
        self._pos += len(data)
        return data

    def close(self) -> None:
        self._conn.close()


def open_remote_jar(version: str, cache_dir: Path) -> tuple[zipfile.ZipFile, HTTPRangeFile]:
    """指定バージョンのクライアントjarをRangeリクエスト経由で開く。戻り値の `HTTPRangeFile`
    は呼び出し側で使い終わったら必ず `close()` すること(keep-alive接続を保持しているため)。"""
    jar_url = get_client_jar_url(version, cache_dir)
    remote = HTTPRangeFile(jar_url)
    return zipfile.ZipFile(remote), remote
