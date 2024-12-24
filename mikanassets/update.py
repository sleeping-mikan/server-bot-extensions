import sys
import os
import platform
import discord
intents = discord.Intents.default() 
client = discord.Client(intents=intents)

from enum import Enum
from datetime import datetime
import logging

time = datetime.now().strftime("%Y-%m-%d_%H_%M_%S")

#プロンプトを送る
print()

now_path = "/".join(__file__.replace("\\","/").split("/")[:-1])

if platform.system() == "Windows":
    temp_path = os.environ.get('TEMP') + "/mcserver"
else:
    temp_path = "/tmp/mcserver"

def make_config():
    import json
    if not os.path.exists(now_path + "/../" + ".config"):
        file = open(now_path + "/../"  + ".config","w")
        config_dict = {"allow":{"ip":True},"server_path":now_path + "/../","allow_mccmd":["list","whitelist","tellraw","w","tell"],"server_name":"bedrock_server.exe","log":{"server":True,"all":False}}
        json.dump(config_dict,file,indent=4)
    else:
        config_dict = json.load(open(now_path + "/../"  + ".config","r"))
    return config_dict


config = make_config()

try:
    server_path = config["server_path"]
    server_name = config["server_name"]
    log = config["log"]
    log["all"]
except KeyError:
    exit("config file is broken. please delete .config and try again.")

#token
token = open(now_path + "/../.token","r").read()

class Color(Enum):
    BLACK          = '\033[30m'#(文字)黒
    RED            = '\033[31m'#(文字)赤
    GREEN          = '\033[32m'#(文字)緑
    YELLOW         = '\033[33m'#(文字)黄
    BLUE           = '\033[34m'#(文字)青
    MAGENTA        = '\033[35m'#(文字)マゼンタ
    CYAN           = '\033[36m'#(文字)シアン
    WHITE          = '\033[37m'#(文字)白
    COLOR_DEFAULT  = '\033[39m'#文字色をデフォルトに戻す
    BOLD           = '\033[1m'#太字
    UNDERLINE      = '\033[4m'#下線
    INVISIBLE      = '\033[08m'#不可視
    REVERCE        = '\033[07m'#文字色と背景色を反転
    BG_BLACK       = '\033[40m'#(背景)黒
    BG_RED         = '\033[41m'#(背景)赤
    BG_GREEN       = '\033[42m'#(背景)緑
    BG_YELLOW      = '\033[43m'#(背景)黄
    BG_BLUE        = '\033[44m'#(背景)青
    BG_MAGENTA     = '\033[45m'#(背景)マゼンタ
    BG_CYAN        = '\033[46m'#(背景)シアン
    BG_WHITE       = '\033[47m'#(背景)白
    BG_DEFAULT     = '\033[49m'#背景色をデフォルトに戻す
    RESET          = '\033[0m'#全てリセット
    def __add__(self, other):
        if isinstance(other, Color):
            return self.value + other.value
        elif isinstance(other, str):
            return self.value + other
        else:
            raise NotImplementedError
    def __radd__(self, other):
        if isinstance(other, Color):
            return other.value + self.value
        elif isinstance(other, str):
            return other + self.value
        else:
            raise NotImplementedError
        
class Formatter():
    levelname_size = 8
    name_size = 10
    class ColoredFormatter(logging.Formatter):
    # ANSI escape codes for colors
        COLORS = {
            'DEBUG': Color.BOLD + Color.WHITE,   # White
            'INFO': Color.BOLD + Color.BLUE,    # Blue
            'WARNING': Color.BOLD + Color.YELLOW, # Yellow
            'ERROR': Color.BOLD + Color.RED,   # Red
            'CRITICAL': Color.BOLD + Color.MAGENTA # Red background
        }
        RESET = '\033[0m'  # Reset color
        BOLD_BLACK = Color.BOLD + Color.BLACK  # Bold Black

        def format(self, record):
            # Format the asctime
            record.asctime = self.formatTime(record, self.datefmt)
            bold_black_asctime = f"{self.BOLD_BLACK}{record.asctime}{self.RESET}"
            
            # Adjust level name to be 8 characters long
            original_levelname = record.levelname
            padded_levelname = original_levelname.ljust(Formatter.levelname_size)
            original_name = record.name
            padded_name = original_name.ljust(Formatter.name_size)
            
            # Apply color to the level name only
            color = self.COLORS.get(original_levelname, self.RESET)
            colored_levelname = f"{color}{padded_levelname}{self.RESET}"
            
            # Get the formatted message
            message = record.getMessage()
            
            # Create the final formatted message
            formatted_message = f"{bold_black_asctime} {colored_levelname} {padded_name}: {message}"
            
            return formatted_message
    class DefaultConsoleFormatter(logging.Formatter):
        def format(self, record):
            # Format the asctime
            record.asctime = self.formatTime(record, self.datefmt)
            
            # Adjust level name to be 8 characters long
            original_levelname = record.levelname
            padded_levelname = original_levelname.ljust(Formatter.levelname_size)
            original_name = record.name
            padded_name = original_name.ljust(Formatter.name_size)
            
            
            # Get the formatted message
            message = record.getMessage()
            
            # Create the final formatted message
            formatted_message = f"{record.asctime} {padded_levelname} {padded_name}: {message}"
            
            return formatted_message
#logger
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = Formatter.ColoredFormatter(f'{Color.BOLD + Color.BG_BLACK}%(asctime)s %(levelname)s %(name)s: %(message)s', dt_fmt)
file_formatter = Formatter.DefaultConsoleFormatter(f'%(asctime)s %(levelname)s %(name)s: %(message)s', dt_fmt)
def create_logger(name):

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    if log["all"]:
        f = logging.FileHandler(now_path + "/../logs/all " + time + "-replace" + ".log")
        f.setLevel(logging.DEBUG)
        f.setFormatter(file_formatter)
        logger.addHandler(f)
    return logger

replace_logger = create_logger("replace")


args = sys.argv
replace_logger.info("args -> " + str(args))
@client.event
async def on_ready():
    file = open(args[1], 'r',encoding='utf-8').read()
    try:
        write_file = await copy(file,int(args[2]),int(args[3]),args[4])
    except:
        await copy(file,int(args[2]),int(args[3]),"server.py")

async def copy(txt,msg_id,ch_id,file_name):
    #与えられたtxtをserver.pyに書き込む
    with open(now_path + "/../" + file_name, 'w', encoding='utf-8') as f:
        f.write(txt)

    #interaction
    ch = client.get_channel(ch_id)
    msg = await ch.fetch_message(msg_id)
    
    #完了メッセージの表示
    await msg.edit(content="更新完了しました")

    #server.pyを実行
    os.execv(sys.executable,["python3",now_path + "/../" + file_name,"-init"])

# discord.py用のロガーを取得して設定
discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
discord_logger.addHandler(console_handler)
if log["all"]:
    file_handler = logging.FileHandler(now_path + "/../logs/all " + time + "-replace" + ".log")
    file_handler.setFormatter(file_formatter)
    discord_logger.addHandler(file_handler)

client.run(token,log_formatter=formatter)


