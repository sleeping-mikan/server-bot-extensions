"""
chat-bridge — サーバー内のチャットとDiscordのチャットを相互に共有する拡張機能。

## 対象ゲームはMinecraftに限らない

server-bot-v3 自体はMinecraft専用のBotではなく(rcon/world-visualizer/update-watch のように
Minecraft固有プロトコル・ファイル形式に依存する拡張もあれば、disk-space-watchdog や
auto-announce のように任意のサーバーで動く拡張もある)、この拡張はサーバー内チャットがテキストの
ログとして出力され、コンソール入力でチャット送信できるサーバーであればゲームを問わず使える設計に
している。state.json の各項目は全て config コマンドで書き換え可能で、コード側にはMinecraft固有の
処理は一切ハードコードしていない。_DEFAULT_STATE の既定値(下記参照)はあくまで最も利用者が多い
であろうバニラMinecraftサーバーを想定した「例」であり、他のゲームサーバーで使う場合は
/extension-chat-bridge config でログの検出パターン・チャット送信コマンドに合わせてこれらを
変更すること。

## どのログを読むか: core.log_tailer.LogTailer をほぼそのまま使う

server-bot-v3 は拡張機能から再利用できる `core.log_tailer.LogTailer` を提供している
(`async for line in tailer:` で新規行を待ち続ける非同期イテレータ)。既定のソースは
`LogManager.snapshot_log_msg()` — bot/sys/server/cmd/extension 等**全ロガーが混ざった**、
直近100件の共有バッファで、サーバーの標準出力も server ロガー経由でここに常に流れ込む
(server/stdout.py の `server_log.info(line)` はソース確認済みで、`.config` の `log.server`
フラグ〈ファイルへも書き出すかどうか〉に一切左右されず常に実行される。つまりこの経路には
「ログが存在しない」という状態がほぼ無い — bot が起動してsysロガーが何か1行でも出した時点で
このバッファは空でなくなる)。

このバッファは Discord/コンソール表示用に ANSI カラーコードが付いた文字列
(`core/log_setup.py` の `ServerConsoleFormatter`/`ColoredFormatter`、実体は `\033[...m`)
になっているが、これは無視せず _strip_ansi() で素直に取り除いてから正規表現に通せば済む話
なので、独自のログファイル探索(以前の実装で行っていた `ctx.server_path/logs/server *.log`
のglob)は行わずこの共有バッファをそのまま使うようにした。他ロガーの行(bot起動メッセージ、
コマンド実行ログ等)も同じバッファに混ざるが、game_log_pattern は「サーバーのログ行に
現れる特定の並び」に一致するものだけを search() で拾うため、素通しでも実害は無い
(既定パターンの `[HH:MM:SS] [Server thread/INFO]: <name> chat` はサーバー以外のロガーの
行には出現しない)。

この設計により、以前のファイルベース実装が抱えていた2つの制約が両方解消されている:
  1. `.config` の `log.server` を true にしておく必要が無くなった(ファイルの有無に依存しない)
  2. ファイル名パターン(`server <起動日時>.log`)という server-bot-v3 の非公開の実装詳細に
     依存しなくなった(LogManager.snapshot_log_msg() は LogTailer 自身の既定ソースであり、
     拡張機能向けの正式な入口)

代わりに次の制約を受け入れている(個人〜身内数人規模のサーバー運用を前提とした割り切り、
詳細は plan.json 参照): 共有バッファは全ロガー合算で `maxlen=100` なので、チャット以外の
ログ(コマンド実行・拡張ロード等)を含めて2秒間(poll_interval)に100行を超えて出力される
ような極端なバーストがあると、古い行が読む前に押し出されて一部のチャットを取りこぼす可能性が
ある。個人サーバーの通常利用でこの閾値に達することはまず無い。

`async for line in tailer:` は開始した瞬間に**その時点でバッファに既にある内容を丸ごと最初の
バッチとして返す**ため、拡張を有効化した直後に既存の内容を一括でDiscordへ流さないよう、
tail開始時点の最後の行を記録しておき、それが来るまでは処理をスキップしている(下記 `_tail_loop`)。
`enabled` が false の間は tailer 自体を作らない(無効中に発生した分は再有効化時にも読み返さない
= 取りこぼしは再送しない、という単純な仕様にしている)。

## 判定方法

- サーバー→Discord: state.json の game_log_pattern(正規表現。{name} と {chat} という
  プレースホルダを埋め込む)を、ANSIコードを取り除いた新規ログ行に対して search() する。
  {name}/{chat} はそれぞれ (?P<name>.+?) / (?P<chat>.+) という名前付きキャプチャグループに
  置換されるので、それ以外の部分は素の正規表現としてそのまま使える(既定値はバニラMinecraft
  (Log4j2)の `<プレイヤー名> 発言内容` 形式を例にした
  r"\\[\\d{2}:\\d{2}:\\d{2}\\] \\[Server thread/INFO\\]: <{name}> {chat}"。他のゲームサーバーでは
  ログ形式に合わせて config で書き換える)。マッチした場合のみ discord_message_format
  (プレースホルダ {server} {channel} {name} {chat} が使える。既定値は `<{name}> {chat}` の
  シンプルな表示。送信先チャンネルが discord_channel_id 1個だけに固定されており発言元が
  常に単一のサーバーなので、game_command_format(下記)のような発言元disambiguationは
  デフォルトでは不要という判断)で整形してDiscordチャンネルへ送信する。
- Discord→サーバー: **サーバー→Discordとは非対称の設計**。サーバー→Discordの送信先は
  discord_channel_id で指定する単一チャンネルだが、Discord→サーバーは特定チャンネルに絞らず
  **bot が参加している全サーバー(Discordギルド)の全テキストチャンネル**を対象にする(権限的に
  読めるチャンネルのみ、`channel.permissions_for(guild.me)` で view_channel /
  read_message_history を確認してから対象にする)。複数チャンネル・複数Discordサーバーから
  発言が混ざって届くため、game_command_format の既定値は `{server}`(発言があった
  Discordサーバー名 = message.guild.name)から始まる書式にしてあり、サーバー内でどこからの
  発言か分かるようにしている(`say {server} #{channel} ✧ <{name}> {chat}`)。
  on_message イベントは使わない。理由は、拡張機能には Discord イベントを追加購読する公式な
  仕組みが無く(discord.Client には add_listener が存在しない。bot本体は bot/events.py で
  `@client.event async def on_message` を既にモジュールロード時に登録しており、
  ターミナルチャンネル機能〈/terminal set〉がこれで実装されている。拡張側で client.on_message を
  上書きするとこの機能を壊しかねない)、代わりに append_task のループで対象チャンネル毎に
  channel.history() を定期的にポーリングし、前回チェック時より新しいメッセージ(bot自身の
  発言は除く)だけを game_command_format で整形して bot.extensions.write_server_in() で
  サーバーへ書き込む。write_server_in は改行を複数コマンドの区切りとして解釈してしまう
  (plan.json known_limitations)ため、発言内容の改行は必ず除去してから渡す。

**注意(範囲が広いことについて)**: 「bot が参加している全てのDiscordサーバーの全チャンネル」を
対象にするため、モデレーター専用チャンネルなど本来サーバー内に見せたくない内容のあるチャンネルに
botを招待している場合、その発言もサーバー内チャットへそのまま転送される。個人〜身内数人規模の
運用を前提にした割り切りなので、公開性の高い環境で使う場合は転送したくないチャンネルからbotを
外す、権限でread_message_historyを与えないなどして調整すること。

既定の書式同士(Minecraftの例: `say {server} #{channel} ✧ <{name}> {chat}` を送信 → ログには
`[Server thread/INFO]: [Server] Guild #general ✧ <name> chat` のように余分な要素が挟まって
出力される)は game_log_pattern の `: <{name}>` という並びに一致しないため、
Discordから転送した発言がそのままサーバー→Discordへ折り返される自己ループは起きない。
ただしこれは既定値同士の組み合わせでたまたま成立している性質であり、
game_log_pattern / game_command_format を変更する場合は自己ループが起きないか
/extension-chat-bridge test で確認すること。

## 前提 (Discord側)

Discordメッセージの内容 (message.content / clean_content) を読み取るには Message Content
Intent が必要。bot/client.py で `intents.message_content = True` は既にコード側で設定済みなので、
残る前提は Discord Developer Portal 側でこのBotアプリケーションの Message Content Intent を
オンにすることだけ(こちらはコードでは有効化できない)。無効な場合、サーバー→Discordは動作するが
Discord→サーバーの発言内容が常に空になり中継されない。

## 権限レベル

rcon / scheduled-backup / update-watch と同じく、各コマンドの要求権限レベルは state.json では
なく **.config** の discord_commands.permission.commands_level に
"extension-chat-bridge <サブコマンド名>": <レベル> というキーで管理する。キー名は実際の
スラッシュコマンド名(/extension-chat-bridge config 等)とそのまま一致させている。デフォルト値は
_KNOWN_PERMISSIONS にまとめてあり、拡張ロード時に .config にまだ無いキーがあれば自動的にこの
デフォルト値で書き足し、その場で .config ファイルへ即座に反映する(詳細は
_register_missing_permission_keys() / _perm() を参照)。つまり管理者は .config を開けば
"extension-chat-bridge config" 等のキーが既に存在した状態になっており、値を書き換えるだけでよい
(何もしなければデフォルトのまま動く)。

    extension-chat-bridge status   0
    extension-chat-bridge test     0
    extension-chat-bridge config   1

登録される全コマンド: /extension-chat-bridge <config|status|test>
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from bot.client import client
from bot.embeds import ModifiedEmbeds
from bot.extensions import append_task, write_server_in
from bot.utils import not_enough_permission, print_user, rewrite_config, user_permission
from core.log_setup import LogManager
from core.log_tailer import LogTailer, LogTailerEmptyError
from core.state import ctx

# ロード時のみ ctx にセットされる値なので、モジュール先頭で変数に保持しておく
tree = ctx.extension_commands_group
logger = ctx.extension_logger

# chat-bridge の各コマンドが要求する権限レベルのデフォルト値(唯一の定義元)。
# state.json ではなく .config の discord_commands.permission.commands_level 側で管理する
# (rcon / scheduled-backup / update-watch と同じ方式)。.config に同名キーが無ければ
# ここに登録し、そのままファイルへも書き戻す(_register_missing_permission_keys 参照)。
_KNOWN_PERMISSIONS: dict[str, int] = {
    "extension-chat-bridge status": 0,
    "extension-chat-bridge test": 0,
    "extension-chat-bridge config": 1,
}

_STATE_FILE = Path(__file__).parent / "state.json"
_LOG_TICK_SECONDS = 2
_DISCORD_POLL_SECONDS = 3
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# 以下はいずれも config で変更可能。game_log_pattern/game_command_format の既定値は
# バニラMinecraft(Log4j2)を例にしただけで、他のゲームサーバーではログ形式・チャット送信
# コマンドに合わせて書き換えることを前提にしている(コード側にゲーム固有の処理はハードコード
# していない)。
_DEFAULT_STATE = {
    "enabled": False,
    "discord_channel_id": None,
    "server_display_name": None,
    "game_log_pattern": r"\[\d{2}:\d{2}:\d{2}\] \[Server thread/INFO\]: <{name}> {chat}",
    "discord_message_format": "<{name}> {chat}",
    "game_command_format": "say {server} #{channel} ✧ <{name}> {chat}",
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
# Discordメッセージのポーリング用。チャンネルIDごとに前回チェックした時点の最新メッセージIDを持つ
# (Discord→サーバーは特定チャンネルに絞らず、bot参加中の全Discordサーバーの全チャンネルを
# 対象にするため)。
_last_discord_message_ids: dict[int, int] = {}


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _compile_chat_pattern(template: str) -> re.Pattern | None:
    if "{name}" not in template or "{chat}" not in template:
        return None
    pattern_src = (
        template.replace("{name}", "\x00NAME\x00")
        .replace("{chat}", "\x00CHAT\x00")
        .replace("\x00NAME\x00", "(?P<name>.+?)")
        .replace("\x00CHAT\x00", "(?P<chat>.+)")
    )
    try:
        return re.compile(pattern_src)
    except re.error:
        return None


def _display_name() -> str:
    # ctx.server_name はサーバー本体のファイル名(例: server.jar)であり表示名としては
    # 見栄えが悪いため、フォールバック先には使わない。見た目にこだわるなら config で
    # server_display_name を設定すること。
    return _state["server_display_name"] or "Server"


def _render(template: str, **kwargs: str) -> str:
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"chat-bridge: template render failed ({e}): {template!r}")
        return f"<{kwargs.get('name', '')}> {kwargs.get('chat', '')}"


async def _get_channel(channel_id: int) -> discord.abc.Messageable | None:
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.HTTPException as e:
            logger.error(f"chat-bridge: discord channel fetch failed ({e})")
            return None
    return channel


async def _relay_to_discord(name: str, chat: str) -> None:
    channel_id = _state["discord_channel_id"]
    if not channel_id:
        return
    channel = await _get_channel(channel_id)
    if channel is None:
        return
    text = _render(
        _state["discord_message_format"],
        server=_display_name(),
        channel=getattr(channel, "name", str(channel_id)),
        name=name,
        chat=chat,
    )
    await channel.send(text)


async def _handle_log_line(line: str) -> None:
    pattern = _compile_chat_pattern(_state["game_log_pattern"])
    if pattern is None:
        return
    m = pattern.search(_strip_ansi(line))
    if not m:
        return
    try:
        name = m.group("name").strip()
        chat = m.group("chat").strip()
    except IndexError:
        return
    if not name or not chat:
        return
    await _relay_to_discord(name, chat)


@tasks.loop(seconds=_LOG_TICK_SECONDS)
async def _tail_loop() -> None:
    # LogTailer.__aiter__ は「新規行が来るまで内部で待ち続ける」無限非同期イテレータのため、
    # このコルーチン本体は基本的に戻ってこない(discord.ext.tasks.Loop はコルーチンが
    # 一度も戻らなくても append_task 経由の .cancel() で正しく中断できるため、
    # /restart 等でのライフサイクル管理はそのまま機能する)。seconds=_LOG_TICK_SECONDS は
    # 無効時の待機や LogTailerEmptyError からのリトライ間隔として使っている。
    while True:
        if not _state["enabled"] or not _state["discord_channel_id"]:
            await asyncio.sleep(_LOG_TICK_SECONDS)
            continue

        tailer = LogTailer(poll_interval=_LOG_TICK_SECONDS)  # ソースは既定の LogManager.snapshot_log_msg
        # tail開始時点でバッファに既にある内容は、最初のバッチとしてまるごと返ってくるため
        # 一括流し込みを避けるべく読み飛ばす(最後の行が来るまでスキップ)
        current = LogManager.snapshot_log_msg()
        skip_until = current[-1] if current else None
        try:
            async for line in tailer:
                if not _state["enabled"] or not _state["discord_channel_id"]:
                    break  # 無効化された。tailerを作り直すため外側のwhileへ戻る
                if skip_until is not None:
                    if line == skip_until:
                        skip_until = None
                    continue
                await _handle_log_line(line)
        except LogTailerEmptyError:
            # bot起動直後などでバッファがまだ空。少し待って再試行(通常は起きない)
            await asyncio.sleep(_LOG_TICK_SECONDS)
        except Exception as e:
            logger.error(f"chat-bridge: tail loop error ({e})")
            await asyncio.sleep(_LOG_TICK_SECONDS)


append_task(_tail_loop)


async def _relay_to_game(message: discord.Message) -> None:
    if message.author.bot or message.webhook_id is not None:
        return

    content = " ".join(message.clean_content.splitlines()).strip()[:256]
    if not content:
        return

    command = _render(
        _state["game_command_format"],
        server=message.guild.name if message.guild else "DM",
        channel=getattr(message.channel, "name", str(message.channel.id)),
        name=message.author.display_name,
        chat=content,
    )
    command = " ".join(command.splitlines())  # write_server_in の改行注入対策の保険
    ok, reason = write_server_in(command)
    if not ok:
        logger.info(f"chat-bridge: relay to server skipped ({reason})")


def _iter_target_channels():
    """bot が参加している全サーバーのうち、読み取り権限があるテキストチャンネルを列挙する。"""
    for guild in client.guilds:
        me = guild.me
        for channel in guild.text_channels:
            if me is not None:
                perms = channel.permissions_for(me)
                if not (perms.view_channel and perms.read_message_history):
                    continue
            yield channel


@tasks.loop(seconds=_DISCORD_POLL_SECONDS)
async def _discord_poll_loop() -> None:
    # 特定チャンネルに絞らず、参加している全サーバーの全チャンネルを毎回まとめてポーリングする。
    # 参加サーバー・チャンネル数が多いbotではAPI呼び出し回数がその分増える点に注意
    # (個人〜身内数人規模の運用を想定した割り切り、詳細はモジュールdocstring参照)。
    if not _state["enabled"]:
        return

    for channel in _iter_target_channels():
        last_id = _last_discord_message_ids.get(channel.id)
        try:
            if last_id is None:
                # このチャンネルを見るのが初めて。履歴は流し込まず基準点だけ記録する
                last_seen = 0
                async for m in channel.history(limit=1):
                    last_seen = m.id
                _last_discord_message_ids[channel.id] = last_seen
                continue

            async for message in channel.history(limit=50, after=discord.Object(id=last_id), oldest_first=True):
                _last_discord_message_ids[channel.id] = message.id
                await _relay_to_game(message)
        except discord.Forbidden:
            continue
        except discord.HTTPException as e:
            logger.error(f"chat-bridge: history fetch failed for #{channel} ({e})")
            continue


append_task(_discord_poll_loop)


def _register_missing_permission_keys() -> None:
    """.config に無い chat-bridge の権限キーを _KNOWN_PERMISSIONS の値で登録し、即座に .config へ書き戻す。

    rcon 拡張と同じ理由・同じ仕組み: コアBotの起動時補完(core/config_loader.py)は
    コア本体の command_desc に登録されたコマンドしか対象にしないため、拡張機能側の
    キーはここで自前で ctx.text.command_permission(= .config の commands_level と
    同一のdict)へ補完する。拡張のロードは main.py の同期的な起動フェーズ内で行われ、
    この時点でDiscordのイベントループはまだ動いていないため、async def だが中身は
    同期的なファイル書き込みでしかない rewrite_config() を asyncio.run() で問題なく呼べる。
    """
    added = False
    for key, default in _KNOWN_PERMISSIONS.items():
        if key not in ctx.text.command_permission:
            ctx.text.command_permission[key] = default
            added = True
    if added:
        logger.info("registered missing chat-bridge permission keys, writing to .config")
        asyncio.run(rewrite_config())


_register_missing_permission_keys()


def _perm(key: str) -> int:
    """コマンドごとの要求権限レベルを .config から読む(未登録キーは有り得ない前提)。"""
    return ctx.text.command_permission.get(key, _KNOWN_PERMISSIONS[key])


async def _check_permission(interaction: discord.Interaction, required: int) -> bool:
    await print_user(logger, interaction.user)
    if await user_permission(interaction.user) < required:
        await not_enough_permission(interaction, logger)
        return False
    return True


@tree.command(name="status", description="chat-bridge の現在の設定と稼働状況を表示する")
async def status_command(interaction: discord.Interaction) -> None:
    if not await _check_permission(interaction, _perm("extension-chat-bridge status")):
        return

    buffered = len(LogManager.snapshot_log_msg())
    target_channels = list(_iter_target_channels()) if _state["enabled"] else []
    guild_count = len({c.guild.id for c in target_channels})

    embed = ModifiedEmbeds.DefaultEmbed(title="chat-bridge 状態")
    embed.add_field(name="有効", value=str(_state["enabled"]), inline=True)
    embed.add_field(
        name="サーバー→Discord 転送先",
        value=f"<#{_state['discord_channel_id']}>" if _state["discord_channel_id"] else "未設定",
        inline=True,
    )
    embed.add_field(
        name="Discord→サーバー 監視対象",
        value=f"{guild_count}Discordサーバー / {len(target_channels)}チャンネル(参加Discordサーバーの全チャンネル)",
        inline=True,
    )
    embed.add_field(name="サーバー表示名", value=_display_name(), inline=True)
    embed.add_field(name="共有ログバッファ件数", value=f"{buffered}/100 (全ロガー合算)", inline=True)
    embed.add_field(name="サーバーログ判定パターン", value=f"`{_state['game_log_pattern']}`", inline=False)
    embed.add_field(name="Discord表示書式", value=f"`{_state['discord_message_format']}`", inline=False)
    embed.add_field(name="サーバー送信コマンド書式", value=f"`{_state['game_command_format']}`", inline=False)
    embed.set_footer(text="権限レベルは .config の discord_commands.permission.commands_level で設定してください")
    await interaction.response.send_message(embed=embed)


@tree.command(name="test", description="サーバーログ判定パターンをサンプル行に対して試す")
async def test_command(interaction: discord.Interaction, sample_line: str) -> None:
    if not await _check_permission(interaction, _perm("extension-chat-bridge test")):
        return

    pattern = _compile_chat_pattern(_state["game_log_pattern"])
    if pattern is None:
        embed = ModifiedEmbeds.ErrorEmbed(
            title="game_log_pattern が不正です",
            description="{name} と {chat} を両方含む、有効な正規表現になっていません",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    m = pattern.search(_strip_ansi(sample_line))
    if not m:
        embed = ModifiedEmbeds.ErrorEmbed(
            title="マッチしませんでした",
            description=f"パターン: `{_state['game_log_pattern']}`",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    name = m.group("name").strip()
    chat = m.group("chat").strip()
    preview = _render(_state["discord_message_format"], server=_display_name(), channel="channel", name=name, chat=chat)

    embed = ModifiedEmbeds.DefaultEmbed(title="マッチしました")
    embed.add_field(name="name", value=name or "(空)", inline=True)
    embed.add_field(name="chat", value=chat or "(空)", inline=True)
    embed.add_field(name="Discord送信プレビュー", value=preview, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="config", description="有効/無効・通知先・ログ判定パターン・書式を設定する")
@app_commands.describe(
    enabled="サーバー内チャットとDiscordの相互共有を有効にするか",
    discord_channel="サーバー内チャットの転送先Discordチャンネル(サーバー→Discord専用。"
    "Discord→サーバーは特定チャンネルに絞らず、bot参加中の全Discordサーバーの全チャンネルが対象)",
    game_log_pattern="サーバーログの1行からチャットを検出する正規表現。{name} と {chat} を埋め込む",
    discord_message_format="サーバー→Discordの表示書式。{server}(サーバー表示名) {channel} {name} {chat} が使える",
    game_command_format="Discord→サーバーで実行する標準入力コマンドの書式。"
    "{server}(発言元のDiscordサーバー名) {channel} {name} {chat} が使える",
    server_display_name="discord_message_format の {server} に埋め込むサーバー表示名(未指定なら\"Server\")",
)
async def config_command(
    interaction: discord.Interaction,
    enabled: bool | None = None,
    discord_channel: discord.TextChannel | None = None,
    game_log_pattern: str | None = None,
    discord_message_format: str | None = None,
    game_command_format: str | None = None,
    server_display_name: str | None = None,
) -> None:
    if not await _check_permission(interaction, _perm("extension-chat-bridge config")):
        return

    if game_log_pattern is not None:
        if _compile_chat_pattern(game_log_pattern) is None:
            embed = ModifiedEmbeds.ErrorEmbed(
                title="game_log_pattern が不正です",
                description="{name} と {chat} を両方含む、有効な正規表現を指定してください",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        _state["game_log_pattern"] = game_log_pattern

    for key, value in (
        ("discord_message_format", discord_message_format),
        ("game_command_format", game_command_format),
    ):
        if value is None:
            continue
        try:
            value.format(server="x", channel="x", name="x", chat="x")
        except (KeyError, IndexError, ValueError) as e:
            embed = ModifiedEmbeds.ErrorEmbed(title=f"{key} が不正です", description=str(e))
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        _state[key] = value

    if discord_channel is not None:
        _state["discord_channel_id"] = discord_channel.id
    if server_display_name is not None:
        _state["server_display_name"] = server_display_name or None

    if enabled is not None:
        _state["enabled"] = enabled
        if enabled and _state["discord_channel_id"] is None and interaction.channel is not None:
            _state["discord_channel_id"] = interaction.channel.id

    _save_state()
    await status_command.callback(interaction)
