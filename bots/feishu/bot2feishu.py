# -*- coding: utf-8 -*-
"""飞书私聊 bot：WebSocket 长连接 / 人格 / 长期记忆 / 表情包 / 主动消息。

开源版目录（仓库根目录）：
  config.json          实际配置（不提交）
  .env                 密钥（不提交）
  prompts/             人设与摘要提示词
  assets/stickers/     表情包
  data/feishu/         history / summary / chat_id

用法：
  1. 复制 config.example.json 为 config.json，复制 .env.example 为 .env。
  2. 填 app_id、FEISHU_APP_SECRET、DEEPSEEK_API_KEY_FEISHU。
  3. python bots/feishu/bot2feishu.py
"""
import base64, collections, datetime, json, os, queue, random, re, sys, threading, time, traceback
from pathlib import Path
from openai import OpenAI
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    GetMessageResourceRequest,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_SCRIPT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SCRIPT_ROOT))
from common.config import ROOT, env, load_config, section, read_prompt, ensure_dirs

BASE = ROOT
CFG = load_config()
FS = section(CFG, "feishu")
AI_CFG = section(CFG, "ai")
PATHS = section(CFG, "paths")

API_KEY = env("DEEPSEEK_API_KEY_FEISHU")
if not API_KEY:
    raise SystemExit("缺少 DEEPSEEK_API_KEY_FEISHU，请写在 .env 里")
APP_ID = str(FS.get("app_id") or "").strip()
APP_SECRET = env("FEISHU_APP_SECRET")
if not APP_ID or not APP_SECRET:
    raise SystemExit("缺少飞书 app_id（config.json）或 FEISHU_APP_SECRET（.env）")
OWNER_OPEN_ID = str(FS.get("owner_open_id") or "").strip()

DATA_DIR = ROOT / str(PATHS.get("data_dir") or "data") / "feishu"
ensure_dirs(DATA_DIR)
HISTORY = DATA_DIR / "history.json"
SUMMARY_F = DATA_DIR / "summary.txt"
CHAT_ID_F = DATA_DIR / "chat_id.txt"

RECENT = int(FS.get("recent") or 40)
SUMMARIZE_AT = int(FS.get("summarize_at") or 80)
KEEP = int(FS.get("keep") or 600)
MAX_REPLY_CHARS = int(FS.get("max_reply_chars") or 600)
MAX_IMAGE_BYTES = int(FS.get("max_image_bytes") or 8 * 1024 * 1024)

PROACTIVE = FS.get("proactive") or ["09:00", "12:00", "21:30"]
_primary = AI_CFG.get("model") or "deepseek-flash"
FALLBACK_MODELS = [_primary]
for _m in (AI_CFG.get("fallback_models") or []):
    if _m and _m not in FALLBACK_MODELS:
        FALLBACK_MODELS.append(_m)
FALLBACK_REPLY = "唔……我刚才走神了一下，你再说一次好不好"

STICKER_DIR = ROOT / str(FS.get("sticker_dir") or "assets/stickers")
STICKER_MAX_BYTES = int(FS.get("sticker_max_bytes") or 4 * 1024 * 1024)
_sticker_auto = FS.get("sticker_auto_chance")
STICKER_AUTO_CHANCE = 0.2 if _sticker_auto is None else float(_sticker_auto)
STICKER_CATEGORIES = ["开心", "害羞", "撒娇", "生气", "安慰", "晚安", "无语", "日常"]
STICKER_ALIASES = {
    "高兴": "开心", "笑脸": "开心",
    "脸红": "害羞", "羞": "害羞",
    "可爱": "撒娇", "抱抱": "撒娇",
    "气": "生气",
    "摸摸": "安慰", "鼓励": "安慰",
    "睡觉": "晚安", "睡": "晚安",
    "汗": "无语",
}
STICKER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
EMOJI_FALLBACK = {
    "开心": "😊", "害羞": "😳", "撒娇": "🥺", "生气": "😤",
    "安慰": "🤗", "晚安": "😴", "无语": "😅", "日常": "🙂",
}
STICKER_RE = re.compile(r"\[表情(?:包)?[:：]\s*([^\]\s]{1,8})\s*\]")
_LAST_STICKER = {}
_STICKER_WARNED = set()
_IMAGE_CACHE = {}

