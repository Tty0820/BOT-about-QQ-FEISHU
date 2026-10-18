# 开源版改动清单

所有改动只在 `opensource/` 内，**原项目目录 / 根目录脚本都没有动**。

原项目没有 `.git`，所以这里不是 git diff，而是按文件列出的改动点，方便逐条核对。

## 1. 新增的目录/文件

| 文件 | 作用 |
|---|---|
| `common/config.py` | 统一读取 `config.json`、`.env`、`prompts/` |
| `config.example.json` | 配置模板，全部占位符 |
| `.env.example` | 密钥变量名模板，值全空 |
| `.gitignore` | 挡住 `config.json`、`.env`、`data/`、`logs/`、备份 |
| `prompts/*.txt` | 8 份 SFW 示例人设/摘要提示词 |
| `assets/stickers/` | 24 张原创猫咪表情（从原 QQ 版复制） |
| `bots/qq/bot2qq.py` | 原 QQ bot 的开源版 |
| `bots/feishu/bot2feishu.py` | 原飞书 bot 的开源版 |
| `tools/settings.py` | 配置菜单，只改 `config.json` / `.env` |
| `tools/start_all.ps1` / `stop_all.ps1` | 从 `config.json` 读路径/账号 |
| `start_all.bat` / `stop_all.bat` | 双击入口 |
| `README.md` / `LICENSE` / `requirements.txt` | 说明、MIT、依赖 |

## 2. QQ bot 的改动

文件：`bots/qq/bot2qq.py`

1. 顶部文档改成开源目录说明。
2. 删除 `BASE = Path(os.environ.get("QQ_BOT_HOME")...)`，改为从仓库根目录推导 `ROOT`。
3. 删除 `qq_config.json`、`key.txt` 的查找逻辑。
4. 删除 `DEFAULT_CONFIG` 和 `load_config()` 自动写配置逻辑。
5. 新增 `from common.config import ...`，统一读 `config.json`。
6. DeepSeek Key 改为 `.env` 的 `DEEPSEEK_API_KEY_QQ`。
7. OneBot Token 改为 `.env` 的 `QQ_ONEBOT_TOKEN`。
8. `ws_url` 改为 `config.json` 的 `qq.ws_url`。
9. `owner_qq`、`allowed_qqs`、`others_mode` 改为 `qq.*`。
10. 限频改为 `qq.rate_per_minute` / `rate_per_day` / `guest_rate_*`。
11. 群聊开关、只 @ 才回、只回主人、允许群、群限频改为 `qq.*`。
12. 数据目录改为 `data/qq/`。
13. 日志改为 `logs/qq_bot.log`。
14. 锁文件改为仓库根 `.qq_bot.lock`。
15. 表情包目录改为 `qq.sticker_dir`（默认 `assets/stickers`）。
16. 表情概率、大小上限改为 `qq.sticker_auto_chance` / `sticker_max_bytes`。
17. 主人/其他人/群聊人格改为读取 `prompts/qq_owner.txt`、`qq_guest.txt`、`qq_group.txt`。
18. 三个摘要提示词改为读取 `prompts/qq_sum_*.txt`。
19. 模型、备用模型、`api_base`、`temperature` 改为 `ai.*`。
20. 记忆参数 `recent` / `summarize_at` / `keep`、回复长度改为 `qq.*`。
21. `call_model` 和 `/status` 里对 `CFG` 的模型引用改为 `AI_CFG`。
22. 保留原有 SFW 过滤、限频、单实例锁、发送回执、群聊只回主人等逻辑。

未复制到开源版：

- `qq_config.json`（含真实 owner / token）
- `key.txt`
- `qq_data/`、`history.json`、`summary.txt`
- 日志、锁、`.bak`、`_backup`
- 原版成人向人设文本（开源版只放 SFW 示例）

## 3. 飞书 bot 的改动

文件：`bots/feishu/bot2feishu.py`

1. 新增模块文档，说明开源目录。
2. 删除 `api_keys/other.txt`、`bot feishu/key.txt`、上级 `key.txt` 的查找逻辑。
3. DeepSeek Key 改为 `.env` 的 `DEEPSEEK_API_KEY_FEISHU`。
4. `feishu.txt` 的双行读取改为 `config.json` 的 `feishu.app_id` + `.env` 的 `FEISHU_APP_SECRET`。
5. `OWNER_OPEN_ID` 改为 `feishu.owner_open_id`。
6. `history.json` / `summary.txt` / `chat_id.txt` 改为 `data/feishu/`。
7. `PROACTIVE` 改为 `feishu.proactive`。
8. `FALLBACK_MODELS` 改为 `ai.model` + `ai.fallback_models`。
9. `temperature=0.9` 改为 `ai.temperature`。
10. `MAX_IMAGE_BYTES` 改为 `feishu.max_image_bytes`。
11. `RECENT` / `SUMMARIZE_AT` / `KEEP` / `MAX_REPLY_CHARS` 改为 `feishu.*`。
12. 表情包目录、概率、大小改为 `feishu.sticker_*`。
13. 主人提示词改为读取 `prompts/feishu_main.txt`。
14. 摘要提示词改为读取 `prompts/feishu_sum.txt`。
15. 身份提醒改为读取 `prompts/feishu_identity.txt`。
16. 旧 `summary.json` 兼容路径改为 `data/feishu/summary.json`。
17. 锁文件改为仓库根 `.feishu_bot.lock`。
18. 保留身份提醒、主动消息、图片理解、表情发送逻辑。

未复制到开源版：

- `feishu.txt`（含 App ID / Secret）
- `chat_id.txt`、`history.json`、`summary.txt`、`summary.json`
- 原版成人向人设文本

## 4. 启动脚本的改动

原 `启动全部.ps1` 写死了：

- Python 路径
- NapCat 路径
- 小号 QQ
- 原项目下的两个 bot 目录

开源版 `tools/start_all.ps1` 改为：

- 从 `config.json` 读 `startup.python`
- 从 `config.json` 读 `startup.napcat`
- 从 `config.json` 读 `startup.quick_login_qq`
- bot 固定从 `bots/qq`、`bots/feishu` 启动
- `qq.enabled` / `feishu.enabled` 为 false 时跳过
- 3001 已监听时跳过 NapCat
- 已在跑的 bot 跳过

## 5. 配置菜单

`tools/settings.py` 不直接改散落的文件，只改：

- `config.json`：账号、模型、群聊、路径、启动项
- `.env`：4 个密钥/Token

布尔项输入 `是/否`、`y/n`、`true/false`、`1/0`。
列表用逗号分隔，`-` 表示清空。
Secret 输入时隐藏。
写入前备份为 `*.bak`。

## 6. 需要你逐条确认的地方

1. `config.example.json` 的字段命名和默认值。
2. `prompts/` 里的 SFW 示例人设是否符合你的预期。
3. `bots/qq/bot2qq.py` 顶部 1–160 行的配置映射。
4. `bots/feishu/bot2feishu.py` 顶部 1–120 行的配置映射。
5. `tools/start_all.ps1` 的路径/账号读取。
6. `tools/settings.py` 的菜单项和校验。
7. `README.md` 的权限、目录、启动说明。

## 7. 开源前还要做

- 轮换所有真实 Key / Token / App Secret。
- 不要把原 `bot qq`、`bot feishu` 整个文件夹直接复制进仓库。
- 确认没有 `history.json`、`summary.txt`、`qq_data`、`chat_id.txt`、`feishu.txt`、`qq_config.json`。
- 如果以后初始化 git，先确认 `.gitignore` 生效：

```powershell
git status --short
```

不应该看到 `config.json`、`.env`、`data/`、`logs/`。
