# -*- coding: utf-8 -*-
"""QQ 小号版（OneBot 11 / NapCat）：私聊 + 群聊(@) / 两种人格 / SFW / 限频 / 管理命令 / 表情包。

开源版目录（仓库根目录）：
  config.json          实际配置（不提交）
  .env                 密钥（不提交）
  prompts/             人设与摘要提示词
  assets/stickers/     表情包
  data/qq/             每个 QQ 号的记忆
  logs/                日志

用法：
  1. 复制 config.example.json 为 config.json，复制 .env.example 为 .env。
  2. 填 owner_qq、OneBot Token、DeepSeek Key；NapCat 开好 3001。
  3. python bots/qq/bot2qq.py
"""
import asyncio
import base64
import collections
import datetime
import json
import logging
import logging.handlers
import os
import queue
import random
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

import websockets
from openai import OpenAI

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_SCRIPT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SCRIPT_ROOT))
from common.config import ROOT, env, load_config, section, read_prompt, ensure_dirs

CFG = load_config()
QQ = section(CFG, "qq")
AI_CFG = section(CFG, "ai")
PATHS = section(CFG, "paths")

OWNER_QQ = int(QQ.get("owner_qq") or 0)
ALLOWED_QQS = {int(x) for x in (QQ.get("allowed_qqs") or [])}
DATA_DIR = ROOT / str(PATHS.get("data_dir") or "data") / "qq"
LOG_F = ROOT / str(PATHS.get("log_dir") or "logs") / "qq_bot.log"
LOCK_F = ROOT / ".qq_bot.lock"
ensure_dirs(DATA_DIR, LOG_F.parent)

OTHERS_MODE = str(QQ.get("others_mode") or "guest").lower()
if OTHERS_MODE not in ("guest", "block"):
    OTHERS_MODE = "guest"
GUEST_RATE_PER_MIN = int(QQ.get("guest_rate_per_minute") or 5)
GUEST_RATE_PER_DAY = int(QQ.get("guest_rate_per_day") or 30)
ALLOW_GROUP = bool(QQ.get("allow_group", True))
GROUP_REQUIRE_AT = bool(QQ.get("group_require_at", True))
GROUP_ONLY_OWNER = bool(QQ.get("group_only_owner", True))
ALLOWED_GROUPS = {int(x) for x in (QQ.get("allowed_groups") or [])}
GROUP_RATE_PER_MIN = int(QQ.get("group_rate_per_minute") or 5)
GROUP_RATE_PER_DAY = int(QQ.get("group_rate_per_day") or 50)
STICKER_DIR = ROOT / str(QQ.get("sticker_dir") or "assets/stickers")
STICKER_MAX_BYTES = int(QQ.get("sticker_max_bytes") or 4 * 1024 * 1024)
_sticker_auto = QQ.get("sticker_auto_chance")
STICKER_AUTO_CHANCE = 0.2 if _sticker_auto is None else float(_sticker_auto)
MAX_IMAGE_BYTES = int(QQ.get("max_image_bytes") or 8 * 1024 * 1024)
STICKER_CATEGORIES = ["开心", "害羞", "撒娇", "生气", "安慰", "晚安", "无语", "日常"]
STICKER_ALIASES = {
    "高兴": "开心", "笑脸": "开心",
    "脸红": "害羞", "羞": "害羞",
    "可爱": "撒娇", "抱抱": "撒娇",
    "气": "生气",
    "摸摸": "安慰", "鼓励": "安慰",
    "睡觉": "晚安", "睡": "晚安",
    "汗": "无语", "无语": "无语",
}
STICKER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
EMOJI_FALLBACK = {
    "开心": "😊", "害羞": "😳", "撒娇": "🥺", "生气": "😤",
    "安慰": "🤗", "晚安": "😴", "无语": "😅", "日常": "🙂",
}

API_KEY = env("DEEPSEEK_API_KEY_QQ")
if not API_KEY:
    raise SystemExit("缺少 DEEPSEEK_API_KEY_QQ，请写在 .env 里")
ONEBOT_TOKEN = env("QQ_ONEBOT_TOKEN")

AI = OpenAI(api_key=API_KEY,
            base_url=AI_CFG.get("api_base") or "https://api.deepseek.com",
            max_retries=0)

# ---------- 日志 ----------