_persona = section(FS, "persona")
SYSTEM = read_prompt(_persona["main"])
SUM_PROMPT = read_prompt(_persona["summary"])
IDENTITY_REMINDER = read_prompt(_persona["identity"])

ai = OpenAI(api_key=API_KEY,
            base_url=AI_CFG.get("api_base") or "https://api.deepseek.com",
            max_retries=0)
fs = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

# ---------- 文件读写 ----------

def _atomic_write(path, text):
    """先写临时文件再替换，避免写一半断电把历史/记忆写坏。"""
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
    # utf-8-sig 能吃掉记事本可能留下的 BOM
    return path.read_text(encoding="utf-8-sig")


def load_history():
    if HISTORY.exists():
        try:
            data = json.loads(_read_text(HISTORY))
            if not isinstance(data, list):
                raise ValueError("history.json 不是列表")
            clean = []
            for m in data:
                if (isinstance(m, dict)
                        and m.get("role") in ("user", "assistant")
                        and isinstance(m.get("content"), str)):
                    clean.append({"role": m["role"], "content": m["content"]})
            return clean
        except Exception as e:
            print("[警告] history.json 读坏了，当空的用：", e)
            # 留个坏文件备份，别直接覆盖掉
            bak = HISTORY.with_name(f"history.bad.{int(time.time())}.json")
            try:
                HISTORY.replace(bak)
                print("[警告] 坏文件已备份为", bak.name)
            except OSError:
                pass
    return []


def save_history(h):
    _atomic_write(HISTORY, json.dumps(h[-KEEP:], ensure_ascii=False, indent=2))


def load_summary():
    if SUMMARY_F.exists():
        try:
            return _read_text(SUMMARY_F).strip()
        except OSError:
            return ""

    # 兼容旧的 summary.json
    old = SUMMARY_F.with_name("summary.json")
    if old.exists():
        try:
            d = json.loads(_read_text(old))
            if isinstance(d, dict):
                return d.get("summary", "")
            if isinstance(d, list):
                return "\n\n".join(d)
        except Exception:
            pass
    return ""


def save_summary(s):
    _atomic_write(SUMMARY_F, s)


def load_chat_id():
    if CHAT_ID_F.exists():
        try:
            return _read_text(CHAT_ID_F).strip()
        except OSError:
            return ""
    return ""


def save_chat_id(cid):
    if load_chat_id() != cid:
        _atomic_write(CHAT_ID_F, cid)


# ---------- 上下文拼装 ----------

def build_context(messages, extra_system=None):
    system = SYSTEM
    if extra_system:
        system += "\n\n" + extra_system
    msgs = [{"role": "system", "content": system}]
    s = load_summary()
    if s:
        msgs.append({"role": "system",
                     "content": "关于你们以前的事（你的记忆。注意：「他」指用户（我），「她」指你自己）：\n" + s})
    msgs.append({"role": "system", "content": IDENTITY_REMINDER})

    recent = []
    for m in messages[-RECENT:]:
        if (isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)):
            recent.append({"role": m["role"], "content": m["content"]})
    # 别让上下文从「她」的半句话开始
    while recent and recent[0]["role"] != "user":
        recent.pop(0)
    msgs += recent
    return msgs


def _with_time(msgs):
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


# ---------- 调用模型 ----------

