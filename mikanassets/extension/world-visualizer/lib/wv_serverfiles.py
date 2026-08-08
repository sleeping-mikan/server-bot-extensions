"""wv_serverfiles — server.properties からのワールド名取得など、複数サブモジュールが
共通で使うサーバーファイル読み取りヘルパー(rcon拡張と同じ読み取りロジック)。"""

from __future__ import annotations

from bot.extension_api import ctx


def read_server_properties() -> dict[str, str]:
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


def level_name() -> str:
    return read_server_properties().get("level-name", "world") or "world"
