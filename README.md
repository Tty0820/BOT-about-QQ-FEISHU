# QQ + 飞书 双线聊天 bot（开源模板）

一个本地运行的 QQ / 飞书聊天 bot：

- QQ：NapCat + OneBot 11，支持私聊、群聊（只 @ 才回、可限制只回主人）
- 飞书：WebSocket 长连接，私聊、主动消息、图片理解
- 两条线独立：各自的人格、记忆、表情包、限频
- 人设、模型、账号、Key 全部走配置/环境变量，不写死在代码里
- 自带一套原创猫咪文字表情（8 类 × 3 张）

> 这是从个人自用版整理出的开源模板。示例人设是 SFW 的，真实使用时请自己改 `prompts/`。

## 目录

```
.
├─ config.example.json     配置模板（提交）
├─ .env.example            密钥模板（提交）
├─ requirements.txt
├─ prompts/                人设、摘要提示词
├─ assets/stickers/        表情包
├─ bots/qq/bot2qq.py
├─ bots/feishu/bot2feishu.py
├─ common/config.py        统一配置加载
├─ tools/settings.py       配置菜单
├─ tools/start_all.ps1     一键启动
├─ tools/stop_all.ps1      一键停止
├─ start_all.bat
└─ stop_all.bat
```

运行时生成、**不会提交**：

```
config.json
.env
data/
logs/
*.log
*.bak
```

## 快速开始

### 1. 装依赖

需要 Python 3.10+。

```powershell
python -m pip install -r requirements.txt
```

### 2. 生成配置

```powershell
copy config.example.json config.json
copy .env.example .env
```

然后编辑 `config.json`，至少填：

- `qq.owner_qq`：你的 QQ 号
- `feishu.app_id`：飞书应用 App ID
- `startup.python`：Python 路径（可写 `python`）
- `startup.napcat`：NapCat 启动脚本路径
- `startup.quick_login_qq`：小号 QQ（不需要快速登录就填 0）

编辑 `.env`：

```dotenv
DEEPSEEK_API_KEY_QQ=sk-...
DEEPSEEK_API_KEY_FEISHU=sk-...
FEISHU_APP_SECRET=...
QQ_ONEBOT_TOKEN=...
```

> 两套 Key 可以相同，也可以不同。QQ 的 OneBot Token 必须和 NapCat 里配置的一致。

### 3. 改人设

`prompts/` 里是示例人设，直接改文本即可，不用动代码：

```
qq_owner.txt        QQ 主人人格
qq_guest.txt        QQ 其他人人格
qq_group.txt        QQ 群聊人格
qq_sum_owner.txt    QQ 主人摘要提示词
qq_sum_guest.txt    QQ 其他人摘要提示词
qq_sum_group.txt    QQ 群摘要提示词
feishu_main.txt     飞书人格
feishu_sum.txt      飞书摘要提示词
feishu_identity.txt 飞书身份提醒
```

### 4. NapCat / 飞书

**QQ**

- 用**小号**登录 QQ（不要主号）。
- NapCat 开启 OneBot 11 的 WebSocket 服务端，默认 `ws://127.0.0.1:3001`。
- 在 NapCat 里设置 Token，并写入 `.env` 的 `QQ_ONEBOT_TOKEN`。
- `startup.quick_login_qq` 填小号 QQ 可快速登录；不填就手动扫码。

**飞书**

- 飞书开放平台创建企业自建应用，开启机器人能力。
- 需要权限：`im:message`、`im:message:send_as_bot`、`im:resource:upload`（发表情图片用）。
- 事件订阅选长连接；订阅 `im.message.receive_v1`。
- 把 App ID 写到 `config.json`，App Secret 写到 `.env`。
- 第一次收到消息后，日志会打印 `[发送者] open_id=ou_...`，把它填到 `feishu.owner_open_id` 可限制只回你。

### 5. 启动

```powershell
# 方式一：双击
start_all.bat

# 方式二：手动
python bots/qq/bot2qq.py
python bots/feishu/bot2feishu.py
```

只改配置可以用：

```powershell
python tools/settings.py
python tools/settings.py --show
```

## 配置说明

### `config.json`

| 字段 | 说明 |
|---|---|
| `ai.api_base` | OpenAI 兼容接口地址 |
| `ai.model` / `ai.fallback_models` | 主模型 / 备用模型 |
| `ai.temperature` | 温度 |
| `qq.enabled` | 是否启动 QQ |
| `qq.ws_url` | OneBot WS 地址 |
| `qq.owner_qq` | 主人 QQ，只有这个号用主人人格 |
| `qq.allowed_qqs` | 私聊白名单，空 = 所有人 |
| `qq.others_mode` | `guest` 普通助手 / `block` 忽略 |
| `qq.allow_group` | 群聊开关 |
| `qq.group_require_at` | 群里只 @ 才回 |
| `qq.group_only_owner` | 群里只回主人 |
| `qq.allowed_groups` | 允许的群，空 = 所有群 |
| `qq.persona` | 人设/摘要提示词文件路径 |
| `qq.sticker_dir` | 表情包目录 |
| `feishu.enabled` | 是否启动飞书 |
| `feishu.app_id` | 飞书 App ID |
| `feishu.owner_open_id` | 只回这个 open_id；空 = 所有人 |
| `feishu.proactive` | 主动消息时间，如 `["09:00","21:30"]` |
| `feishu.persona` | 飞书人设/摘要提示词 |
| `paths.data_dir` / `paths.log_dir` | 数据/日志目录 |
| `startup.python` | Python 路径 |
| `startup.napcat` | NapCat 启动脚本 |
| `startup.quick_login_qq` | 快速登录小号 QQ |

### `.env`

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY_QQ` | QQ 线用的 Key |
| `DEEPSEEK_API_KEY_FEISHU` | 飞书线用的 Key |
| `FEISHU_APP_SECRET` | 飞书 App Secret |
| `QQ_ONEBOT_TOKEN` | NapCat OneBot Token |

## 安全 & 隐私

- `config.json`、`.env`、`data/`、`logs/` 都不要提交；`.gitignore` 已处理。
- 不要把真实 Key、App Secret、OneBot Token、QQ 号、open_id 写进 README/截图/示例配置。
- 用过的 Key 如果曾经进过仓库或公开过，去后台**重建/轮换**。
- NapCat 跑真实 QQ 小号，存在封号风险；只用小号，不群发、不绑支付。
- 本项目不提供任何色情内容；示例人设为 SFW。真实人设请自行负责合规。

## 表情包

`assets/stickers/` 下是原创猫咪文字表情（8 类 × 3 张），可自由替换成自己的图片。
支持 `png / jpg / jpeg / gif / webp`；某个类别为空会退回 emoji。

## License

MIT，见 `LICENSE`。