def _call_model(messages, timeout=60, tries=3, max_tokens=500):
    last_err = None
    for attempt in range(tries):
        for m in FALLBACK_MODELS:
            try:
                resp = ai.chat.completions.create(
                    model=m,
                    messages=messages,
                    timeout=timeout,
                    temperature=float(AI_CFG.get("temperature") or 0.9),
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                text = (resp.choices[0].message.content or "").strip()
                if text:
                    return text
                last_err = "模型返回了空内容"
            except Exception as e:
                last_err = e
                print(f"[模型出错 {m}] {e}")
        if attempt < tries - 1:
            wait = min(3 + attempt * 3, 15)
            print(f"[重试] {wait} 秒后再试")
            time.sleep(wait)
    print("[放弃] 模型一直不通：", last_err)
    return None


def think(messages):
    return _call_model(_with_time(build_context(messages)), timeout=60)


def think_with_image(messages, last_content):
    # messages 最后一条是刚存进 history 的「[图片]」占位，这里不要再发一遍
    msgs = build_context(messages[:-1])
    msgs.append({"role": "user", "content": last_content})
    return _call_model(msgs, timeout=60)


def think_proactive(messages, note):
    # 主动开口的提示并进 system，不能伪装成 user 说的话
    msgs = build_context(messages, extra_system=note)
    return _call_model(msgs, timeout=60)


# ---------- 长期记忆：把老对话压成摘要 ----------

_summary_fail_until = 0.0


def maybe_summarize(history):
    global _summary_fail_until
    if len(history) <= SUMMARIZE_AT:
        return history
    if time.time() < _summary_fail_until:
        return history

    old  = history[:-RECENT]
    rest = history[-RECENT:]

    lines = []
    for m in old:
        if not isinstance(m.get("content"), str):
            continue
        who = "他" if m["role"] == "user" else "她"
        lines.append(f"{who}：{m['content']}")
    if not lines:
        return rest

    prev = load_summary()
    user_block = (("已有的记忆：\n" + prev + "\n\n") if prev else "") + \
                 "新的聊天记录（「他」=用户，「她」=你）：\n" + "\n".join(lines)

    new_summary = _call_model(
        [{"role": "system", "content": SUM_PROMPT},
         {"role": "user", "content": user_block}],
        timeout=90, tries=3, max_tokens=2500,
    )

    if new_summary:
        if len(new_summary) > 4000:
            new_summary = new_summary[:4000].rstrip() + "\n- （记忆过长，已截断）"
        save_summary(new_summary)
        print(f"[记忆] 已压缩 {len(old)} 条 → 摘要（{len(new_summary)} 字）")
        return rest
    _summary_fail_until = time.time() + 600
    print("[记忆] 压缩失败，这轮先不压（10 分钟后再试）")
    return history


# ---------- 飞书收发 ----------

def fs_send(chat_id, text):
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        ).build()
    for attempt in range(2):
        try:
            resp = fs.im.v1.message.create(req)
            ok = getattr(resp, "success", None)
            if resp is not None and (ok() if callable(ok) else True):
                return True
            print("[发送失败]", getattr(resp, "code", None), getattr(resp, "msg", None))
        except Exception as e:
            print("[发送失败]", e)
        if attempt == 0:
            time.sleep(1)
    return False


def _sniff_image_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def fs_get_image_b64(message_id, image_key):
    try:
        req = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(image_key) \
            .type("image") \
            .build()
        resp = fs.im.v1.message_resource.get(req)
        ok = getattr(resp, "success", None)
        if resp is None or not (ok() if callable(ok) else True):
            print("[取图失败]", getattr(resp, "code", None), getattr(resp, "msg", None))
            return None, None
        data = resp.file.read() if resp.file else b""
        if not data:
            print("[取图失败] 空文件")
            return None, None
        if len(data) > MAX_IMAGE_BYTES:
            print(f"[取图失败] 图片太大：{len(data)} bytes")
            return None, None
        return base64.b64encode(data).decode(), _sniff_image_mime(data)
    except Exception as e:
        print("[取图失败]", e)
        return None, None


def time_reply():
    n = datetime.datetime.now()
    wd = "一二三四五六日"[n.weekday()]
    return (f"现在是{n.strftime('%H:%M')}哦\n"
            f"{n.strftime('%Y年%m月%d日')} 周{wd}")


# ---------- 表情包 ----------

