# GitHub Actions 自动签到配置指南

上一版增加了窗口保护、重试和失败邮件，但没有解决实际的多小时触发延迟。原来的 UTC cron 换算正确，修改日志显示时区或增加窗口检查无法让 GitHub 提前创建运行。本版改为北京时间 **15:43 提前启动独立预热任务**，吸收已经观测到的约 5 小时延迟；自动提交限制在 **21:00–23:00**，21:01 开始尝试，最晚 22:55 停止尝试，为邮件通知留出 5 分钟。

## 本次问题的依据

运行日志中的触发 cron 与 GitHub API 的创建时间表明，问题出在定时事件创建迟到：

| 运行 | 实际触发 cron（原 UTC 配置） | 应触发的北京时间 | 实际创建的北京时间 | 延迟 |
| --- | --- | --- | --- | --- |
| [37980890096](https://github.com/Estrella711/swu-checkin-auto/actions/runs/37980890096) | `17 14 * * *` | 2026-10-09 22:17 | 2026-10-10 03:32:11 | 约 5 小时 15 分钟 |
| [37984530029](https://github.com/Estrella711/swu-checkin-auto/actions/runs/37984530029) | `7 15 * * *` | 2026-10-09 23:07 | 2026-10-10 04:04 | 约 4 小时 57 分钟 |

上一版于 10 月 9 日 21:56 部署，当天 20:43 的计划时间已经过去，无法补执行已经错过的提前启动；后两次运行仍直接证明了多小时调度延迟。此前 [运行 37831095772](https://github.com/Estrella711/swu-checkin-auto/actions/runs/37831095772) 还存在窗口外直接跳过接口及通知、最终显示绿色的问题；绿色跳过不能说明已签到。截图中的显示时间应以对应运行日志及 API 时间换算为准。

GitHub 官方明确说明，`schedule` 事件在负载高时可能延迟或被丢弃，且仅运行默认分支上的工作流；公开仓库 60 天没有活动还会自动禁用定时工作流。手动执行成功不代表定时调度正常。[GitHub 定时事件说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 配置账号和失败邮件

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**。本次排查已修正 SMTP 配置并验证失败邮件测试，现有账号和邮件 Secret 名称继续使用，无需重命名。

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

| 北京时间计划触发 | cron（`Asia/Shanghai`） | 作用 |
| --- | --- | --- |
| 15:43 | `43 15 * * *` | 独立预热任务等待到 20:50，再进入签到任务 |
| 21:17 | `17 21 * * *` | 第一轮补救 |
| 22:17 | `17 22 * * *` | 第二轮补救 |
| 22:47 | `47 22 * * *` | 23:00 前最后一轮补救 |

每条 cron 显式设置 `timezone: Asia/Shanghai`，小时直接写北京时间。GitHub 已正式支持这个字段；它用于明确解释 cron，不能保证 GitHub 按时创建事件，也不能单独修复约 5 小时的延迟。[GitHub 时区支持公告](https://github.blog/changelog/2026-03-19-github-actions-late-march-2026-updates/)

15:43 的运行分成两个任务：

1. **预热任务**只使用标准库等待到北京时间 20:50，不读取校园账号和邮箱 Secrets，不安装 OCR 依赖，也不访问学校接口。若启动时已过 20:50，就直接进入后续签到任务。
2. **签到任务**安装依赖，使用 `src/swu_checkin/scheduled.py` 等待到 21:01，然后按窗口执行。

这样，正常启动的预热任务会提前等待；若重复已观测到的 5 小时 15 分钟延迟，15:43 的计划约在 20:58 创建，仍有机会在晚间窗口内执行。预热结束后，独立的签到任务仍需申请执行器，可能继续排队。Actions 页面下午显示一条正在运行的记录属于设计行为，运行记录时间不等于学校接口提交时间。

21:17、22:17、22:47 的补救触发直接进入签到任务。定时程序处理如下：

1. 接受当天北京时间 20:40–22:55 的启动，提前启动时等待至 21:01。
2. 执行已有签到程序，保留登录、接口和已签到检查。
3. 登录、网络或暂无任务等可重试状态，间隔 300 秒继续尝试；成功或已签到立即结束。
4. 最晚 22:55 停止新尝试。截止仍未确认成功，或定时运行已经迟到，则返回失败 `[4]`，进入邮件通知流程并把 Actions 标记为失败。

学校接口的自动提交检查限制在北京时间 21:00–23:00 内，实际截止提前至 22:55。程序在每轮执行前及最终提交前检查日期和时间，防止一次请求或重试拖到窗口外后继续提交。请假状态 `[5]` 沿用原有行为，不继续重试，也不当作已签到。

同一账号的运行跨分支串行处理，`cancel-in-progress: false` 保留正在执行的任务。GitHub 默认只保留一个等待任务，新的补救触发可能替换旧的等待任务，因此四个计划触发并不保证分别执行四次。正在执行的定时程序已经覆盖后续重试时段。[GitHub 并发控制说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)

预热任务最长运行 330 分钟，低于 GitHub 托管执行器单个任务 6 小时的上限；签到任务另有 180 分钟上限，两者分开计时。[GitHub Actions 运行限制](https://docs.github.com/en/actions/reference/limits) 22:55 的主动截止用于在签到任务结束前发送邮件；平台硬超时、取消运行或没有创建运行记录，无法保证执行邮件步骤。

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
- 预热任务失败、定时启动迟到、跨北京时间日期、截止仍失败、账号配置错误、安装依赖失败或结果缺失：尝试发送失败邮件，Actions 显示失败。
- `[5]` 请假：停止尝试，沿用原有异常通知及失败状态，不表示已签到。

邮件包含账号名称、结果、北京时间和运行日志链接。邮件发送结果独立于签到结果，不会把签到失败变成绿色成功。严重迟到的补救运行可能发生在另一轮已经成功之后；该邮件表示本次运行未能确认成功，应结合学校签到记录和当天其他运行判断。

## 调度限制与可选外部触发

当前方案通过下午提前启动吸收已观测到的多小时延迟，继续使用 GitHub Actions，**不能保证 GitHub 每天创建运行记录**。所有触发都丢失、延迟超过可用余量、工作流被禁用或仓库长期无活动时，未启动的程序无法自行发邮件。严格要求独立于 GitHub 定时事件的触发，需要外部定时源；本项目已预留 API 入口，当前不部署额外服务。

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

工作流每条 cron 使用显式 `timezone: Asia/Shanghai`，小时直接采用北京时间；只设置 `TZ='Asia/Shanghai'` 日志变量不会改变 cron 时区。修改 `.github/workflows/checkin.yml` 的触发时间时，同步检查预热等待至 20:50、定时程序 20:40 的最早启动时间、21:01 开始时间、22:55 截止和 300 秒重试间隔，确保自动提交始终在目标 21:00–23:00 内。

排查顺序：

1. 查看是否创建当天运行记录；没有记录时先检查工作流启用状态、默认分支、仓库活动和 GitHub 调度。
2. 查看日志中的触发事件、cron、实际北京时间和签到结果，区分调度迟到与学校接口失败。
3. 查看“发送通知邮件”“邮件通知结果”，确认 SMTP 配置与实际收件；Actions 红色状态本身不代表邮件已发送。
4. 账号或网络失败时，参考对应状态码和学校端签到记录；不要仅依据运行颜色。

## 停用

在 **Actions → SWU自动签到01 → Disable workflow** 停用，恢复时选择 **Enable workflow**。GitHub 官方禁用会停止该工作流的定时、手动及 API 触发。如另配了外部定时器，还应在其服务中停用派发。
