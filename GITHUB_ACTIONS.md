# GitHub Actions 自动签到配置指南

工作流沿用已有账号和邮件 Secrets，修复定时运行错过窗口后显示成功、无法发送失败邮件的问题。学校签到窗口为北京时间 **21:00–23:30**；定时程序提前准备环境，21:01 开始尝试，最晚 23:25 停止尝试，为邮件通知留出 5 分钟。

## 本次问题的依据

2026 年 10 月 7 日、8 日的定时运行实际到 UTC 19 时才启动，对应北京时间 10 月 8 日、9 日凌晨 3 时，已经错过签到窗口。旧工作流的窗口检查直接跳过学校接口及邮件步骤，最终却显示绿色成功；该状态只能说明工作流正常跳过，不能说明已签到。例如：[运行 37831095772](https://github.com/Estrella711/swu-checkin-auto/actions/runs/37831095772)。

GitHub 官方明确说明，`schedule` 事件在负载高时可能延迟或被丢弃，且仅运行默认分支上的工作流；公开仓库 60 天没有活动还会自动禁用定时工作流。手动执行成功不代表定时调度正常。[GitHub 定时事件说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 配置账号和失败邮件

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**。已有配置可以继续使用，无需重命名。

| Secret 名称 | 用途 |
| --- | --- |
| `SWU_USERNAME` | 校园网账号 |
| `SWU_PASSWORD` | 校园网密码 |
| `MAIL_SERVER` | SMTP 服务器，例如 `smtp.qq.com` |
| `MAIL_PORT` | SMTP 端口，例如 `587` 或 `465` |
| `MAIL_USERNAME` | 发件邮箱账号 |
| `MAIL_PASSWORD` | 邮箱服务要求的 SMTP 授权码或应用密码 |
| `NOTIFY_EMAIL` | 收件邮箱；多个收件人使用英文逗号分隔 |

失败邮件需要全部五项邮件 Secrets。仅有空白、配置缺失或收件人格式无效时，工作流会明确提示未发送。SMTP 发送失败会单独记录，签到失败仍然显示失败。不要把账号密码、邮箱授权码或触发令牌提交到仓库。

## 新的自动运行方式

| 北京时间计划触发 | UTC cron | 作用 |
| --- | --- | --- |
| 20:43 | `43 12 * * *` | 提前启动并安装依赖，等待至 21:01 开始签到 |
| 21:17 | `17 13 * * *` | 第一轮补救 |
| 22:17 | `17 14 * * *` | 第二轮补救 |
| 23:07 | `7 15 * * *` | 窗口结束前补救 |

每次定时运行使用 `src/swu_checkin/scheduled.py`：

1. 按北京时间检查当天的启动时段，提前启动时等待至 21:01。
2. 执行已有签到程序，保留登录、接口和已签到检查。
3. 登录、网络或暂无任务等可重试状态，间隔 300 秒继续尝试；成功或已签到立即结束。
4. 最晚 23:25 停止新尝试。截止仍未确认成功，或定时运行已经迟到，则返回失败 `[4]`，进入邮件通知流程并把 Actions 标记为失败。

程序在每轮执行前及最终提交前检查日期和时间，防止一次请求或重试拖到窗口外后继续提交。请假状态 `[5]` 沿用原有行为，不继续重试，也不当作已签到。

同一账号的运行跨分支串行处理，`cancel-in-progress: false` 保留正在执行的任务。GitHub 默认只保留一个等待任务，新的补救触发可能替换旧的等待任务，因此四个计划触发并不保证分别执行四次。正在执行的定时程序已经覆盖后续重试时段。[GitHub 并发控制说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)

任务最长运行 180 分钟。23:25 的主动截止用于在任务结束前发送邮件；平台硬超时、取消运行或没有创建运行记录，无法保证执行邮件步骤。

## 手动签到与邮件测试

进入 **Actions → SWU自动签到01 → Run workflow**，选择 `main` 分支。

| 输入 | 默认值 | 作用 |
| --- | --- | --- |
| `scheduled` | `false` | `false` 保留原有立即签到行为；`true` 使用窗口检查、等待和持续重试 |
| `notify_test` | `false` | `true` 模拟一次失败并测试邮件，不访问学校接口 |

正常手动签到保持两个输入为 `false`，直接运行即可。验证定时逻辑时，仅将 `scheduled` 设为 `true`；提前启动会等待到 21:01，窗口外启动会失败并尝试通知。

验证邮件时，仅将 `notify_test` 设为 `true`。这是专门的失败测试，运行显示失败属于预期；检查“发送通知邮件”和“邮件通知结果”，并确认收件箱实际收到邮件。测试成功只证明通知链路有效，不能证明学校签到成功。

工作流也接受 `repository_dispatch` 的 `event_type: swu-checkin`，该事件自动使用定时模式。普通 `workflow_dispatch` 必须显式传入 `scheduled: true` 才会使用定时模式。

## 邮件与最终状态

- `[1]` 签到成功、`[2]` 已签到：不发送失败邮件，Actions 显示成功。
- 定时可重试失败：先在截止前继续尝试，仍未成功再通知。
- 定时启动迟到、截止仍失败、账号配置错误、安装依赖失败或结果缺失：尝试发送失败邮件，Actions 显示失败。
- `[5]` 请假：停止尝试，沿用原有异常通知及失败状态，不表示已签到。

邮件包含账号名称、结果、北京时间和运行日志链接。邮件发送结果独立于签到结果，不会把签到失败变成绿色成功。严重迟到的补救运行可能发生在另一轮已经成功之后；该邮件表示本次运行未能确认成功，应结合学校签到记录和当天其他运行判断。

## 调度限制与可选外部触发

当前方案增强 GitHub 的提前启动、跨时段补救和窗口内重试，**不能保证 GitHub 每天创建运行记录**。所有触发都丢失、工作流被禁用或仓库长期无活动时，未启动的程序无法自行发邮件。若要求每天按时启动，必须使用独立于 GitHub `schedule` 的定时源；本项目已预留 API 入口，本次增强方案继续使用 GitHub Actions。

外部定时器可在北京时间 **20:55、21:10** 调用下面的官方 API，传入 `scheduled: true`。若外部定时器采用 UTC，对应 cron 为 `55 12 * * *`、`10 13 * * *`。令牌放在定时器的 Secret 中；建议使用仅授权此仓库、具备 **Actions: write** 权限的细粒度令牌。[GitHub 工作流派发 API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

```bash
curl --fail-with-body --silent --show-error --request POST \
  --header "Accept: application/vnd.github+json" \
  --header "Authorization: Bearer ${GITHUB_DISPATCH_TOKEN}" \
  --header "Content-Type: application/json" \
  --header "X-GitHub-Api-Version: 2026-03-10" \
  https://api.github.com/repos/Estrella711/swu-checkin-auto/actions/workflows/checkin.yml/dispatches \
  --data '{"ref":"main","inputs":{"scheduled":true}}'
```

Fork 使用者应把 API 中的仓库地址换成自己的仓库。API 派发成功仍要等待 GitHub 执行器启动；严格要求独立于 GitHub 排队的运行时间，应由常驻主机直接运行签到程序。

## 多账号

在 `.github/workflows/checkin.yml` 的 `strategy.matrix.account` 添加账号，并配置对应 Secrets：

```yaml
matrix:
  account:
    - name: "账号1"
      username_secret: SWU_USERNAME
      password_secret: SWU_PASSWORD
    - name: "账号2"
      username_secret: SWU_USERNAME_2
      password_secret: SWU_PASSWORD_2
```

第二个账号添加 `SWU_USERNAME_2`、`SWU_PASSWORD_2`；更多账号依此类推。不同账号并行处理，`fail-fast: false` 避免一个账号失败影响其他账号；每个账号有独立日志及失败通知，共用现有邮件 Secrets。

## 修改时间与排查

工作流使用 UTC cron，北京时间为 UTC + 8；`TZ='Asia/Shanghai'` 的日志时间不会改变 cron 时区。修改 `.github/workflows/checkin.yml` 的触发时间时，同时检查定时程序中的启动时段、21:01 开始时间、23:25 截止和 300 秒重试间隔，确保仍符合学校 21:00–23:30 的窗口。

排查顺序：

1. 查看是否创建当天运行记录；没有记录时先检查工作流启用状态、默认分支、仓库活动和 GitHub 调度。
2. 查看日志中的触发事件、cron、实际北京时间和签到结果，区分调度迟到与学校接口失败。
3. 查看“发送通知邮件”“邮件通知结果”，确认 SMTP 配置与实际收件；Actions 红色状态本身不代表邮件已发送。
4. 账号或网络失败时，参考对应状态码和学校端签到记录；不要仅依据运行颜色。

## 停用

在 **Actions → SWU自动签到01 → Disable workflow** 停用，恢复时选择 **Enable workflow**。GitHub 官方禁用会停止该工作流的定时、手动及 API 触发。如另配了外部定时器，还应在其服务中停用派发。
