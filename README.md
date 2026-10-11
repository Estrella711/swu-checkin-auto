# SWU 查寝打卡脚本

西南大学钉钉查寝自动打卡独立脚本。适用于需要在本地或云函数中运行自动打卡的场景。

## 功能特性

- ✅ 自动获取当日打卡任务
- ✅ 支持统一身份认证登录
- ✅ OCR 自动识别验证码
- ✅ 自动填写宿舍信息和位置
- ✅ 支持请假状态检测
- ✅ 防重复打卡
- ✅ 详细状态码返回
- ✅ GitHub Actions 全天短巡检，签到窗口内持续重试
- ✅ 瞬时失败自动重试（多次失败才算失败）
- ✅ 失败邮件通知

## 环境要求

- Python 3.13+
- 依赖库：requests, beautifulsoup4, Pillow, ddddocr

## 快速开始

### 方式一：GitHub Actions 自动签到

无需本地环境，通过云端执行：

1. Fork 本仓库到你的账号
2. 在仓库 **Settings** → **Secrets and variables** → **Actions** 中配置账号密码及邮件 Secrets
3. 启用两个工作流：`SWU定时巡检（不直接打卡）` 每 15 分钟做一次短巡检；只有实际北京时间在 21:00–22:55 时，才派发正式打卡任务
4. 首次在下午启用时，可在北京时间 15:30 后、21:00 前手动运行巡检，设置 `bootstrap: true`、`notify_test: false`；独立等待任务到今晚 21:00 后交接巡检，弥补首日尚无全天事件流的空档
5. 正式任务显示为 `自动查寝 · 日期`，按日期和实际执行时间复核后签到；成功或已签到结束，否则每隔 300 秒继续尝试，22:55 停止并预留邮件时间
6. 23:00 后或之后恢复巡检时，只检查最近一个已结束晚间；若没有确认成功，也没有正在执行或已经处理的失败告警，派发 `未签到告警 · 日期`，仅发邮件，不访问学校接口

全天运行记录属于独立的巡检工作流，窗外正常跳过不会被当成打卡失败。正式打卡工作流不再设置固定时间的 cron，也不使用下午等待任务。巡检采用 `7,22,37,52 * * * *` 和 `timezone: Asia/Shanghai`；不猜测 GitHub 会迟到多少小时。

`bootstrap` 默认关闭，仅用于首次启用当天；之后的定时巡检不等待。等待任务不访问学校接口、不读取账号或邮箱 Secrets，也不阻塞正常巡检；交接绑定目标日期，次日凌晨迟到时仅处理原晚间通知，不打卡到另一天。它仍受 GitHub 排队影响，不能保证准点交接。

GitHub 仍可能延迟或丢弃全部定时事件，API 派发后的正式任务也可能排队；主程序会再次检查日期和窗口，错过时只通知，不提交签到。没有任何巡检启动时无法即时发邮件，恢复后只检查最近一个已结束晚间，不会逐日补全多日断流的告警。详见配置教程中的实际运行证据和通知规则。

**详细配置教程**: [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md)

### 方式二：本地运行

#### 1. 安装依赖

```bash
# 使用 pip
pip install -e .

# 或使用 uv（推荐）
uv sync
```

#### 2. 运行脚本

安装后可直接使用命令行工具：

```bash
# 设置环境变量后运行
export SWUDK_USERNAME="你的学号"
export SWUDK_PASSWORD="你的密码"
swu-checkin
```

或作为 Python 模块调用：

```python
from swu_checkin import check_in

# 从环境变量读取账号密码
import os
result = check_in(os.environ["SWUDK_USERNAME"], os.environ["SWUDK_PASSWORD"])
```

##### Windows PowerShell

```powershell
$env:SWUDK_USERNAME="你的学号"
$env:SWUDK_PASSWORD="你的密码"
swu-checkin
```

##### Linux / macOS

```bash
export SWUDK_USERNAME="你的学号"
export SWUDK_PASSWORD="你的密码"
swu-checkin
```

## 返回状态码

| 状态码 | 含义 |
|-------|------|
| 0 | 今日暂无签到任务 |
| 1 | 签到成功 |
| 2 | 今日已签到，无需重复操作 |
| 3 | 账号或密码验证失败 |
| 4 | 连接错误或请求超时 |
| 5 | 请假中，跳过打卡 |

## 项目结构

```
.
├── .github/
│   └── workflows/
│       ├── checkin.yml       # 正式签到、失败邮件及仅通知告警
│       └── watchdog.yml      # 全天短巡检，不直接打卡
├── src/
│   └── swu_checkin/
│       ├── __init__.py
│       ├── check_in.py       # 主打卡脚本
│       ├── bootstrap.py      # 首次手动启用时等待当晚 21:00，不直接打卡
│       ├── dispatch.py       # 标准库巡检及派发，不读取校园及邮箱 Secrets
│       ├── scheduled.py      # 等待签到窗口、持续重试及截止检查
│       ├── get_info.py       # 信息获取模块
│       ├── verify.py         # 登录验证模块
│       ├── identity.py       # 身份选择处理
│       └── des.py            # DES 加密工具
├── pyproject.toml            # 项目配置和依赖
├── README.md
└── GITHUB_ACTIONS.md         # Actions 配置指南
```

## 工作流程

1. 使用校园网账号密码登录统一身份认证
2. 通过 OCR 识别验证码自动登录
3. 获取 token 和打卡任务信息
4. 检测请假状态
5. 自动填写宿舍位置信息并提交打卡

## 注意事项

- ⚠️ 脚本仅从环境变量读取账号密码
- ⚠️ 切勿将账号密码写入代码或提交到仓库
- ⚠️ 建议在正式使用前先手动测试一次
- ⚠️ 网络异常、验证码识别失败、今日任务暂未生成时会自动重试 3 次，打满才算失败
- ⚠️ 可用环境变量 `SWUDK_MAX_ATTEMPTS`、`SWUDK_RETRY_DELAY` 调整重试次数和等待秒数
- ⚠️ Actions 默认手动运行仍立即执行；勾选 `scheduled` 才使用签到窗口及持续重试，勾选 `notify_test` 可单独验证失败邮件而不访问学校接口
- ⚠️ 巡检工作流也提供默认 `false` 的 `notify_test`；设为 `true` 可验证“巡检派发 → 正式工作流失败邮件”的完整链路，不访问学校接口
- ⚠️ 自动提交限制在北京时间 21:00–23:00，实际尝试截止为 22:55；巡检会填写目标日期，正式任务跨日或迟到后仅通知
- ⚠️ 巡检使用 GitHub 自动提供的 `GITHUB_TOKEN`，无需新增令牌 Secret；校园账号和邮件 Secrets 只由正式工作流使用
- ⚠️ GitHub Actions 使用 Secrets 存储敏感信息，安全可靠

## 相关项目

- **[swu-login](https://github.com/Sorynthia/swu-login)** - 西南大学统一身份认证独立登录模块
- **[swudk-dingtalk](https://github.com/Sorynthia/swudk-dingtalk)** - 钉钉扫码打卡前端工具

## 贡献指南

欢迎提交 Issue 和 Pull Request！请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解详细信息。

## 引用与归属

如果你在项目中使用或参考了本代码，建议按以下方式标注：

```
基于 Sorynthia/swu-checkin 开发
GitHub: https://github.com/Sorynthia/swu-checkin
```

本项目采用 MIT 许可证，欢迎使用和修改，但请保留原作者信息。

## 许可证

MIT License