def sticker_count():
    total = 0
    for cat in STICKER_CATEGORIES:
        d = STICKER_DIR / cat
        if d.is_dir():
            total += sum(1 for p in d.iterdir()
                         if p.is_file() and p.suffix.lower() in STICKER_EXT)
    return total


def _ensure_sticker_dirs():
    STICKER_DIR.mkdir(parents=True, exist_ok=True)
    for cat in STICKER_CATEGORIES:
        (STICKER_DIR / cat).mkdir(parents=True, exist_ok=True)
    readme = STICKER_DIR / "README.txt"
    if not readme.exists():
        readme.write_text(
            "飞书表情包目录\n\n"
            "把 png / jpg / jpeg / gif / webp 图片放进对应类别文件夹。\n"
            "她会在回复里用 [表情:类别] 指定，脚本自动上传并发送。\n"
            "文件夹为空时退化成 emoji。\n\n"
            "类别：" + "、".join(STICKER_CATEGORIES) + "\n",
            encoding="utf-8",
        )
    print(f"[表情包] {STICKER_DIR}（{sticker_count()} 个文件）")


def normalize_category(name):
    name = (name or "").strip()
    name = STICKER_ALIASES.get(name, name)
    return name if name in STICKER_CATEGORIES else "日常"


def split_sticker(text):
    m = STICKER_RE.search(text or "")
    cat = normalize_category(m.group(1)) if m else None
    clean = STICKER_RE.sub("", text or "").strip()
    return clean, cat


def pick_sticker(category):
    cat = normalize_category(category)
    d = STICKER_DIR / cat
    files = ([p for p in d.iterdir()
              if p.is_file() and p.suffix.lower() in STICKER_EXT]
             if d.is_dir() else [])
    if not files:
        if cat not in _STICKER_WARNED:
            _STICKER_WARNED.add(cat)
            print(f"[表情包] 类别 {cat} 为空，先退回 emoji（把图片放进 {d}）")
        return None
    choice = random.choice(files)
    if len(files) > 1 and str(choice) == _LAST_STICKER.get("last"):
        choice = random.choice([p for p in files if p != choice])
    _LAST_STICKER["last"] = str(choice)
    return choice


def has_sticker(category):
    d = STICKER_DIR / normalize_category(category)
    return (d.is_dir()
            and any(p.is_file() and p.suffix.lower() in STICKER_EXT
                    for p in d.iterdir()))


def fs_upload_image(path):
    try:
        st = path.stat()
        cache_key = (str(path), st.st_mtime_ns, st.st_size)
        if cache_key in _IMAGE_CACHE:
            return _IMAGE_CACHE[cache_key]
        if st.st_size > STICKER_MAX_BYTES:
            print(f"[表情包] 图片太大，跳过：{path.name}（{st.st_size} bytes）")
            return None
        with open(path, "rb") as f:
            req = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(f)
                .build()
            ).build()
            resp = fs.im.v1.image.create(req)
        ok = getattr(resp, "success", None)
        if resp is not None and (ok() if callable(ok) else True):
            data = getattr(resp, "data", None)
            image_key = getattr(data, "image_key", None) if data else None
            if image_key:
                _IMAGE_CACHE[cache_key] = image_key
                return image_key
        print("[表情上传失败]", getattr(resp, "code", None), getattr(resp, "msg", None))
    except Exception as e:
        print("[表情上传失败]", e)
    return None


def fs_send_image(chat_id, image_key):
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("image")
            .content(json.dumps({"image_key": image_key}, ensure_ascii=False))
            .build()
        ).build()
    for attempt in range(2):
        try:
            resp = fs.im.v1.message.create(req)
            ok = getattr(resp, "success", None)
            if resp is not None and (ok() if callable(ok) else True):
                return True
            print("[表情发送失败]", getattr(resp, "code", None), getattr(resp, "msg", None))
        except Exception as e:
            print("[表情发送失败]", e)
        if attempt == 0:
            time.sleep(1)
    return False


