import discord
from .utils import nbt, format_uuid
import os
import requests
from collections import deque
from io import BytesIO
import traceback
import asyncio

import subprocess
try:
    from PIL import Image,ImageDraw,ImageFont
except ImportError:
    subprocess.run(["pip", "install", "Pillow"])
    print("please restart bot")
    exit()

    
# 呼び出し元
from __main__ import extension_commands_group as tree
from __main__ import server_path
from __main__ import now_path
from __main__ import extension_logger
from __main__ import print_user


extension_path = os.path.join(now_path,"mikanassets","extension","minecraft-je")

# assets/pictureが存在しなければ作成
if not os.path.exists(os.path.join(extension_path,"assets","pictures")):
    os.makedirs(os.path.join(extension_path,"assets"),exist_ok=True)
    os.makedirs(os.path.join(extension_path,"assets","pictures"),exist_ok=True)


# メモリから読み出すコマンド群と、サーバーストレージから読み出すコマンド群
read_memory_group = discord.app_commands.Group(name="memory", description="read memory")
read_storage_group = discord.app_commands.Group(name="storage", description="read storage")

def properties_to_dict(filename):
    properties = {}
    with open(filename) as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith('#'):
                if line.startswith(' ') or line.startswith('\t'):
                    line = line[1:]
                key, value = line.split('=', 1)
                properties[key] = value
    return properties
# server.propertiesからlevel-nameを読み込む
file = properties_to_dict(os.path.join(server_path,"server.properties"))
level_name = file["level-name"]

read_storage_seed_logger = extension_logger.getChild("seed")
read_storage_inventory_logger = extension_logger.getChild("inventory")

@read_storage_group.command(name="seed", description="return world seed")
async def read_storage_world_settings(interaction: discord.Interaction):
    await print_user(read_storage_seed_logger,interaction.user)
    d_nbt = nbt(file_path=os.path.join(server_path,level_name,"level.dat")).to_dict()
    read_storage_seed_logger.info("reading seed...")
    try:
        seed = d_nbt[""]["Data"]["WorldGenSettings"]["seed"]
    except:
        seed = d_nbt[""]["Data"]["RandomSeed"]
    read_storage_seed_logger.info("seed : " + str(seed))
    await interaction.response.send_message(seed)

minecraft_dict = {
    "ender_eye":"eye_of_ender",
    "emerald_block":"block_of_emerald",
    "redstone_block":"block_of_redstone",
    "diamond_block":"block_of_diamond",
    "iron_block":"block_of_iron",
    "gold_block":"block_of_gold",
    "coal_block":"block_of_coal",
    "dye":"lapis_lazuli",
    "quartz":"nether_quartz",
    "leather_leggings":"leather_pants",
    "leather_helmet":"leather_cap",
    "written_book":"book",
    "wooden_button":"oak_button",
    "slime_ball":"slimeball",
}

image_tile_size = 64

