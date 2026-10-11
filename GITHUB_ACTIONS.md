# GitHub Actions 自动签到配置指南

正式打卡和全天巡检现在使用两个独立工作流。巡检每 15 分钟短暂检查一次，只有**实际北京时间 21:00–22:55** 才派发正式任务；正式任务再次核对日期和执行时间后打卡。窗外正常巡检不直接打卡，也不会被当作打卡失败。

目标自动提交时段仍为 **21:00–23:00**，实际尝试截止为 **22:55**，为失败邮件留出 5 分钟。此次移除正式工作流的四条固定 cron 及下午等待任务，不再猜测 GitHub 会迟到多少小时。

## 实际运行证据

原 UTC 换算没有错误，显式 `timezone: Asia/Shanghai` 也不能消除平台延迟。上一版固定提前启动仍出现以下情况：

| 旧计划的北京时间 | 实际创建的北京时间 | 运行及结果 |
| --- | --- | --- |
| 2026-10-10 15:43 | 2026-10-10 21:57:46 | [38057735615](https://github.com/Estrella711/swu-checkin-auto/actions/runs/38057735615)，迟到 6 小时 14 分 46 秒；约 21:58 查询返回 `[2]` 已签到 |
| 2026-10-10 21:17 | 2026-10-11 01:51 | [38073445477](https://github.com/Estrella711/swu-checkin-auto/actions/runs/38073445477)，窗口外补跑失败 |
| 2026-10-10 22:17 | 2026-10-11 02:35 | [38076362939](https://github.com/Estrella711/swu-checkin-auto/actions/runs/38076362939)，窗口外补跑失败 |
| 2026-10-10 22:47 | 2026-10-11 02:54 | [38077696755](https://github.com/Estrella711/swu-checkin-auto/actions/runs/38077696755)，窗口外补跑失败 |

`[2]` 只证明查询时学校端已经签到，不能证明本次流程提交成功，可能此前已手动签到。后续凌晨补跑的失败通知，也不代表当天学校端未签到。

GitHub 官方明确说明，`schedule` 事件可能延迟或被丢弃，且仅运行默认分支上的工作流；公开仓库 60 天没有活动会自动禁用定时工作流。手动执行成功不代表定时调度正常。[GitHub 定时事件说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 配置账号和失败邮件

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**。现有账号和邮件 Secret 名称继续使用，无需重命名；收件地址已修正，现有 SMTP 配置保留并验证发送成功。

| Secret 名称 | 用途 |
| --- | --- |
| `SWU_USERNAME` | 校园网账号 |
| `SWU_PASSWORD` | 校园网密码 |
| `MAIL_SERVER` | SMTP 服务器，例如 `smtp.qq.com` |
| `MAIL_PORT` | SMTP 端口，例如 `587` 或 `465` |
| `MAIL_USERNAME` | 发件邮箱账号 |
| `MAIL_PASSWORD` | 邮箱服务要求的 SMTP 授权码或应用密码 |
| `NOTIFY_EMAIL` | 收件邮箱；多个收件人使用英文逗号分隔 |

失败邮件需要全部五项邮件 Secrets。配置缺失、仅有空白或收件人格式无效时，工作流会明确提示未发送。SMTP 发送失败单独记录，不会把签到失败变成成功。不要把账号密码、邮箱授权码或令牌提交到仓库。

巡检使用 GitHub 为每个任务自动提供的 `GITHUB_TOKEN`，**无需新增令牌 Secret**。`actions: write` 权限仅授予巡检任务，用于查询运行记录并派发正式工作流；正式工作流保留 `contents: read`。巡检程序使用标准库，不读取校园账号或邮件 Secrets，不访问学校接口，不输出令牌。校园账号和邮件 Secrets 仅由正式工作流使用。

GitHub 允许 `GITHUB_TOKEN` 触发 `workflow_dispatch`；这个事件属于不会被阻止创建新运行的特例。派发接口需要 Actions 写权限。[GITHUB_TOKEN 文档](https://docs.github.com/en/actions/concepts/security/github_token)、[工作流派发 API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

## 两个工作流的职责

### 全天巡检：watchdog.yml

Actions 中的名称为 **SWU定时巡检（不直接打卡）**。定时配置为：

```yaml
schedule:
  - cron: '7,22,37,52 * * * *'
    timezone: Asia/Shanghai
```

每天计划创建 96 次短巡检，在每小时 :07、:22、:37、:52 检查。它根据**实际执行时的北京时间**判断，而不是把迟到 cron 当作原计划日期的打卡任务。

巡检调用 `src/swu_checkin/dispatch.py`：

1. 实际时间在 21:00–22:55 时，查询当天正式运行。已有成功或正在执行的任务，则不重复派发。
2. 需要尝试时，派发 `main` 分支的 `checkin.yml`，传入 `scheduled: true` 和当天 `target_date`。
3. 窗外不派发打卡；正常跳过不会生成失败邮件，也不代表学校端已经签到。
4. 到 23:00 或之后恢复巡检时，检查最近一个已结束晚间是否得到成功确认。没有成功、已完成的失败告警、正在执行的任务或既有未签到通知时，派发 `missed_date` 的仅通知任务；不会逐日补查多日断流期间的所有晚间。

全天记录属于独立巡检工作流。每次检查后即结束，不全天占用执行器。公开仓库的标准 GitHub 托管执行器免费；若改为私有仓库，会使用账户的 Actions 分钟额度。[GitHub 执行器说明](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)

### 正式打卡及通知：checkin.yml

Actions 中保留 **SWU自动签到01**，已经移除其 `schedule` 和下午预热任务。自动派发的运行标题为 **自动查寝 · 日期**；只在巡检确认当前窗口适合执行时派发。

API 派发之后仍可能排队。正式任务启动时会再次核对目标日期及真实北京时间；日期不符或已经错过窗口时不访问学校接口，转入通知。运行标题带目标日期，避免把上一晚的迟到任务误当作今天的签到。

通过检查后，安装依赖并执行 `src/swu_checkin/scheduled.py`：

1. 21:01 前等待，不提前访问学校接口。
2. 执行已有登录、宿舍信息和签到状态检查。
3. 登录、网络或暂无任务等可重试状态，间隔 300 秒继续尝试；成功或已签到立即结束。
4. 最晚 22:55 停止尝试，仍未确认成功时进入失败邮件流程。

程序在每轮执行前及最终提交前检查日期和时间。每个账号的运行跨分支串行处理，`cancel-in-progress: false` 保留正在执行的任务；GitHub 默认只保留一个等待任务，新的等待运行可能替换旧的等待运行。[GitHub 并发控制说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)

仅通知的运行标题为 **未签到告警 · 日期**，通过 `missed_date` 指定晚间日期，只执行邮件流程，**不调用学校接口、不补打过去日期**。巡检查到已有成功、失败告警、活跃任务或相同日期的未签到通知后，不再重复派发该未签到告警。

## 邮件与运行状态

- `[1]` 签到成功、`[2]` 已签到：正式运行显示成功，不发送失败邮件。
- 窗内尝试到截止仍失败、账号配置错误、环境准备失败、结果缺失、跨日或迟到的正式任务：执行失败邮件流程。
- 晚间缺少成功确认：派发带日期的仅通知告警，不访问学校接口。
- 巡检窗外正常跳过或发现当天已有成功、告警或活跃任务：不发邮件。
- `[5]` 请假：停止重试，保留原有异常通知和失败状态，不表示已签到。

邮件发送结果与签到结果分别记录。“已有告警运行”不等于收件箱已收到邮件；需要结合正式任务的“发送通知邮件”“邮件通知结果”和实际收件确认。巡检工作流的绿色状态只说明巡检正常结束，应查看对应日期的正式运行或学校端记录确认签到。

## 手动签到与邮件测试

进入 **Actions → SWU自动签到01 → Run workflow**，选择 `main` 分支。

| 输入 | 默认值 | 作用 |
| --- | --- | --- |
| `scheduled` | `false` | `false` 保留原有立即签到行为；`true` 使用日期、窗口检查和持续重试 |
| `target_date` | 空 | 自动任务的目标北京时间日期，格式 `YYYY-MM-DD`；巡检自动填写当天 |
| `notify_test` | `false` | `true` 模拟一次失败并测试邮件，不访问学校接口 |
| `missed_date` | 空 | 仅通知指定晚间未确认成功，不访问学校接口；由巡检自动填写 |

普通手动打卡保持两个开关为 `false`，日期输入为空，保留原来的立即执行行为。

验证自动模式时，设置 `scheduled: true` 并填写当天 `target_date`，在北京时间窗口内运行；正式任务仍检查实际开跑的日期和时间，迟到则不提交。

测试邮件时，仅将 `notify_test` 设为 `true`，其他输入保持默认。测试运行显示失败属于预期，应检查发送步骤和收件箱。测试成功只证明通知链路有效，不能证明学校签到成功。

验证完整的“巡检派发 → 正式工作流失败邮件”链路：进入 **Actions → SWU定时巡检（不直接打卡） → Run workflow**。巡检的 `notify_test` 默认是 `false`；仅在测试时设为 `true`。巡检使用自身 `GITHUB_TOKEN` 派发正式工作流的邮件测试，不访问学校接口。检查巡检派发结果、后续正式测试运行的邮件步骤和实际收件。

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

## 调度限制与排查

全天短巡检提高了晚间得到执行机会的可能性，避免依赖某个固定提前量；**不能保证 GitHub 的定时事件都不会丢失，也不能保证 21:00 准点执行**。派发后的正式任务也可能排队，因此日期和实际时间检查始终保留，截止不放宽。

如果晚间所有巡检都未启动，系统无法当时发送邮件。之后恢复巡检时，只检查最近一个已结束晚间，才有机会发现缺少成功记录并派发未签到通知；连续多日断流不会逐日补全告警。工作流被禁用或平台持续没有创建任何运行时，同样无法及时通知。

排查顺序：

1. 查看 **SWU定时巡检（不直接打卡）**，确认是否有实际北京时间在窗口内的巡检，以及是否派发或因成功、活跃任务而跳过。
2. 查看对应 **自动查寝 · 日期** 的实际执行时间、目标日期和 `[1]`/`[2]` 结果，结合学校记录判断；`[2]` 不代表这次提交。
3. 若看到 **未签到告警 · 日期**，它只通知对应晚间缺少确认，不会尝试窗口外打卡。
4. 检查正式任务的邮件步骤与实际收件；不要把巡检的绿色状态或告警运行存在当作签到、收信成功。

修改 `.github/workflows/watchdog.yml` 的巡检频率时，保留 `timezone: Asia/Shanghai`。修改窗口时，需同步巡检、正式任务 guard、定时程序及提交前检查；当前目标 21:00–23:00、实际截止 22:55 不因延迟而放宽。

## 停用

停止自动巡检：在 **Actions → SWU定时巡检（不直接打卡） → Disable workflow** 停用，恢复时选择 **Enable workflow**。停用巡检后，正式工作流仍可手动运行。

停止所有正式打卡及邮件：同时在 **Actions → SWU自动签到01 → Disable workflow** 停用。仅禁用正式工作流会令仍启用的巡检无法派发，因此完全停用时应禁用两个工作流。