LOG_F.parent.mkdir(parents=True, exist_ok=True)
_handlers = [logging.StreamHandler()]
try:
    _handlers.append(logging.handlers.RotatingFileHandler(
        LOG_F, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
except OSError:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("qqbot")

# ---------- 人格 / 提示词 ----------

_persona = section(QQ, "persona")
SYSTEM_OWNER = read_prompt(_persona["owner"])
SYSTEM_GUEST = read_prompt(_persona["guest"])
SYSTEM_GROUP = read_prompt(_persona["group"])
SUM_PROMPT_OWNER = read_prompt(_persona["sum_owner"])
SUM_PROMPT_GUEST = read_prompt(_persona["sum_guest"])
SUM_PROMPT_GROUP = read_prompt(_persona["sum_group"])

FALLBACK_REPLY = "唔……我刚才走神了一下，你再说一次好不好"
SAFE_REPLY = "这个不行哦，我们聊点别的嘛。"
RATE_REPLY = "慢一点嘛，我有点跟不上了……"
PAUSED_REPLY = "她休息啦，晚点再来找我嘛。"
BUSY_REPLY = "现在消息有点多，等我一下下哦。"

BLOCK_RE = re.compile(
    r"(做爱|性交|口交|肛交|自慰|手淫|裸照|裸体|脱光|约炮|开房|射精|高潮|"
    r"阴茎|阴道|乳头|下体|淫水|淫叫|嫖娼|强奸|迷奸|恋童|未成年.{0,6}性|"
    r"色情|黄图|搞黄|成人视频|成人内容)"
)

STICKER_RE = re.compile(r"\[表情(?:包)?[:：]\s*([^\]\s]{1,8})\s*\]")

RECENT = int(QQ.get("recent") or 40)
SUMMARIZE_AT = int(QQ.get("summarize_at") or 80)
KEEP = int(QQ.get("keep") or 400)
MAX_REPLY_CHARS = int(QQ.get("max_reply_chars") or 600)
RATE_PER_MIN = int(QQ.get("rate_per_minute") or 10)
RATE_PER_DAY = int(QQ.get("rate_per_day") or 100)

# ---------- 状态 ----------

START_TS = time.time()
PAUSED = False
MSG_COUNT = 0
BLOCKED_COUNT = 0
LOOP = None
WS = None
_SUMMARY_FAIL = {}
_SEEN = collections.OrderedDict()
_RATE_NOTICE = {}
_PAUSE_NOTICE = {}
_LAST_STICKER = {}
_STICKER_WARNED = set()
job_q = queue.Queue(maxsize=50)
_PENDING = {}


def _atomic_write(path, text):
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _read_text(path):
    return path.read_text(encoding="utf-8-sig")


# ---------- 按 QQ 号隔离的记忆 ----------

class UserStore:
    def __init__(self, base):
        self.base = base
        self._locks = {}
        self._guard = threading.Lock()

    def lock(self, uid):
        with self._guard:
            lk = self._locks.get(uid)
            if lk is None:
                lk = threading.RLock()
                self._locks[uid] = lk
            return lk

    def dir(self, uid):
        d = self.base / str(uid)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_history(self, uid):
        p = self.dir(uid) / "history.json"
        if not p.exists():
            return []
        try:
            data = json.loads(_read_text(p))
            if not isinstance(data, list):
                raise ValueError("history 不是列表")
            out = []
            for m in data:
                if (isinstance(m, dict)
                        and m.get("role") in ("user", "assistant")
                        and isinstance(m.get("content"), str)):
                    out.append({"role": m["role"], "content": m["content"]})
            return out
        except Exception as e:
            log.warning("QQ=%s 历史读坏，已备份：%s", uid, e)
            bak = p.with_name(f"history.bad.{int(time.time())}.json")
            try:
                p.replace(bak)
            except OSError:
                pass
            return []

    def save_history(self, uid, hist):
        _atomic_write(self.dir(uid) / "history.json",
                      json.dumps(hist[-KEEP:], ensure_ascii=False, indent=2))

    def load_summary(self, uid):
        p = self.dir(uid) / "summary.txt"
        if not p.exists():
            return ""
        try:
            return _read_text(p).strip()
        except OSError:
            return ""

    def save_summary(self, uid, s):
        _atomic_write(self.dir(uid) / "summary.txt", s)

    def reset(self, uid):
        with self.lock(uid):
            shutil.rmtree(self.base / str(uid), ignore_errors=True)

    def user_count(self):
        try:
            return sum(1 for p in self.base.iterdir() if p.is_dir())
        except OSError:
            return 0


store = UserStore(DATA_DIR)


# ---------- 限频 ----------

class RateLimiter:
    def __init__(self, per_min, per_day):
        self.per_min = per_min
        self.per_day = per_day
        self.minutes = {}
        self.days = {}
        self._lock = threading.Lock()

    def allow(self, uid):
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            dq = self.minutes.setdefault(uid, collections.deque())
            while dq and now - dq[0] >= 60:
                dq.popleft()
            if len(dq) >= self.per_min:
                return False, "分钟"
            day, cnt = self.days.get(uid, (today, 0))
            if day != today:
                day, cnt = today, 0
            if cnt >= self.per_day:
                self.days[uid] = (day, cnt)
                return False, "每天"
            dq.append(now)
            self.days[uid] = (day, cnt + 1)
            return True, ""

    def today_total(self):
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            return sum(c for d, c in self.days.values() if d == today)


owner_limiter = RateLimiter(RATE_PER_MIN, RATE_PER_DAY)
guest_limiter = RateLimiter(GUEST_RATE_PER_MIN, GUEST_RATE_PER_DAY)
group_limiter = RateLimiter(GROUP_RATE_PER_MIN, GROUP_RATE_PER_DAY)


def is_owner(uid):
    return bool(OWNER_QQ) and int(uid) == OWNER_QQ


def limiter_for(uid):
    return owner_limiter if is_owner(uid) else guest_limiter


def is_allowed(uid):
    if not ALLOWED_QQS:
        return True
    return uid in ALLOWED_QQS or is_owner(uid)


def sfw_blocked(text):
    return bool(BLOCK_RE.search(text or ""))


# ---------- 表情包 ----------

def sticker_count():
    total = 0
    for cat in STICKER_CATEGORIES:
        d = STICKER_DIR / cat
        if d.is_dir():
            total += sum(1 for p in d.iterdir()
                         if p.is_file() and p.suffix.lower() in STICKER_EXT)
    return total


def has_sticker(category):
    d = STICKER_DIR / normalize_category(category)
    return (d.is_dir()
            and any(p.is_file() and p.suffix.lower() in STICKER_EXT
                    for p in d.iterdir()))


def _ensure_sticker_dirs():
    STICKER_DIR.mkdir(parents=True, exist_ok=True)
    for cat in STICKER_CATEGORIES:
        (STICKER_DIR / cat).mkdir(parents=True, exist_ok=True)
    readme = STICKER_DIR / "README.txt"
    if not readme.exists():
        readme.write_text(
            "QQ 表情包目录\n\n"
            "把 png / jpg / jpeg / gif / webp 图片放进对应类别文件夹。\n"
            "她会根据回复里的 [表情:类别] 标记挑一张发；文件夹为空时退化成 emoji。\n\n"
            "类别：" + "、".join(STICKER_CATEGORIES) + "\n"
            "测试：在私聊里发 /sticker 开心\n",
            encoding="utf-8",
        )
    log.info("表情包目录：%s（%s 个文件）", STICKER_DIR, sticker_count())


def normalize_category(name):
    name = (name or "").strip()
    name = STICKER_ALIASES.get(name, name)
    return name if name in STICKER_CATEGORIES else "日常"


def split_sticker(text):
    m = STICKER_RE.search(text or "")
    cat = normalize_category(m.group(1)) if m else None
    clean = STICKER_RE.sub("", text or "").strip()
    return clean, cat


def pick_sticker(uid, category):
    cat = normalize_category(category)
    d = STICKER_DIR / cat
    files = ([p for p in d.iterdir()
              if p.is_file() and p.suffix.lower() in STICKER_EXT]
             if d.is_dir() else [])
    if not files:
        if cat not in _STICKER_WARNED:
            _STICKER_WARNED.add(cat)
            log.warning("表情包类别 %s 为空，先退回 emoji（把图片放进 %s）", cat, d)
        return None
    choice = random.choice(files)
    if len(files) > 1 and str(choice) == _LAST_STICKER.get(uid):
        choice = random.choice([p for p in files if p != choice])
    _LAST_STICKER[uid] = str(choice)
    return choice


def build_segments(uid, text, category=None):
    text = (text or "").strip()
    path = pick_sticker(uid, category) if category else None
    if category and path is None:
        emoji = EMOJI_FALLBACK.get(normalize_category(category), "🙂")
        text = (text + " " + emoji).strip()

    segs = []
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    if path:
        try:
            data = path.read_bytes()
            if len(data) <= STICKER_MAX_BYTES:
                b64 = base64.b64encode(data).decode()
                segs.append({"type": "image", "data": {"file": "base64://" + b64}})
            else:
                log.warning("表情包太大，跳过：%s（%s bytes）", path.name, len(data))
        except OSError as e:
            log.warning("读表情包失败 %s：%s", path, e)
    if not segs:
        segs.append({"type": "text", "data": {"text": FALLBACK_REPLY}})
    return segs


_ensure_sticker_dirs()


# ---------- 上下文 / 模型 ----------

def build_context(uid, messages):
    owner = is_owner(uid)
    msgs = [{"role": "system", "content": SYSTEM_OWNER if owner else SYSTEM_GUEST}]
    s = store.load_summary(uid)
    if s:
        if owner:
            mem = "关于你们以前的事（你的记忆。注意：「他」指用户（我），「她」指你自己）：\n" + s
        else:
            mem = "关于这个朋友以前聊过的事（你的记忆）：\n" + s
        msgs.append({"role": "system", "content": mem})

    recent = []
    limit = RECENT if owner else 20
    for m in messages[-limit:]:
        if (isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)):
            recent.append({"role": m["role"], "content": m["content"]})
    while recent and recent[0]["role"] != "user":
        recent.pop(0)
    msgs += recent
    return msgs


def build_group_context(gid, messages):
    key = f"g{gid}"
    msgs = [{"role": "system", "content": SYSTEM_GROUP}]
    s = store.load_summary(key)
    if s:
        msgs.append({"role": "system",
                     "content": "关于这个群以前聊过的事（你的记忆）：\n" + s})
    recent = []
    for m in messages[-20:]:
        if (isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)):
            recent.append({"role": m["role"], "content": m["content"]})
    while recent and recent[0]["role"] != "user":
        recent.pop(0)
    msgs += recent
    return msgs


