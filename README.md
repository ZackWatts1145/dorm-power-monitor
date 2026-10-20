# 宁夏理工学院 · 宿舍电费微信提醒

针对宁夏理工学院「缴费平台」小程序（`jxpt.nxist.com`）的宿舍电费自动查询：**余额低于阈值时通过微信提醒你**。

只需要填一个房间号和一个推送 key。不用装 App，不用小程序一直开着，也不用自己维护服务器（用 GitHub Actions 的话）。

- 学校电费单价 0.5 元/度，所以 `5 度 ≈ 2.5 元`
- 只依赖 Python 标准库，不需要 `pip install` 任何东西
- 自动按房间号解析电表，不用知道 roomId / meterId
- 自带冷却、充值后重置、掉线告警、用量趋势
- 每人跑自己的一份，只查自己的房间

> 本项目与学校官方无关，只做只读查询。使用前请阅读[免责声明](#免责声明)。

## 效果

余额不足时，微信收到这样一条：

```
电费余额不足 · 11201
──────────────
房间: 11201
剩余电量: 4.80 度
剩余金额: ¥2.40
日均消耗: 3.20 度/天
预计可用: 1.5 天
触发原因: 电量 4.80 ≤ 5 度
查询时间: 2026-01-01 08:30:12

请及时充值，避免断电。
```

## 房间号怎么找

打开缴费平台小程序：

- 首页「我的房间」区块能看到已绑定的房间号
- 或者点「查找房间」，输入楼栋/房间关键词，列表里显示的就是房间号

房间号形如 `11201`、`N5A101` 这种，**按小程序里显示的原样填**，不要自己加楼栋名。

## 快速开始

### 方式一：GitHub Actions（推荐，手机就能配完）

不用买服务器，GitHub 免费额度足够（每 30 分钟跑一次，一次几秒钟）。

1. 点右上角 **Fork**，把仓库复制到你自己的账号下
2. 去你的 fork：**Settings → Secrets and variables → Actions → New repository secret**
   添加两个 Secret：

   | Name | Secret |
   |---|---|
   | `ROOM_CODE` | 你的房间号，例如 `11201` |
   | `SENDKEY` | 你的 Server酱 SendKey（见下方[怎么拿 key](#微信推送怎么拿-key)） |

3. 打开 fork 的 **Actions** 标签页，如果提示 workflow 未启用，点 **I understand my workflows, go ahead and enable them**
4. 左侧选 **power-monitor** → 右侧 **Run workflow** → 跑一次
5. 看运行日志，出现 `notify serverchan -> 0 SUCCESS` 就说明推送成功，微信应该已经收到

之后每 30 分钟自动跑一次。想改阈值的话，在 **Settings → Secrets and variables → Actions → Variables** 里加一个 `THRESHOLD_KWH`（不配默认 5 度）。

> GitHub 的定时任务不保证准点，通常延迟几分钟到十几分钟，对电费提醒来说完全够用。
>
> 仓库 60 天没有任何提交时，GitHub 会自动停用定时 workflow，需要回 Actions 页面重新启用。

### 方式二：自己的电脑 / 服务器

```bash
git clone https://github.com/ZackWatts1145/nxist-power-monitor.git
cd nxist-power-monitor

cp config.example.json config.json
# 编辑 config.json，填 room.code 和 notify.serverchan_key

python monitor.py --test     # 先验证能不能推到微信
python monitor.py            # 正常巡检一次
```

跑通后挂到定时任务：

- **Windows**：任务计划程序，每 30 分钟执行 `python monitor.py`
- **Linux**：`crontab -e` 加一行 `*/30 * * * * cd /path/to/repo && /usr/bin/python3 monitor.py`
- **macOS**：`launchd` 或 `cron`

也可以跑在 Docker、树莓派上。

## 配置说明

```jsonc
{
  "base_url": "https://jxpt.nxist.com",   // 学校缴费平台地址，一般不用改
  "room": {
    "code": "11201"                       // 必填：你的房间号
  },
  "threshold": {
    "energy_kwh": 5,                      // 剩余电量 ≤ 这个数就提醒（度）
    "money_yuan": 0                       // 剩余金额 ≤ 这个数也提醒，0 表示关闭
  },
  "notify": {
    "channel": "auto",                    // auto / serverchan / pushplus / wecom / toast / console
    "serverchan_key": "",
    "pushplus_token": "",
    "wecom_webhook": "",
    "toast_fallback": true                // 推送失败时弹本机通知（仅 Windows）
  },
  "alert": {
    "cooldown_hours": 12,                 // 同一次低电量多久内不重复提醒
    "heartbeat_failures": 3               // 连续查不到几次后提醒「监控掉线」
  }
}
```

`channel` 设为 `auto` 时，配了哪个 key 就用哪个，都没配就退化成本机通知。

阈值建议：学校 0.5 元/度，普通宿舍一天 2–4 度。

- `5 度`：提前一天多提醒，够你充值，推荐
- `10 度`：提前两三天，更保险
- `1 度`：基本是断电前才响，不建议

## 微信推送怎么拿 key

### Server酱 Turbo（推荐，能进微信）

1. 微信打开 <https://sct.ftqq.com>，扫码登录并绑定微信
2. 进「SendKey」页面，复制 `SCT` 开头的那串
3. 填到 `SENDKEY`（Actions）或 `notify.serverchan_key`（本地配置）

⚠️ 注意别拿错产品：**Server酱³（sc3.ft07.com）推送的是它自己的 App，不进微信**。两个产品的 SendKey 不通用。本项目两种格式都能识别，但只有 Turbo 的 key 会推到微信。

### PushPlus（替代方案，免费额度更大）

1. 微信打开 <https://www.pushplus.plus> 扫码
2. 复制「一对一推送」的 token
3. 填到 `notify.pushplus_token`

### 企业微信群机器人

在企业微信群里添加「群机器人」，复制 Webhook 地址，填到 `notify.wecom_webhook`。消息会到企业微信，不在微信里。

## 命令

```bash
python monitor.py            # 巡检一次（定时任务调这个）
python monitor.py --test     # 发一条测试消息，不受阈值限制
python monitor.py --json     # 打印读数、房间信息和用量趋势
python monitor.py --resolve  # 强制重新按房间号解析电表
python monitor.py --no-notify  # 只查不推

python test_monitor.py       # 自测，不需要真实 key
```

退出码：`0` 正常、`1` 触发告警、`2` 查询失败。

## 常见问题

**提示「没找到房间」**

房间号要和缴费平台里显示的一致。注意有些楼栋的编号带字母前缀（如 `N5A101`），有些是纯数字（如 `11201`），别自己拆分或补零。

**换了宿舍怎么办**

改 `config.json` 的 `room.code`（或 Actions 里的 `ROOM_CODE`），程序检测到房间号变了会自动重新解析。

**返回「设备离线」或「设备查询超时」**

`code=5000` 是电表离线（宿舍停电、网关故障），`reportStatus=1` 是查询超时。这两种会记为查询失败，连续 3 次会推「监控掉线」，等电表恢复后自动继续。

**收不到微信消息**

先看日志最后一行：

- `notify serverchan -> 0 SUCCESS`：接口已接受，检查是否用的 Turbo 的 key、是否在 sct.ftqq.com 绑定了微信
- `notify serverchan -> 903` / 其他非 0：key 不对或已失效
- `HTTP Error 403`：请求被 Cloudflare 拦了，确认用的是本项目自带的请求头（已内置浏览器 UA）
- 没有 notify 日志：余额没低于阈值，属正常

**平台维护期间怎么办**

缴费平台偶尔会维护，这时接口会超时或返回非 JSON，程序记为失败但不会崩，恢复后自动继续。

## 隐私与安全

- 本项目**不收集任何数据**，没有中心服务器，所有请求从你自己的机器或你的 GitHub Actions 发出
- `config.json`、`state.json`、`history.csv`、`logs/` 都在 `.gitignore` 里，不会被提交
- **SendKey 等同于密码**，泄露后别人可以往你微信发消息。用 Actions 时请放在 Secrets 里，不要写进代码
- 本仓库不含任何人的房间号、手机号或 key

## 目录结构

```
monitor.py              # 主程序
bootstrap.py            # 从环境变量生成 config.json（CI 用）
test_monitor.py         # 自测
config.example.json     # 配置模板
toast.ps1               # 可选的 Windows 桌面通知
.github/workflows/      # GitHub Actions 定时任务
docs/how-it-works.md    # 接口和实现原理
```

## 免责声明

- 本项目与宁夏理工学院官方无关，不是官方工具。
- 请只查询**自己**的房间，不要提高请求频率、不要批量采集或对外提供查询服务。
- 接口随时可能变更、增加鉴权或停止服务，本项目不保证可用性。
- 因使用本项目造成的任何后果（包括但不限于接口被封、数据错误、欠费断电）由使用者自行承担。

## License

[MIT](LICENSE)