@read_storage_group.command(name="inventory", description="return player inventory")
async def read_storage_inventory(interaction: discord.Interaction, mcid: str):
    await print_user(read_storage_inventory_logger,interaction.user)
    try:
        url = "https://api.mojang.com/users/profiles/minecraft/" + mcid
        r = requests.get(url)
        if r.status_code == 200:
            data = r.json()
            uuid = data["id"]
        else:
            await interaction.response.send_message("invalid mcid")
            return
        read_storage_inventory_logger.info("get mcid : " + mcid + f"({uuid})")
        try:
            d_nbt = nbt(file_path=os.path.join(server_path,level_name,"playerdata",format_uuid(uuid) + ".dat")).to_dict()
            read_storage_inventory_logger.info("nbt converted")
        except:
            await interaction.response.send_message("invalid mcid")
            return
        inventory = d_nbt[""]["Inventory"]
        inventory_object = deque()
        read_storage_inventory_logger.info("reading inventory...")
        await interaction.response.send_message("loading & downloading image...")
        for item in inventory:
            item_id = item["id"].split(":")[1]
            if item_id in minecraft_dict:
                item_id = minecraft_dict[item_id]
            try:
                item_count = item["Count"]
            except:
                item_count = item["count"]
            try:
                item_slot = item["Slot"]
            except:
                item_slot = item["slot"]
            filename = os.path.join(extension_path,"assets/pictures",str(item_id) + ".png")
            try:
                # ./assets/pictures/<itemid>.pngがあれば読み込む
                image = Image.open(filename)
            except:
                # 無ければダウンロードする
                def download_image(filename,item_id):
                    read_storage_inventory_logger.info("downloading image -> " + filename)
                    try:
                        # url = "https://minecraft.wiki/images/ItemSprite_" + str(item_id).replace("_","-") + ".png"
                        # urlData = requests.get(url).content
                        # if urlData == b"":
                        item_id = list(item_id)
                        item_id[0] = item_id[0].upper()
                        for leng in range(1, len(item_id) - 1):
                            if item_id[leng] == "_":
                                item_id[leng + 1] = item_id[leng + 1].upper()
                        item_id = "".join(item_id)
                        item_id = item_id.replace("Of","of").replace("_On_","_on_").replace("_A_","_a_")
                        url = "https://minecraft.wiki/images/Invicon_" + str(item_id) + ".png"
                        urlData = requests.get(url).content
                        if urlData == b"":
                            # 16*16の真っ白な画像を作る
                            image = Image.new("RGB", (16, 16), (255, 255, 255))
                            # assets/pictures/<itemid>.pngを作る
                            image.save(filename)
                        else:
                            with open(filename ,mode='wb') as f: # wb でバイト型を書き込める
                                f.write(urlData)
                            try:
                                image = Image.open(filename)
                            except:
                                image = Image.new("RGB", (16, 16), (255, 0, 255))
                            image = image.resize((16, 16),resample=Image.NEAREST)
                            image.save(filename)
                        # else:
                        #     with open(filename ,mode='wb') as f: # wb でバイト型を書き込める
                        #         f.write(urlData)
                    except Exception as e:
                        traceback.print_exc()
                download_image(filename,item_id)
                await asyncio.sleep(0.1)
                # ファイルが存在しなければ
                if not os.path.exists(filename):
                    # エラー
                    await interaction.response.send_message("download error")
                for _ in range(3):
                    try:
                        image = Image.open(filename)
                        break
                    except:
                        # これはファイルがエラーなので再ダウンロード
                        # ファイル削除
                        # await interaction.response.send_message("download error again")
                        download_image(filename,item_id)
                        await asyncio.sleep(0.1)
            inventory_object.append((image, item_count, item_slot))
        # 画像を合成
        image = Image.new("RGB", (16 * 9, 16 * 4), (255, 255, 255))
        for item in inventory_object:
            if item[2] <= 8:
                image.paste(item[0], (item[2] % 9 * 16, 3 * 16))
            else:
                image.paste(item[0], (item[2] % 9 * 16, item[2] // 9 * 16 - 16))
        read_storage_inventory_logger.info("item image pasted")
        # imageをリサイズ
        image = image.resize((image_tile_size * 9, image_tile_size * 4),resample=Image.NEAREST)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("arial.ttf", image_tile_size // 4)
        for item in inventory_object:
            # countを書き込む
            if item[2] <= 8:
                draw.text((item[2] % 9 * image_tile_size + (image_tile_size - image_tile_size // 4), 3 * image_tile_size + (image_tile_size - image_tile_size // 4)), str(item[1]), (255, 255, 255), font=font)
            else:
                draw.text((item[2] % 9 * image_tile_size + (image_tile_size - image_tile_size // 4), item[2] // 9 * image_tile_size + (image_tile_size - image_tile_size // 4) - image_tile_size), str(item[1]), (255, 255, 255), font=font)
        read_storage_inventory_logger.info("item count written")
        buffer = BytesIO()
        image.save(buffer, format="PNG")  # PNG形式で保存
        buffer.seek(0)  # バッファを先頭に移動
        await interaction.followup.send(content=f"inventory of {mcid}",file=discord.File(buffer, "inventory.png"))
        read_storage_inventory_logger.info("inventory sent")
    except Exception as e:
        await interaction.followup.send("error (uuid not found) -> " + str(e))





# コマンドを追加
tree.add_command(read_memory_group)
tree.add_command(read_storage_group)