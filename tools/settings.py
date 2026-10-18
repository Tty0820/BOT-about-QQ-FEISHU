# -*- coding: utf-8 -*-
"""开源版配置菜单：只改 config.json 和 .env，不碰代码。

用法：
  python tools/settings.py
  python tools/settings.py --show
"""
import getpass
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("OPENSOURCE_HOME") or Path(__file__).resolve().parents[1])
CONFIG = ROOT / "config.json"
CONFIG_EXAMPLE = ROOT / "config.example.json"
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _backup(path):
    if path.exists():
        bak = path.with_name(f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(path, bak)
        print(f"  [备份] {bak.name}")


def _atomic_write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_config():
    if not CONFIG.exists():
        if CONFIG_EXAMPLE.exists():
            shutil.copy2(CONFIG_EXAMPLE, CONFIG)
            print(f"[初始化] 已复制 {CONFIG.name}，请逐项填写。")
        else:
            raise SystemExit("缺少 config.example.json")
    return json.loads(CONFIG.read_text(encoding="utf-8-sig"))


def read_env_lines():
    if not ENV.exists():
        if ENV_EXAMPLE.exists():
            shutil.copy2(ENV_EXAMPLE, ENV)
            print(f"[初始化] 已复制 {ENV.name}，请填密钥。")
        else:
            return []
    return ENV.read_text(encoding="utf-8-sig").splitlines()


def env_value(key):
    for line in read_env_lines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def set_env_value(key, value):
    lines = read_env_lines()
    new_line = f"{key}={value}"
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith("#") or "=" not in line:
            continue
        k = line.split("=", 1)[0].strip()
        if k == key:
            lines[i] = new_line
            found = True
            break
    if not found:
        lines.append(new_line)
    _backup(ENV)
    _atomic_write(ENV, "\n".join(lines) + "\n")
    os.environ[key] = value


def get_dotted(data, dotted):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_dotted(data, dotted, value):
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


class Setting:
    def __init__(self, key, label, secret=False):
        self.key = key
        self.label = label
        self.secret = secret

    def raw(self):
        raise NotImplementedError

    def save(self, value):
        raise NotImplementedError

    def display(self):
        value = self.raw()
        if self.secret:
            return f"（已设置，长度 {len(str(value))}）" if value else "（空）"
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, list):
            return ", ".join(str(x) for x in value) if value else "（空）"
        if value is None or value == "":
            return "（空）"
        return str(value)


class JsonSetting(Setting):
    def __init__(self, key, label, kind, secret=False, validate=None):
        super().__init__(key, label, secret)
        self.kind = kind
        self.validate = validate

    def raw(self):
        return get_dotted(load_config(), self.key)

    def save(self, value):
        data = load_config()
        set_dotted(data, self.key, value)
        _backup(CONFIG)
        _atomic_write(CONFIG, json.dumps(data, ensure_ascii=False, indent=2))


class EnvSetting(Setting):
    def raw(self):
        return env_value(self.key)

    def save(self, value):
        set_env_value(self.key, value)


def v_qq(v):
    v = v.strip()
    if not (v.isdigit() and 5 <= len(v) <= 12):
        raise ValueError("QQ 号应为 5~12 位数字")
    return int(v)


def v_url(v):
    v = v.strip()
    if not (v.startswith("ws://") or v.startswith("wss://")):
        raise ValueError("要以 ws:// 或 wss:// 开头")
    return v


def v_bool(v):
    t = v.strip().lower()
    if t in ("1", "true", "yes", "y", "是", "开", "on"):
        return True
    if t in ("0", "false", "no", "n", "否", "关", "off"):
        return False
    raise ValueError("输入 是/否、y/n、true/false 或 1/0")


def v_int(v):
    if not v.strip().isdigit():
        raise ValueError("请输入非负整数")
    return int(v)


def v_float(v):
    try:
        x = float(v)
    except ValueError:
        raise ValueError("请输入数字")
    if not 0 <= x <= 2:
        raise ValueError("temperature 建议在 0~2 之间")
    return x


def v_chance(v):
    try:
        x = float(v)
    except ValueError:
        raise ValueError("请输入 0~1 的数字")
    if not 0 <= x <= 1:
        raise ValueError("概率应在 0~1 之间")
    return x


def v_list_int(v):
    t = v.strip()
    if t in ("", "-", "无", "none"):
        return []
    parts = [p for p in re.split(r"[,，\s]+", t) if p]
    if not all(p.isdigit() for p in parts):
        raise ValueError("用逗号分隔数字")
    return [int(p) for p in parts]


