# GitHub Actions 自动签到配置指南

## 功能说明

- **自动签到**: 每天北京时间 21:03、21:17、21:29 错峰执行（GitHub Actions 可能延迟或丢弃任务）
- **多账号支持**: 一个 workflow 支持多个账号并行签到
- **手动触发**: 支持在 GitHub Actions 页面手动运行
- **自动重试**: 登录/网络/无任务等瞬时失败会自动重试，多次失败才发邮件
- **请求超时**: Actions 设置 `SWUDK_REQUEST_TIMEOUT=30`，给晚间学校接口 30 秒等待时间；未设置时保留原 10 秒默认值
- **智能通知**: 签到异常时自动发送详细邮件通知
- **详细日志**: 完整记录每步执行情况，便于排查问题
- **停用方式**: 使用 GitHub 官方 Disable workflow，不要在 YAML 里加开关

---

## 快速开始（单账号）

### 第一步：配置账号密码

进入仓库页面: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

添加以下 secrets（⚠️ 不要提交到代码里）:

| Secret 名称 | 说明 | 示例 |
|------------|------|------|
| SWU_USERNAME | 校园网账号 | 2021xxxxxx |
| SWU_PASSWORD | 校园网密码 | your_password |

### 第二步：配置邮件通知（可选）

如果需要签到异常时收到邮件通知，继续添加以下配置：

| Secret 名称 | 说明 | 示例 |
|------------|------|------|
| MAIL_SERVER | SMTP 服务器地址 | smtp.gmail.com / smtp.qq.com |
| MAIL_PORT | SMTP 端口 | 587 (TLS) 或 465 (SSL) |
| MAIL_USERNAME | 发件邮箱账号 | your_email@gmail.com |
| MAIL_PASSWORD | 邮箱授权码（不是登录密码） | xxxx xxxx xxxx xxxx |
| NOTIFY_EMAIL | 接收通知的邮箱 | your_email@qq.com |

---

## 多账号配置

### 添加第二个账号

#### 步骤 1：编辑 workflow 文件

打开 `.github/workflows/checkin.yml`，找到 `strategy.matrix.account`：

```yaml
matrix:
  account:
    - name: "账号1"
      username_secret: SWU_USERNAME
      password_secret: SWU_PASSWORD
    # 如需添加更多账号，取消下面的注释并配置对应的 Secrets
    # - name: "账号2"
    #   username_secret: SWU_USERNAME_2
    #   password_secret: SWU_PASSWORD_2
```

**取消注释并修改账号名称**：

```yaml
matrix:
  account:
    - name: "张三"
      username_secret: SWU_USERNAME
      password_secret: SWU_PASSWORD
    - name: "李四"
      username_secret: SWU_USERNAME_2
      password_secret: SWU_PASSWORD_2
```

#### 步骤 2：添加第二个账号的 Secrets

在 GitHub Secrets 中添加：

| Secret 名称 | 说明 |
|------------|------|
| SWU_USERNAME_2 | 第二个账号 |
| SWU_PASSWORD_2 | 第二个密码 |

### 添加更多账号

继续在 `matrix.account` 数组中添加：

```yaml
matrix:
  account:
    - name: "张三"
      username_secret: SWU_USERNAME
      password_secret: SWU_PASSWORD
    - name: "李四"
      username_secret: SWU_USERNAME_2
      password_secret: SWU_PASSWORD_2
    - name: "王五"
      username_secret: SWU_USERNAME_3
      password_secret: SWU_PASSWORD_3
```

对应添加 Secrets：`SWU_USERNAME_3`、`SWU_PASSWORD_3`

### 多账号特性

- ✅ **并行执行**: 所有账号同时签到，速度快
- ✅ **互不干扰**: 一个账号失败不影响其他账号
- ✅ **独立日志**: 每个账号有独立的执行日志
- ✅ **分别通知**: 每个账号异常时分别发送邮件

---

## 邮件通知规则

### 正常情况（不发邮件）
- ✅ `[1]` 签到成功
- ✅ `[2]` 已签到

### 异常情况（自动发邮件）
- ⚠️ `[0]` 今日无签到记录
- ❌ `[3]` 登录失败（账号密码错误）
- ❌ `[4]` 网络错误或数据异常
- ℹ️ `[5]` 请假期间无需签到

邮件包含账号名称、状态码、详细原因和处理建议。邮件配置缺失、仅有空白或收件人格式无效时跳过通知；SMTP 失败不影响签到结果。`NOTIFY_EMAIL` 应填写有效邮箱地址，多个收件人使用英文逗号分隔。

Actions 最终状态只在签到进程正常退出且返回 `[1]` 或 `[2]` 时判定成功；其他状态及结果缺失均标记失败，避免签到失败却显示绿色。`[5]` 表示请假跳过，不能当作已签到。运行摘要会显示状态码、签到进程退出码及独立的邮件结果。

---

## 常见邮箱配置

### QQ 邮箱
```
MAIL_SERVER: smtp.qq.com
MAIL_PORT: 587
MAIL_USERNAME: your_qq@qq.com
MAIL_PASSWORD: 授权码（在 QQ 邮箱设置 → 账户 → 开启 SMTP 服务获取）
```

### Gmail
```
MAIL_SERVER: smtp.gmail.com
MAIL_PORT: 587
MAIL_USERNAME: your_email@gmail.com
MAIL_PASSWORD: 应用专用密码（在 Google 账户安全设置中生成）
```

