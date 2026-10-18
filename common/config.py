# -*- coding: utf-8 -*-
"""开源版的统一配置加载器。

根目录：
  config.json          实际配置（不提交）
  config.example.json  示例（提交）
  .env                 密钥（不提交）
  .env.example         示例（提交）
  prompts/             人设与摘要提示词
  assets/stickers/     表情包
  data/                运行数据（不提交）
  logs/                日志（不提交）
"""
import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("OPENSOURCE_HOME") or Path(__file__).resolve().parent.parent)
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"


def load_env():
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name, default=""):
    load_env()
    return os.environ.get(name, default)


def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit(
            "缺少 config.json。先复制 config.example.json 为 config.json，"
            "再按 README 填自己的值。"
        )
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception as e:
        raise SystemExit(f"config.json 解析失败：{e}")


def section(cfg, name, default=None):
    value = cfg.get(name)
    return value if isinstance(value, dict) else (default or {})


def read_prompt(relative_path):
    path = ROOT / relative_path
    if not path.exists():
        raise SystemExit(f"缺少提示词文件：{path}")
    return path.read_text(encoding="utf-8-sig").strip()


def ensure_dirs(*paths):
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)
