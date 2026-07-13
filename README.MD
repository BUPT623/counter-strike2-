# CS-APP

CS-APP 是一个本地运行的 CS2 饰品跨平台价差分析项目。

项目包含两套独立策略：

- **小额搬砖监控**：面向 Skinport 低价饰品与国内 UU/CSQAQ 价差，带风险模型、推荐池、7 天回测和 Bark 推送。
- **大额捡漏监控**：面向 Skinport `2300-12000 HKD` 与 UU/CSQAQ `2000-10000 RMB` 的高价饰品，只按捡漏利润排序并推送 Top 5。

本项目仅做数据分析和提醒，不构成交易建议。

## 功能

- 抓取 Skinport 饰品价格，Skinport 查询使用 `currency=HKD`
- 抓取 CSQAQ 聚合的国内 UU/BUFF 等价格字段
- 程序启动时获取一次 HKD -> RMB 实时汇率
- 使用 `market_hash_name` 做跨平台匹配
- 小额策略过滤高价与特殊品类，适合稳定搬砖观察
- 大额策略保留带 `★` 饰品，适合寻找刀、手套等高价捡漏机会
- 本地 FastAPI 看板展示推荐池和 7 天回测库
- Bark 手机推送推荐摘要
- Windows 计划任务定时运行
- 默认输出 JSON 报表，不再默认生成 Excel

## 项目结构

```text
config/
  automation_config.json       # 小额自动化与推送配置
  large_deal_config.json       # 大额捡漏阈值与评分配置
  risk_config.json             # 小额风险模型配置

scripts/
  run_automation_once.ps1      # 小额计划任务入口
  run_large_deal_once.ps1      # 大额计划任务入口

src/
  skinport_client.py           # 小额 Skinport 采集
  csqaq_client.py              # 小额 CSQAQ/UU 采集
  analyzer.py                  # 小额利润、风险和回测分析
  web_app.py                   # FastAPI 本地看板
  automation_runner.py         # 小额自动化入口

  large_deal_skinport_client.py
  large_deal_csqaq_client.py
  large_deal_analyzer.py
  large_deal_notifier.py
  large_deal_runner.py         # 大额捡漏自动化入口

tests/
  test_*.py                    # 核心模型与自动化测试
```

## 环境要求

- Windows 10/11
- Python 3.13
- 依赖见 `requirements.txt`

安装依赖：

```powershell
E:\PythonEnvs\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 本地配置

复制 `.env.example` 为 `.env`，填入本机 Bark 配置：

```env
CS_APP_NOTIFY_ENABLED=1
CS_APP_EXPORT_EXCEL=0
CS_APP_PUSH_PROVIDER=bark
CS_APP_BARK_BASE_URL=https://api.day.app
CS_APP_BARK_KEY=你的BarkKey
```

项目根目录还需要放置 CSQAQ token 并填写本地ip白名单(注册CSQAQ后可点开用户界面查看)：

```text
.csqaq_token
```

`.env` 和 `.csqaq_token` 含密钥，已被 `.gitignore` 忽略，不能上传到 GitHub。

## 小额搬砖策略

小额策略的自动流程：

```text
Skinport 小额采集
-> CSQAQ/UU 小额采集
-> 实时汇率折算
-> 利润计算
-> 风险模型评分
-> 7 天回测库更新
-> output/profit_report.json
-> Bark 推送
```

小额利润模型保留两种模式：

```text
挂单出售：
预期出货价 = UU求购价 + 动态成交系数 * (UU售价 - UU求购价)

即时出售：
预期出货价 = UU求购价
```

小额推荐池当前规则：

- 稳定搬砖：全部进入推荐池
- 异常上涨：利润率大于 `10%` 进入推荐池
- 同一饰品按 `market_hash_name` 去重

运行一次：

```powershell
E:\PythonEnvs\.venv\Scripts\python.exe src\automation_runner.py --once
```

干跑一次，不请求平台、不推送：

```powershell
E:\PythonEnvs\.venv\Scripts\python.exe src\automation_runner.py --dry-run --once --ignore-window
```

## 大额捡漏策略

大额策略独立于小额策略，不做风险模型，不写历史回测。

筛选条件：

- Skinport 售价：`2300-12000 HKD`
- CSQAQ/UU 售价：`2000-10000 RMB`
- UU 在售数量：`> 50`
- 保留带 `★` 的饰品

大额利润模型只有一种：

```text
预期出货价 = UU售价
捡漏利润 = UU售价 - Skinport折算成本
捡漏利润率 = 捡漏利润 / Skinport折算成本
```

每轮只推送推荐评分前 5 的大额候选。

运行一次：

```powershell
E:\PythonEnvs\.venv\Scripts\python.exe src\large_deal_runner.py --once
```

干跑一次，不请求平台、不推送：

```powershell
E:\PythonEnvs\.venv\Scripts\python.exe src\large_deal_runner.py --dry-run --once --ignore-window
```

## Windows 定时任务

当前设计：

- 小额搬砖：`09:00 / 11:00 / 13:00 / 15:00 / 17:00 / 19:00 / 21:00 / 23:00`
- 大额捡漏：`09:30 / 11:30 / 13:30 / 15:30 / 17:30 / 19:30 / 21:30 / 23:30`

两套任务错开半小时，避免同时请求平台。

计划任务调用：

```text
scripts/run_automation_once.ps1
scripts/run_large_deal_once.ps1
```

## 数据与输出

运行时会生成：

```text
data/
output/
logs/
```

这些目录已被 `.gitignore` 忽略，不建议上传到 GitHub。

主要输出：

```text
output/profit_report.json       # 小额推荐与分析结果
output/large_deal_report.json   # 大额捡漏 Top 候选
```

## 测试

```powershell
E:\PythonEnvs\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 平台访问说明

项目遵守低频、间隔式采集原则：

- 国内接口循环请求使用随机 `3-7` 秒延迟
- 小额和大额定时任务错峰运行
- 失败后不会高频重试
- 不包含绕过平台限制的逻辑

请根据平台规则和自身账号情况谨慎使用。个人使用禁止用作商业用途~