### 163 邮箱
```
MAIL_SERVER: smtp.163.com
MAIL_PORT: 465
MAIL_USERNAME: your_email@163.com
MAIL_PASSWORD: 授权码（在邮箱设置 → POP3/SMTP/IMAP 中开启）
```

### Outlook
```
MAIL_SERVER: smtp-mail.outlook.com
MAIL_PORT: 587
MAIL_USERNAME: your_email@outlook.com
MAIL_PASSWORD: 邮箱密码或应用密码
```

---

## 使用说明

### 自动运行
配置完 Secrets 后，Actions 会在每天北京时间 21:03、21:17、21:29 尝试自动执行。沿用原工作流“窗口内多轮错峰、已签到后跳过”的设计，第一轮提前，后两轮相隔 14 分钟和 12 分钟，避开 :00/:15/:30/:45。GitHub 可能延迟或丢弃定时任务，三个时间是计划触发时间，不保证准点启动。

同一账号的手动和定时任务串行执行。每次仍由原签到程序查询学校接口：`qdzt == "已签到"` 时直接返回 `[2]`，不提交签到。定时任务若延迟至北京时间 21:00–23:30 窗口外，或由已移除的旧 cron 触发，会在安装依赖及签到前跳过，并在摘要明确注明“未提交签到”。这不能阻止 GitHub 创建迟到的运行记录，但能防止凌晨实际提交。

手动触发保留原行为，不受定时窗口检查限制；两种触发共用同一个账号 matrix、Secrets、环境变量、仓库根目录及执行命令。日志新增触发事件、实际触发 cron 和北京时间，便于区分定时延迟与配置问题。

### 手动触发
1. 进入仓库 **Actions** 页面
2. 选择 **自动签到** workflow
3. 点击 **Run workflow** → **Run workflow**

### 查看日志

**Actions** → 选择运行记录 → 展开对应账号 → 查看详细日志

多账号时日志结构：
```
签到 - 账号1
  ✅ 签到成功
签到 - 账号2
  ✅ 签到成功
```

---

## 日志示例

### 单账号成功签到
```
=========================================
开始执行签到任务
账号名称: 账号1
时间: 2024-01-15 21:00:15
账号: 2021****23
=========================================

[1] 签到成功

✅ 签到正常完成
```

### 瞬时失败后自动重试成功
```
第 1/3 次失败（登录失败），8 秒后重试
第 2/3 次失败（网络错误或数据异常），16 秒后重试
[1] 签到成功

✅ 签到正常完成
```

### 多账号执行
```
[签到 - 张三]
=========================================
开始执行签到任务
账号名称: 张三
账号: 2021****23
=========================================
✅ 签到成功

[签到 - 李四]
=========================================
开始执行签到任务
账号名称: 李四
账号: 2022****56
=========================================
✅ 签到成功
```

---

## 修改执行时间

编辑 `.github/workflows/checkin.yml`:

```yaml
schedule:
  # cron 格式: 分 时 日 月 周
  # 北京时间 = UTC + 8
  # GitHub Actions 定时任务可能有延迟，实际执行时间通常晚于设定时间
  - cron: '3 13 * * *'   # 北京时间 21:03
  - cron: '17 13 * * *'  # 北京时间 21:17
  - cron: '29 13 * * *'  # 北京时间 21:29
```

常用时间：
- 每天 21:03: `3 13 * * *`
- 每天 21:17: `17 13 * * *`
- 每天 21:29: `29 13 * * *`

本仓库明确采用 UTC cron，不配置 `schedule.timezone`；`TZ='Asia/Shanghai' date` 只用于显示和检查实际执行时间，不会改变 cron 时区。修改 cron 时需同步更新“检查定时触发窗口”步骤中的允许列表。

---

## 安全说明

✅ **安全实践**:
- 所有敏感信息存储在 GitHub Secrets，加密存储
- Secrets 不会出现在日志中
- 日志中账号显示为脱敏格式（如 `2021****23`）
- 代码仓库中不包含任何凭证

⚠️ **注意事项**:
- 不要在公开 Issue/PR 中提及凭证
- 定期更新密码和授权码
- 如果仓库变为公开，重新检查 Secrets 配置

---

## 故障排查

### Secret 未配置
**现象**: 日志显示 `⚠️ 账号未配置`
**解决**: 检查 Secret 名称是否与 workflow 中的配置一致

### 签到失败（状态码 3）
**原因**: 账号或密码错误
**解决**:
1. 检查对应的 Secret 是否正确
2. 确认账号未被锁定或冻结
3. 尝试在浏览器手动登录验证

### 网络错误（状态码 4）
**原因**: 网络超时或学校系统维护
**解决**:
1. 查看 Actions 日志中的具体错误信息
2. 等待下次自动重试
3. 检查学校系统是否正常

### 多账号部分失败
**现象**: 一个账号成功，另一个失败
**原因**: 多账号使用 `fail-fast: false`，互不影响
**解决**: 分别查看每个账号的日志，单独处理

---

## 禁用自动签到

### 方法一：官方 Disable workflow（推荐）
**Actions** → **自动签到** → **Disable workflow**

GitHub 官方禁用后，定时任务和手动触发都会停止。需要恢复时再 **Enable workflow**。

### 方法二：永久删除
删除文件: `.github/workflows/checkin.yml`
