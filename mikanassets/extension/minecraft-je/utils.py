
import struct
import gzip
class nbt:
    def __init__(self, data=None, file_path=None):
        """
        Args:
            data: NBTデータ
            file_path: NBTファイルのパス
            (どちらかを渡す)
        """
        if data is None and file_path is None:
            raise ValueError("Either 'data' or 'file_path' must be provided")
        if data is not None and file_path is not None:
            raise ValueError("Only one of 'data' or 'file_path' can be provided")
        if file_path is not None:
            self.data = self._read_nbt(file_path)
        if data is not None:
            self.data = data

    # NBTデータを読み取る関数
    def _read_nbt(self,file_path):
        with gzip.open(file_path, 'rb') as f:  # NBTファイルは通常GZIP圧縮されている
            data = f.read()
        return self._parse_nbt(data)

    # バイナリデータを解析
    def _parse_nbt(self,data, pos=0):
        tag_type = data[pos]  # タグのタイプ
        pos += 1
        if tag_type == 0:  # タグタイプが0（終了タグ）の場合
            return None, pos

        # 名前の長さを取得
        name_length = struct.unpack(">H", data[pos:pos+2])[0]
        pos += 2

        # 名前を取得
        name = data[pos:pos+name_length].decode('utf-8')
        pos += name_length

        # データを取得
        value, pos = self._read_tag(tag_type, data, pos)

        return {name: value}, pos

    # タグタイプに応じたデータを読み取る
    def _read_tag(self,tag_type, data, pos):
        if tag_type == 1:  # Byte
            value = struct.unpack(">b", data[pos:pos+1])[0]
            pos += 1
        elif tag_type == 2:  # Short
            value = struct.unpack(">h", data[pos:pos+2])[0]
            pos += 2
        elif tag_type == 3:  # Int
            value = struct.unpack(">i", data[pos:pos+4])[0]
            pos += 4
        elif tag_type == 4:  # Long
            value = struct.unpack(">q", data[pos:pos+8])[0]
            pos += 8
        elif tag_type == 5:  # Float
            value = struct.unpack(">f", data[pos:pos+4])[0]
            pos += 4
        elif tag_type == 6:  # Double
            value = struct.unpack(">d", data[pos:pos+8])[0]
            pos += 8
        elif tag_type == 7:  # ByteArray
            length = struct.unpack(">i", data[pos:pos+4])[0]
            pos += 4
            value = list(data[pos:pos+length])
            pos += length
        elif tag_type == 8:  # String
            length = struct.unpack(">H", data[pos:pos+2])[0]
            pos += 2
            value = data[pos:pos+length].decode('utf-8')
            pos += length
        elif tag_type == 9:  # List
            # 要素のタグタイプ
            element_type = data[pos]
            pos += 1
            # 要素数
            length = struct.unpack(">i", data[pos:pos+4])[0]
            pos += 4
            # 要素を解析
            value = []
            for _ in range(length):
                element, pos = self._read_tag(element_type, data, pos)
                value.append(element)
        elif tag_type == 10:  # Compound
            value = {}
            while True:
                tag, pos = self._parse_nbt(data, pos)
                if tag is None:
                    break
                value.update(tag)
        elif tag_type == 11:  # IntArray
            length = struct.unpack(">i", data[pos:pos+4])[0]
            pos += 4
            value = list(struct.unpack(f">{length}i", data[pos:pos+(4*length)]))
            pos += 4 * length
        elif tag_type == 12:  # LongArray
            length = struct.unpack(">i", data[pos:pos+4])[0]
            pos += 4
            value = list(struct.unpack(f">{length}q", data[pos:pos+(8*length)]))
            pos += 8 * length
        else:
            raise ValueError(f"Unknown tag type: {tag_type}")
        return value, pos

    # データをSNBT形式に変換
    def to_snbt(self):
        if isinstance(self.data, dict):
            return "{" + ", ".join(f"{key}: {self.to_snbt(value)}" for key, value in self.data.items()) + "}"
        elif isinstance(self.data, list):
            return "[" + ", ".join(self.to_snbt(value) for value in self.data) + "]"
        elif isinstance(self.data, str):
            return f'"{self.data}"'
        else:
            return str(self.data)
        
    def to_dict(self):
        data = self.data
        def parse(data):
            if isinstance(data, dict):
                return {key: parse(value) for key, value in data.items()}
            elif isinstance(data, list):
                return [parse(value) for value in data]
            else:
                return data
        if isinstance(data, tuple):
            return parse(data[0])
        return parse(data)

    def __str__(self):
        return self.to_snbt()
    
def format_uuid(uuid: str) -> str:
    return f"{uuid[:8]}-{uuid[8:12]}-{uuid[12:16]}-{uuid[16:20]}-{uuid[20:]}"