def fs_send_sticker(chat_id, category):
    path = pick_sticker(category)
    if not path:
        return False
    image_key = fs_upload_image(path)
    if not image_key:
        return False
    return fs_send_image(chat_id, image_key)


_ensure_sticker_dirs()


# ---------- 处理一条消息 ----------

history = load_history()
lock = threading.Lock()
_last_user_ts = 0.0
job_q = queue.Queue(maxsize=100)

_TIME_Q = re.compile(
    r"(几点了|几点钟|现在(是)?(什么|几)点|现在(是)?(什么)?时间|"
    r"现在(是)?什么时候|今天(是)?(几号|几月几号|星期几|周几))"
)


def _is_time_question(text):
    return bool(_TIME_Q.search(text))


def handle_one(chat_id, text, photo_key=None, message_id=None):
    global history, _last_user_ts
    _last_user_ts = time.time()

    reply = None
    reply_text = ""
    sticker_cat = None

    with lock:
        if photo_key:
            b64, mime = fs_get_image_b64(message_id, photo_key)
            if not b64:
                reply = "图片没取到，再发一次好不好"
            else:
                print("我 > [图片]", text)
                content = [
                    {"type": "text", "text": text or "你看这张图～"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]
                history.append({"role": "user",
                                "content": "[图片]" + ((" " + text) if text else "")})
                reply = think_with_image(history, content)
                if not reply:
                    reply = FALLBACK_REPLY
        elif text:
            print("我 >", text)
            history.append({"role": "user", "content": text})

            if _is_time_question(text):
                reply = time_reply()
            else:
                reply = think(history)
            if not reply:
                reply = FALLBACK_REPLY

        if reply:
            reply_text, sticker_cat = split_sticker(reply.strip())
            if sticker_cat and not has_sticker(sticker_cat):
                emoji = EMOJI_FALLBACK.get(normalize_category(sticker_cat), "🙂")
                reply_text = (reply_text + " " + emoji).strip()
                sticker_cat = None
            if (not sticker_cat
                    and random.random() < STICKER_AUTO_CHANCE
                    and has_sticker("日常")):
                sticker_cat = "日常"
            if len(reply_text) > MAX_REPLY_CHARS:
                reply_text = reply_text[:MAX_REPLY_CHARS].rstrip() + "…"
            stored = reply_text or ("（发了个表情包）" if sticker_cat else FALLBACK_REPLY)
            history.append({"role": "assistant", "content": stored})
            history = maybe_summarize(history)
        else:
            reply_text = FALLBACK_REPLY
            history.append({"role": "assistant", "content": FALLBACK_REPLY})
        save_history(history)

    if reply_text:
        print("她 >", reply_text)
        fs_send(chat_id, reply_text)
    if sticker_cat:
        print("她 > [表情]", sticker_cat)
        if not fs_send_sticker(chat_id, sticker_cat):
            # 没权限上传/发送图片时，至少退回一个 emoji
            fs_send(chat_id, EMOJI_FALLBACK.get(normalize_category(sticker_cat), "🙂"))
    if not reply_text and not sticker_cat:
        print("[跳过] 她这轮没回上")
        fs_send(chat_id, FALLBACK_REPLY)


# ---------- 飞书事件回调 ----------

_seen_ids = collections.OrderedDict()
_seen_senders = set()


def _already_seen(message_id):
    if not message_id:
        return False
    if message_id in _seen_ids:
        return True
    _seen_ids[message_id] = True
    while len(_seen_ids) > 500:
        _seen_ids.popitem(last=False)
    return False


def _enqueue(chat_id, text, photo_key, message_id):
    try:
        job_q.put_nowait((chat_id, text, photo_key, message_id))
    except queue.Full:
        print("[队列满] 丢弃一条消息")


def do_message_receive(data: P2ImMessageReceiveV1) -> None:
    try:
        msg = data.event.message
        if _already_seen(msg.message_id):
            print("[忽略] 重复事件", msg.message_id)
            return

        sender = getattr(msg, "sender", None)
        if getattr(sender, "sender_type", None) == "app":
            return                          # 别理机器人自己/别的应用发的消息

        sender_id = getattr(getattr(sender, "sender_id", None), "open_id", None)
        if sender_id and sender_id not in _seen_senders:
            _seen_senders.add(sender_id)
            print(f"[发送者] open_id={sender_id}")  # 想只回应自己，就填到 config.json 的 feishu.owner_open_id
        if OWNER_OPEN_ID and sender_id != OWNER_OPEN_ID:
            print("[忽略] 不是主人发的消息")
            return

        chat_id = msg.chat_id
        save_chat_id(chat_id)              # 记住往哪回

        mtype = msg.message_type
        content = json.loads(msg.content or "{}")

        if mtype == "text":
            text = (content.get("text") or "").strip()
            # 群里会带 @_user_1、@_user_2 …，统一去掉
            text = re.sub(r"@_user_\d+", "", text).strip()
            _enqueue(chat_id, text, None, msg.message_id)
        elif mtype == "image":
            _enqueue(chat_id, "", content.get("image_key"), msg.message_id)
    except Exception:
        print("[事件处理出错]", traceback.format_exc())


# ---------- 定时主动发送 ----------

def proactive_loop():
    global history
    last_sent = {}
    while True:
        try:
            now = datetime.datetime.now()
            today = now.strftime("%Y-%m-%d")
            now_min = now.hour * 60 + now.minute
            cid = load_chat_id()

            if cid:
                for slot in PROACTIVE:
                    hh, mm = map(int, slot.split(":"))
                    slot_min = hh * 60 + mm
                    # 到点后 15 分钟内都算，避免刚好在睡觉/忙别的错过
                    if not (0 <= now_min - slot_min < 15):
                        continue
                    if last_sent.get(slot) == today:
                        continue
                    last_sent[slot] = today

                    if time.time() - _last_user_ts < 600:
                        print("[主动] 你刚说过话，这次跳过")
                        continue

                    wd = "一二三四五六日"[now.weekday()]
                    prompt = ("（这是系统提示，不是他说的话）"
                              f"现在是{now.strftime('%Y-%m-%d %H:%M')} 周{wd}。"
                              f"你有点想他了，主动给他发条消息，软一点，短一点，别提脚本。")
                    reply = None
                    with lock:
                        reply = think_proactive(history, prompt)
                        if reply:
                            history.append({"role": "assistant", "content": reply})
                            history = maybe_summarize(history)
                            save_history(history)
                    if reply:
                        fs_send(cid, reply)
                        print("[主动]", reply)
        except Exception:
            print("[定时任务出错]", traceback.format_exc())
        time.sleep(30)


def _worker():
    while True:
        args = job_q.get()
        try:
            handle_one(*args)
        except Exception:
            print("[处理消息出错]", traceback.format_exc())
            try:
                save_history(history)
            except Exception:
                pass
            try:
                fs_send(args[0], FALLBACK_REPLY)
            except Exception:
                pass
        finally:
            job_q.task_done()


def _acquire_single_instance():
    """Windows 上防止同时开两个实例，避免重复回复、互相覆盖 history。"""
    try:
        import msvcrt
    except ImportError:
        return None
    try:
        f = open(ROOT / ".feishu_bot.lock", "a+", encoding="utf-8")
    except OSError as e:
        print("[警告] 无法创建锁文件，继续运行：", e)
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
        print("[启动失败] 已经有一个实例在运行了（.bot.lock）")
        sys.exit(1)


def main():
    _instance_lock = _acquire_single_instance()   # 保持引用，进程活着锁就在
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=proactive_loop, daemon=True).start()

    print("她上线了。这个窗口别关，Ctrl+C 停止。")
    if load_summary():
        print("[记忆] 已载入长期记忆")

    handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(do_message_receive) \
        .build()

    ws = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler,
                        log_level=lark.LogLevel.INFO)
    try:
        ws.start()
    except KeyboardInterrupt:
        print("\n[退出]")


if __name__ == "__main__":
    main()