def with_time(msgs):
    now = datetime.datetime.now()
    h = now.hour
    if 0 <= h < 6:
        part = "凌晨"
    elif 6 <= h < 9:
        part = "早晨"
    elif 9 <= h < 12:
        part = "上午"
    elif 12 <= h < 14:
        part = "中午"
    elif 14 <= h < 18:
        part = "下午"
    elif 18 <= h < 21:
        part = "傍晚"
    else:
        part = "晚上"
    stamp = (f"（现在是{part}。你知道大概时段就好，"
             f"不要报具体时间或星期，也不要每句都提。）\n")
    out = [dict(m) for m in msgs]
    for i in range(len(out) - 1, -1, -1):
        if out[i]["role"] == "user" and isinstance(out[i].get("content"), str):
            out[i]["content"] = stamp + out[i]["content"]
            break
    return out


def call_model(messages, timeout=60, tries=3, max_tokens=400):
    primary = AI_CFG.get("model") or "deepseek-flash"
    models = [primary]
    for m in (AI_CFG.get("fallback_models") or []):
        if m and m not in models:
            models.append(m)
    last_err = None
    for attempt in range(tries):
        for model in models:
            try:
                resp = AI.chat.completions.create(
                    model=model,
                    messages=messages,
                    timeout=timeout,
                    temperature=float(AI_CFG.get("temperature") or 1.0),
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    return text
                last_err = "空回复"
            except Exception as e:
                last_err = e
                log.warning("模型 %s 出错：%s", model, e)
        if attempt < tries - 1:
            time.sleep(min(3 + attempt * 3, 10))
    log.error("模型一直不通：%s", last_err)
    return None


def maybe_summarize(uid, hist):
    owner = is_owner(uid)
    limit = SUMMARIZE_AT if owner else 60
    recent_n = RECENT if owner else 20
    if len(hist) <= limit:
        return hist
    if time.time() < _SUMMARY_FAIL.get(uid, 0):
        return hist

    old, rest = hist[:-recent_n], hist[-recent_n:]
    lines = []
    for m in old:
        if not isinstance(m.get("content"), str):
            continue
        if owner:
            who = "他" if m["role"] == "user" else "她"
        else:
            who = "对方" if m["role"] == "user" else "你"
        lines.append(f"{who}：{m['content']}")
    if not lines:
        return rest

    prev = store.load_summary(uid)
    user_block = (("已有的记忆：\n" + prev + "\n\n") if prev else "") + \
                 "新的聊天记录：\n" + "\n".join(lines)
    new_summary = call_model(
        [{"role": "system", "content": SUM_PROMPT_OWNER if owner else SUM_PROMPT_GUEST},
         {"role": "user", "content": user_block}],
        timeout=60, tries=2, max_tokens=1500 if owner else 800,
    )
    if new_summary:
        cap = 3000 if owner else 1500
        if len(new_summary) > cap:
            new_summary = new_summary[:cap].rstrip() + "\n- （记忆过长，已截断）"
        store.save_summary(uid, new_summary)
        log.info("QQ=%s 记忆压缩：%s 条 → %s 字", uid, len(old), len(new_summary))
        return rest
    _SUMMARY_FAIL[uid] = time.time() + 600
    log.warning("QQ=%s 记忆压缩失败，10 分钟后再试", uid)
    return hist


def maybe_group_summarize(gid, hist):
    key = f"g{gid}"
    if len(hist) <= 60:
        return hist
    if time.time() < _SUMMARY_FAIL.get(key, 0):
        return hist

    old, rest = hist[:-20], hist[-20:]
    lines = []
    for m in old:
        if not isinstance(m.get("content"), str):
            continue
        if m["role"] == "user":
            lines.append(m["content"])
        else:
            lines.append(f"你：{m['content']}")
    if not lines:
        return rest

    prev = store.load_summary(key)
    user_block = (("已有的群记忆：\n" + prev + "\n\n") if prev else "") + \
                 "新的聊天记录：\n" + "\n".join(lines)
    new_summary = call_model(
        [{"role": "system", "content": SUM_PROMPT_GROUP},
         {"role": "user", "content": user_block}],
        timeout=60, tries=2, max_tokens=800,
    )
    if new_summary:
        if len(new_summary) > 1500:
            new_summary = new_summary[:1500].rstrip() + "\n- （记忆过长，已截断）"
        store.save_summary(key, new_summary)
        log.info("群 %s 记忆压缩：%s 条 → %s 字", gid, len(old), len(new_summary))
        return rest
    _SUMMARY_FAIL[key] = time.time() + 600
    log.warning("群 %s 记忆压缩失败，10 分钟后再试", gid)
    return hist


# ---------- OneBot 收发 ----------

async def call_action(action, params, timeout=10):
    global WS
    if WS is None:
        log.warning("WebSocket 未连接，%s 发不出去", action)
        return None
    echo = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    _PENDING[echo] = fut
    payload = {"action": action, "params": params, "echo": echo}
    try:
        await WS.send(json.dumps(payload, ensure_ascii=False))
        resp = await asyncio.wait_for(fut, timeout=timeout)
        ret = resp.get("retcode")
        if ret == 0:
            return resp
        log.warning("OneBot %s 失败 retcode=%s msg=%s", action, ret,
                    resp.get("message") or resp.get("wording"))
        return None
    except asyncio.TimeoutError:
        log.warning("OneBot %s 超时（%ss）", action, timeout)
        return None
    except Exception as e:
        log.warning("OneBot %s 发送异常：%s", action, e)
        return None
    finally:
        _PENDING.pop(echo, None)


async def send_segments(uid, segments):
    resp = await call_action("send_private_msg",
                             {"user_id": int(uid), "message": segments})
    return resp is not None


async def send_text(uid, text):
    return await send_segments(uid, [{"type": "text", "data": {"text": text}}])


async def send_reply(uid, text, category=None):
    return await send_segments(uid, build_segments(uid, text, category))


def send_from_worker(uid, text, category=None):
    if LOOP is None:
        return False
    try:
        fut = asyncio.run_coroutine_threadsafe(send_reply(uid, text, category), LOOP)
        return fut.result(timeout=15)
    except Exception:
        log.exception("发送异常 QQ=%s", uid)
        return False


async def send_group_segments(gid, segments):
    resp = await call_action("send_group_msg",
                             {"group_id": int(gid), "message": segments})
    return resp is not None


async def send_group_text(gid, text):
    return await send_group_segments(gid, [{"type": "text", "data": {"text": text}}])


def send_group_from_worker(gid, text):
    if LOOP is None:
        return False
    try:
        fut = asyncio.run_coroutine_threadsafe(send_group_text(gid, text), LOOP)
        return fut.result(timeout=15)
    except Exception:
        log.exception("群发送异常 群=%s", gid)
        return False


def extract_text(segments):
    if isinstance(segments, str):
        return segments.strip(), False
    parts = []
    has_image = False
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        stype = seg.get("type")
        data = seg.get("data") or {}
        if stype == "text":
            parts.append(str(data.get("text") or ""))
        elif stype == "image":
            has_image = True
        elif stype == "face":
            parts.append("[表情]")
        elif stype == "at":
            name = data.get("name") or data.get("qq") or ""
            if name:
                parts.append(f"@{name} ")
    return "".join(parts).strip(), has_image


def extract_group_text(segments, self_id):
    if isinstance(segments, str):
        return segments.strip(), False, f"@{self_id}" in segments
    parts = []
    has_image = False
    mentioned = False
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        stype = seg.get("type")
        data = seg.get("data") or {}
        if stype == "text":
            parts.append(str(data.get("text") or ""))
        elif stype == "image":
            has_image = True
        elif stype == "face":
            parts.append("[表情]")
        elif stype == "at":
            if str(data.get("qq") or "") == str(self_id):
                mentioned = True
    return "".join(parts).strip(), has_image, mentioned


def _seen(message_id):
    if message_id is None:
        return False
    key = str(message_id)
    if key in _SEEN:
        return True
    _SEEN[key] = True
    while len(_SEEN) > 1000:
        _SEEN.popitem(last=False)
    return False


# ---------- 管理命令 ----------

HELP_TEXT = """可用命令：
/status  查看运行状态
/pause   暂停回复
/resume  恢复回复
/reset [QQ号]  清空记忆（默认清自己）
/resetg 群号  清空某个群的记忆
/sticker [类别]  发一张表情包测试
/help    显示这条帮助"""


def fmt_uptime(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m}m{s}s"


async def handle_admin(uid, text):
    global PAUSED
    if not is_owner(uid):
        await send_text(uid, "这个命令只有主人能用哦。")
        return
    parts = text.replace("／", "/").split()
    cmd = parts[0].lower()

    if cmd == "/help":
        await send_text(uid, HELP_TEXT)
    elif cmd == "/status":
        lines = [
            f"运行：{fmt_uptime(time.time() - START_TS)}",
            f"状态：{'暂停' if PAUSED else '运行中'}",
            f"连接：{'已连接' if WS is not None else '未连接'}",
            f"队列：{job_q.qsize()}",
            f"收到：{MSG_COUNT} 条，拦截：{BLOCKED_COUNT} 条",
            f"今日：主人 {owner_limiter.today_total()} / 其他人 {guest_limiter.today_total()}",
            f"用户：{store.user_count()} 个",
            f"其他人：{OTHERS_MODE}",
            f"群聊：{'开' if ALLOW_GROUP else '关'}，{'@才回' if GROUP_REQUIRE_AT else '每句都回'}，{'仅主人' if GROUP_ONLY_OWNER else '所有人'}，{GROUP_RATE_PER_MIN}/分 {GROUP_RATE_PER_DAY}/天",
            f"限频：主人 {RATE_PER_MIN}/分 {RATE_PER_DAY}/天，其他人 {GUEST_RATE_PER_MIN}/分 {GUEST_RATE_PER_DAY}/天",
            f"表情包：{sticker_count()} 个（{STICKER_DIR}）",
            f"模型：{AI_CFG.get('model')}",
        ]
        await send_text(uid, "\n".join(lines))
    elif cmd == "/pause":
        PAUSED = True
        log.info("管理员暂停回复")
        await send_text(uid, "已暂停回复。")
    elif cmd == "/resume":
        PAUSED = False
        log.info("管理员恢复回复")
        await send_text(uid, "已恢复回复。")
    elif cmd == "/sticker":
        cat = normalize_category(parts[1] if len(parts) > 1 else "日常")
        await send_reply(uid, "", cat)
    elif cmd == "/reset":
        target = uid
        if len(parts) > 1:
            try:
                target = int(parts[1])
            except ValueError:
                await send_text(uid, "用法：/reset 或 /reset QQ号")
                return
        await asyncio.to_thread(store.reset, target)
        log.info("管理员清空 QQ=%s 的记忆", target)
        await send_text(uid, f"已清空 QQ={target} 的记忆。")
    elif cmd == "/resetg":
        if len(parts) < 2:
            await send_text(uid, "用法：/resetg 群号")
            return
        try:
            gid = int(parts[1])
        except ValueError:
            await send_text(uid, "群号必须是数字，例如 /resetg 820909307")
            return
        await asyncio.to_thread(store.reset, f"g{gid}")
        log.info("管理员清空群=%s 的记忆", gid)
        await send_text(uid, f"已清空群 {gid} 的记忆。")
    else:
        await send_text(uid, "不认识的命令。\n" + HELP_TEXT)


# ---------- 单条消息 ----------

def process_message(uid, text):
    global BLOCKED_COUNT, MSG_COUNT
    with store.lock(uid):
        hist = store.load_history(uid)
        hist.append({"role": "user", "content": text})
        raw = call_model(with_time(build_context(uid, hist)), timeout=60, tries=3,
                         max_tokens=400)
        blocked = False
        if not raw:
            raw = FALLBACK_REPLY
        elif sfw_blocked(raw):
            BLOCKED_COUNT += 1
            blocked = True
            log.info("QQ=%s 输出被 SFW 拦截", uid)
            raw = SAFE_REPLY

        reply_text, sticker_cat = split_sticker(raw.strip())
        reply_text = reply_text.strip()
        if (not blocked and sticker_cat is None
                and STICKER_AUTO_CHANCE > 0
                and random.random() < STICKER_AUTO_CHANCE
                and has_sticker("日常")):
            sticker_cat = "日常"
        if len(reply_text) > MAX_REPLY_CHARS:
            reply_text = reply_text[:MAX_REPLY_CHARS].rstrip() + "…"

        stored = reply_text or ("（发了个表情包）" if sticker_cat else FALLBACK_REPLY)
        hist.append({"role": "assistant", "content": stored})
        hist = maybe_summarize(uid, hist)
        store.save_history(uid, hist)

    send_from_worker(uid, reply_text, sticker_cat)
    MSG_COUNT += 1
    log.info("回复 QQ=%s 字数=%s 表情=%s", uid, len(reply_text), sticker_cat or "-")


def process_group_message(gid, sender_name, text):
    global BLOCKED_COUNT, MSG_COUNT
    key = f"g{gid}"
    with store.lock(key):
        hist = store.load_history(key)
        hist.append({"role": "user", "content": f"{sender_name}：{text}"})
        raw = call_model(with_time(build_group_context(gid, hist)), timeout=60,
                         tries=3, max_tokens=400)
        if not raw:
            raw = FALLBACK_REPLY
        elif sfw_blocked(raw):
            BLOCKED_COUNT += 1
            log.info("群 %s 输出被 SFW 拦截", gid)
            raw = SAFE_REPLY

        reply_text, _ = split_sticker(raw.strip())
        reply_text = reply_text.strip()
        if len(reply_text) > MAX_REPLY_CHARS:
            reply_text = reply_text[:MAX_REPLY_CHARS].rstrip() + "…"
        stored = reply_text or FALLBACK_REPLY
        hist.append({"role": "assistant", "content": stored})
        hist = maybe_group_summarize(gid, hist)
        store.save_history(key, hist)

    send_group_from_worker(gid, stored)
    MSG_COUNT += 1
    log.info("群回复 群=%s 字数=%s", gid, len(stored))


def worker():
    while True:
        job = job_q.get()
        kind = job.get("kind")
        try:
            if kind == "group":
                process_group_message(job["gid"], job["name"], job["text"])
            else:
                process_message(job["uid"], job["text"])
        except Exception:
            log.exception("处理消息出错：%s", job)
            if kind == "group":
                try:
                    send_group_from_worker(job["gid"], FALLBACK_REPLY)
                except Exception:
                    pass
            else:
                try:
                    store.save_history(job["uid"], store.load_history(job["uid"]))
                except Exception:
                    pass
                try:
                    send_from_worker(job["uid"], FALLBACK_REPLY)
                except Exception:
                    pass
        finally:
            job_q.task_done()


# ---------- 事件 ----------

async def on_event(raw):
    global MSG_COUNT, BLOCKED_COUNT
    try:
        event = json.loads(raw)
    except Exception:
        return
    if event.get("post_type") != "message":
        return

    mtype = event.get("message_type")
    self_id = int(event.get("self_id") or 0)

    if mtype == "group":
        if not ALLOW_GROUP:
            return
        gid = int(event.get("group_id") or 0)
        uid = int(event.get("user_id") or 0)
        if not gid or not uid or uid == self_id:
            return
        if GROUP_ONLY_OWNER and not is_owner(uid):
            return
        if ALLOWED_GROUPS and gid not in ALLOWED_GROUPS:
            return
        if _seen(event.get("message_id")):
            return

        text, has_image, mentioned = extract_group_text(event.get("message"), self_id)
        direct = bool(mentioned or event.get("to_me"))
        if GROUP_REQUIRE_AT and not direct:
            return
        if not text:
            if has_image and direct:
                await send_group_text(gid, "这版先只聊文字哦～")
            elif direct:
                await send_group_text(gid, "在呢，怎么啦～")
            return

        text = text.strip()
        if len(text) > 2000:
            text = text[:2000]
        if text.startswith("/") or text.startswith("／"):
            return  # 群里不处理管理命令，避免把号主隐私发到群里
        if PAUSED:
            return

        ok, why = group_limiter.allow(gid)
        if not ok:
            now = time.time()
            if now - _RATE_NOTICE.get(f"g{gid}", 0) > 30:
                _RATE_NOTICE[f"g{gid}"] = now
                log.info("群 %s 触发%s限频", gid, why)
                await send_group_text(gid, RATE_REPLY)
            return

        if sfw_blocked(text):
            BLOCKED_COUNT += 1
            log.info("群 %s 输入被 SFW 拦截", gid)
            await send_group_text(gid, SAFE_REPLY)
            return

        sender = event.get("sender") or {}
        name = str(sender.get("card") or sender.get("nickname") or uid)[:20]
        try:
            job_q.put_nowait({"kind": "group", "gid": gid, "name": name,
                              "text": text, "mid": event.get("message_id")})
        except queue.Full:
            await send_group_text(gid, BUSY_REPLY)
            return
        log.info("收到群聊 群=%s 用户=%s 字数=%s", gid, uid, len(text))
        return

    if mtype != "private":
        return

    uid = int(event.get("user_id") or 0)
    if not uid or uid == self_id:
        return
    if _seen(event.get("message_id")):
        return

    text, has_image = extract_text(event.get("message"))
    if not text:
        if has_image:
            await send_text(uid, "这版先只聊文字哦～")
        return

    text = text.strip()
    if len(text) > 2000:
        text = text[:2000]

    if text.startswith("/") or text.startswith("／"):
        await handle_admin(uid, text)
        return

    if not is_allowed(uid):
        log.info("QQ=%s 不在白名单，忽略", uid)
        return

    if not is_owner(uid) and OTHERS_MODE == "block":
        log.info("QQ=%s 不是主人，按 block 模式忽略", uid)
        return

    if PAUSED:
        now = time.time()
        if now - _PAUSE_NOTICE.get(uid, 0) > 600:
            _PAUSE_NOTICE[uid] = now
            await send_text(uid, PAUSED_REPLY)
        return

    ok, why = limiter_for(uid).allow(uid)
    if not ok:
        now = time.time()
        if now - _RATE_NOTICE.get(uid, 0) > 30:
            _RATE_NOTICE[uid] = now
            log.info("QQ=%s 触发%s限频", uid, why)
            await send_text(uid, RATE_REPLY)
        return

    if sfw_blocked(text):
        BLOCKED_COUNT += 1
        log.info("QQ=%s 输入被 SFW 拦截", uid)
        await send_text(uid, SAFE_REPLY)
        return

    try:
        job_q.put_nowait({"kind": "private", "uid": uid, "text": text,
                          "mid": event.get("message_id")})
    except queue.Full:
        await send_text(uid, BUSY_REPLY)
        return

    log.info("收到私聊 QQ=%s 角色=%s 字数=%s", uid,
             "主人" if is_owner(uid) else "其他人", len(text))


async def _dispatch_event(raw):
    try:
        await on_event(raw)
    except Exception:
        log.exception("事件处理出错")


async def connect_loop():
    global WS
    backoff = 3
    url = QQ.get("ws_url") or "ws://127.0.0.1:3001"
    token = ONEBOT_TOKEN
    while True:
        kwargs = {"ping_interval": 20, "ping_timeout": 20,
                  "max_size": 4 * 1024 * 1024}
        if token:
            kwargs["additional_headers"] = {"Authorization": f"Bearer {token}"}
        try:
            async with websockets.connect(url, **kwargs) as ws:
                WS = ws
                backoff = 3
                log.info("已连接 OneBot：%s", url)
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        echo = msg.get("echo")
                        if echo and echo in _PENDING:
                            fut = _PENDING[echo]
                            if not fut.done():
                                fut.set_result(msg)
                            continue
                        asyncio.create_task(_dispatch_event(raw))
                    except Exception:
                        log.exception("事件处理出错")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("OneBot 连接断开：%s", e)
        finally:
            for fut in list(_PENDING.values()):
                if not fut.done():
                    fut.set_result({"retcode": -1, "message": "WebSocket 已断开"})
            _PENDING.clear()
            WS = None
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


# ---------- 单实例 / 入口 ----------

def acquire_single_instance():
    try:
        import msvcrt
    except ImportError:
        return None
    try:
        f = open(LOCK_F, "a+", encoding="utf-8")
    except OSError as e:
        log.warning("无法创建锁文件，继续运行：%s", e)
        return None
    try:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(" ")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        log.error("已经有一个 bot2qq 在运行了（.qq_bot.lock）")
        sys.exit(1)


async def amain():
    global LOOP
    LOOP = asyncio.get_running_loop()
    threading.Thread(target=worker, daemon=True, name="qq-worker").start()
    log.info("QQ 受限版启动：私聊 + 群聊=%s（@才回=%s，仅主人=%s）/ SFW / 限频 | 其他人=%s",
             ALLOW_GROUP, GROUP_REQUIRE_AT, GROUP_ONLY_OWNER, OTHERS_MODE)
    if not OWNER_QQ:
        log.warning("owner_qq 还没配置，所有人都会走“普通朋友”人格；管理命令也不可用")
    if GROUP_ONLY_OWNER and not OWNER_QQ:
        log.warning("group_only_owner=True 但 owner_qq 没配置，群里谁都不会被回复")
    if OTHERS_MODE == "block" and not OWNER_QQ:
        log.warning("others_mode=block 但 owner_qq 没配置，所有人都会被忽略")
    log.info("表情包：%s 个（%s）", sticker_count(), STICKER_DIR)
    await connect_loop()


def main():
    _lock = acquire_single_instance()  # 保持引用
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        log.info("退出")


if __name__ == "__main__":
    main()