def v_list_str(v):
    t = v.strip()
    return [p for p in re.split(r"[,，\s]+", t) if p]


def v_times(v):
    parts = [p for p in re.split(r"[,，\s]+", v.strip()) if p]
    for p in parts:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", p):
            raise ValueError("时间格式应为 HH:MM，例如 09:00,21:30")
    return parts


def v_text(v):
    if not v.strip():
        raise ValueError("不能为空")
    return v.strip()


def v_secret(v):
    v = v.strip()
    if not v or any(c.isspace() for c in v):
        raise ValueError("不能为空或包含空格")
    return v


def v_openid(v):
    v = v.strip()
    if v == "-":
        return ""
    if v and not v.startswith("ou_"):
        raise ValueError("open_id 通常以 ou_ 开头；清空输入 -")
    return v


SETTINGS = [
    EnvSetting("DEEPSEEK_API_KEY_QQ", "QQ 的 DeepSeek API Key", secret=True),
    EnvSetting("DEEPSEEK_API_KEY_FEISHU", "飞书的 DeepSeek API Key", secret=True),
    JsonSetting("qq.owner_qq", "QQ 主人号", "int", validate=v_qq),
    JsonSetting("qq.ws_url", "QQ OneBot WS 地址", "str", validate=v_url),
    EnvSetting("QQ_ONEBOT_TOKEN", "QQ OneBot access_token", secret=True),
    JsonSetting("qq.allowed_qqs", "QQ 私聊白名单（空=所有人）", "list",
                validate=v_list_int),
    JsonSetting("qq.allow_group", "QQ 群聊开关", "bool", validate=v_bool),
    JsonSetting("qq.group_require_at", "QQ 群里只 @ 才回", "bool", validate=v_bool),
    JsonSetting("qq.group_only_owner", "QQ 群里只回主人", "bool", validate=v_bool),
    JsonSetting("qq.allowed_groups", "QQ 允许的群号（空=所有群）", "list",
                validate=v_list_int),
    JsonSetting("qq.group_rate_per_minute", "QQ 群聊每分钟上限", "int", validate=v_int),
    JsonSetting("qq.group_rate_per_day", "QQ 群聊每天上限", "int", validate=v_int),
    JsonSetting("qq.max_reply_chars", "QQ 单条回复最大字数", "int", validate=v_int),
    JsonSetting("qq.sticker_auto_chance", "QQ 自动表情概率 0~1", "float",
                validate=v_chance),
    JsonSetting("ai.model", "AI 主模型", "str", validate=v_text),
    JsonSetting("ai.fallback_models", "AI 备用模型（逗号分隔）", "list",
                validate=v_list_str),
    JsonSetting("ai.temperature", "AI temperature", "float", validate=v_float),
    JsonSetting("feishu.app_id", "飞书 App ID", "str", validate=v_text),
    EnvSetting("FEISHU_APP_SECRET", "飞书 App Secret", secret=True),
    JsonSetting("feishu.owner_open_id", "飞书主人 open_id（- = 清空）", "str",
                validate=v_openid),
    JsonSetting("feishu.proactive", "飞书主动消息时间（逗号分隔 HH:MM）", "list",
                validate=v_times),
    JsonSetting("startup.python", "Python 路径（可写 python）", "str", validate=v_text),
    JsonSetting("startup.napcat", "NapCat 启动脚本路径", "str", validate=v_text),
    JsonSetting("startup.quick_login_qq", "NapCat 快速登录 QQ", "int", validate=v_int),
]


def show():
    print("=" * 60)
    print(f"根目录：{ROOT}")
    print("=" * 60)
    for i, s in enumerate(SETTINGS, 1):
        print(f"{i:2}. {s.label}")
        print(f"    {s.display()}")
    print("=" * 60)


def main():
    if "--show" in sys.argv:
        show()
        return
    while True:
        show()
        choice = input("编号修改，0 退出：").strip()
        if choice in ("0", "q", ""):
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(SETTINGS)):
            print("编号不对。")
            continue
        s = SETTINGS[int(choice) - 1]
        print(f"\n{s.label}\n当前：{s.display()}")
        try:
            value = getpass.getpass("新值（回车取消）：") if s.secret else input("新值（回车取消）：")
        except (EOFError, KeyboardInterrupt):
            return
        if value == "":
            print("未修改。")
            continue
        try:
            if s.validate:
                value = s.validate(value)
            s.save(value)
            print("  [已写入] 重启对应 bot 后生效")
        except Exception as e:
            print(f"  [失败] {e}")


if __name__ == "__main__":
    main()
