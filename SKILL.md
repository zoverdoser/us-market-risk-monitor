---
name: us-market-risk-monitor
description: 用于生成“美股风险监控 / T+1 基金执行参考”的可执行决策系统。Use when an AI agent or analyst needs to monitor US equity top/crash risk, judge whether the market is in a fragile high-zone, detect risk transmission into credit/rates/funding, assess breadth and leadership concentration, scan event overrides, and map conclusions to T+1 mutual fund or QDII subscription/redemption actions.
---

# 美股风险监控 / T+1 基金执行参考

## 使用场景

使用本 skill 回答这些问题：

- 当前美股是否处于高位脆弱区。
- 风险是否已经从情绪层传导到底层信用、利率、融资层。
- 指数上涨是否存在内部失真、集中度过高、领导权狭窄。
- 是否存在会迅速改变市场状态的重大事件。
- 当前市场风险应映射成什么级别：绿 / 黄 / 橙 / 红。
- 对 T+1 基金申购、定投、暂停、分批赎回和 QDII 风险管理意味着什么。

这不是泛泛市场评论，也不是 T+0 股票交易指令。必须从市场监控走到 T+1 基金执行建议，并明确净值滞后、申赎截止、赎回到账、汇率、溢价和流动性约束。

## 快速执行

优先运行脚本生成报告：

```bash
python3 scripts/monitor.py
```

如果用户只要求解释框架，可以直接按本文件的模块结构说明。若用户要求“今天 / 最新 / 当前”报告，必须获取实时或最新可得数据；不可凭记忆作答。

## 总体流程

并行评估 5 个监控模块，再进入两层映射：

- 模块 A：事件层。事件层是并行 override 模块，重大事件可直接提升整体风险灯号。
- 模块 B：情绪 / 脆弱度层。VX1!-VX2! 是一级核心指标和首要闸门。
- 模块 C：风险传导层。判断信用、利率、融资底层是否恶化。
- 模块 D：市场内部结构层。判断是否只剩少数大票撑指数。
- 模块 E：慢变量压力层。判断油价、利率、美元 / 金融条件是否持续压制市场赔率。
- 模块 F：市场风险映射层。将模块结论映射成绿 / 黄 / 橙 / 红，并把橙灯拆成软橙 / 硬橙。
- 模块 G：T+1 基金执行映射层。将市场风险灯转换成申购比例、定投处理、赎回 / 降暴露建议和 QDII 注意事项。

不要使用简单总分制。先形成每个模块的内部结论，再做跨模块确认。避免把同一风险的不同表现重复计分。

## 数据源与降级

核心原则：

- 核心项优先成功，增强项失败不阻塞主流程。
- unavailable 的指标必须明确写出。
- 使用代理指标时必须说明代理逻辑。
- 第一版优先自动化稳定、适合 daily 运行的数据源。

### B 情绪 / 脆弱度层

VX1!-VX2!：

- 级别：一级核心指标。
- 主源：本 skill 内置 VX 合约解析与 TradingView futures scanner 请求，自动识别 VX 近月 / 次月合约并计算 VX1!-VX2!。
- fallback 1：CBOE delayed quotes API：`_VIX`、`_VIX9D`、`_VIX3M`、`_VIX6M`。
- fallback 2：CBOE futures settlement CSV：`https://markets.cboe.com/us/futures/market_statistics/settlement/csv/?dt=YYYY-MM-DD`。
- 输出：单点值、曲线状态 / 压缩状态、风险解释、最近 14 日演化。
- 注意：不得依赖用户本地其它 skill 或脚本；VX1!-VX2! 获取能力必须能随本 skill 独立运行。

VX1!-VX2! 风险分级：

- `<= -2.0`：极度脆弱，禁止主动加高 beta。
- `-2.0 ~ -1.0`：警戒，弱信号减半。
- `-1.0 ~ 0`：正常。
- `0 ~ +1.5`：压缩警戒。
- `> 0`：避险状态；若明显 backwardation，应提高防守等级。

VIX 辅助项：

- 主源：CBOE delayed quotes API。
- 必看：VIX9D vs VIX、VIX vs VIX3M、VIX3M / VIX6M 是否仍压制短端。
- 解释：短端抬头代表风险从“脆弱”向“避险”切换。

### C 风险传导层

HY OAS：

- 级别：核心项。
- 主源：FRED `BAMLH0A0HYM2`。
- 主实现：优先使用 FRED official API，需要环境变量 `FRED_API_KEY`。API：`https://api.stlouisfed.org/fred/series/observations?series_id=BAMLH0A0HYM2&api_key=$FRED_API_KEY&file_type=json&observation_start=YYYY-MM-DD&observation_end=YYYY-MM-DD`。
- CSV 页面仅作为人工核对 / 备用参考：`https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2&cosd=YYYY-MM-DD&coed=YYYY-MM-DD`。
- 实现要求：不要把 `fredgraph.csv` 作为默认自动化路径；当前 agent 网络环境中该 endpoint 可能 0 字节超时或远端断开。若未配置 `FRED_API_KEY`，直接进入代理，不要等待 graph CSV 超时。
- 输出：最新值、5 日变化、20 日变化、60 日分位。
- fallback：若未配置 `FRED_API_KEY` 或 FRED API 请求失败，使用 HYG vs SHY 相对表现作为高收益信用压力代理，并标注真实原因，例如 `FRED_API_KEY not configured; using HYG vs SHY credit ETF proxy`。

IG OAS：

- 级别：核心项。
- 主源：FRED `BAMLC0A0CM`。
- 主实现：优先使用 FRED official API，需要环境变量 `FRED_API_KEY`。API：`https://api.stlouisfed.org/fred/series/observations?series_id=BAMLC0A0CM&api_key=$FRED_API_KEY&file_type=json&observation_start=YYYY-MM-DD&observation_end=YYYY-MM-DD`。
- CSV 页面仅作为人工核对 / 备用参考：`https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLC0A0CM&cosd=YYYY-MM-DD&coed=YYYY-MM-DD`。
- 实现要求：不要把 `fredgraph.csv` 作为默认自动化路径；若未配置 `FRED_API_KEY`，直接进入代理，不要等待 graph CSV 超时。
- 输出同 HY OAS。
- fallback：若 FRED CSV 超时，使用 LQD vs SHY 相对表现作为投资级信用压力代理，并标注代理逻辑。

MOVE / 利率波动代理：

- 级别：核心代理项。
- 主方法：若能稳定获得 MOVE exact，则使用 exact。
- fallback 必须实现：FRED `DGS2`、`DGS10`，计算 10 日 / 20 日收益率日变化波动率，并与近 3 个月均值比较。
- 若 MOVE exact 源未接入，输出数据源提示，说明使用 Treasury yield volatility proxy，且不作为错误处理。
- 若 FRED 利率数据超时，使用 Yahoo Finance 代理：`ZT=F` 作为 2Y Treasury futures proxy，`^TNX` 作为 10Y Treasury yield proxy。

SOFR-OIS / funding stress：

- 级别：高级确认项，不是硬依赖。
- exact 源未接入时输出数据源提示，说明使用 credit + rates + banks proxy，且不作为错误处理。
- 代理逻辑：HY OAS / IG OAS 是否同步走阔、2Y / 10Y 波动是否上升、银行板块是否显著弱于大盘、funding stress 新闻是否出现。

### D 市场内部结构层

必须回答：是不是只剩少数大票扛指数，是否存在拥挤交易，指数是否“表面强、内部窄”。

广度和集中度核心代理：

- SPY vs RSP：市值加权 vs 等权重标普，输出 5 / 20 / 60 日相对收益差。
- QQQ vs QQEW：纳指权重 vs 等权重纳指代理。若 QQEW 不稳定，使用 QQQ vs RSP 并结合半导体 / 小盘确认。
- IWM vs SPY：小盘 vs 大盘。
- 银行：XLF 或 KRE，相对 SPY。
- 半导体：SOXX 或 SMH，相对 QQQ。
- 运输：IYT，相对 SPY。

增强项：

- A/D line、200 日线上股票占比、新高 / 新低家数。
- 若取不到，输出数据源提示，说明使用 ETF breadth proxies，且不作为错误处理。

归因规则：

- SPY 强于 RSP、QQQ 强于等权重代理、小盘掉队，归因于集中度 / 领导权狭窄。
- 银行掉队偏信用 / 金融环境确认；半导体掉队偏成长风险偏好确认；运输掉队偏经济敏感确认。
- 不要把这些与 VIX 情绪重复计分。

### E 慢变量压力层

油价：

- 级别：核心项。
- 主方法：优先使用可用的 stock-sdk MCP quotes：Brent、WTI。
- fallback：Google News RSS 中 oil / Iran / Brent / WTI headline。
- 解释：油价持续上升会压制通胀赔率、利率路径和风险偏好。

利率：

- 级别：核心项。
- 主源：FRED `DGS2`、`DGS10`，可选 `DFII10`。
- 输出：最新值、5 日变化、20 日变化、是否处于近期高位。
- 实现要求：优先使用 FRED official API + `FRED_API_KEY`，只拉近 180-240 天观察窗口；不要把 `fredgraph.csv` 作为默认自动化路径。
- fallback：FRED 超时则用 Yahoo Finance 代理：`ZT=F`（2Y Treasury futures proxy）、`^TNX`（10Y Treasury yield proxy）、`TIP`（TIPS ETF real-rate proxy）。

美元 / 金融条件：

- 级别：增强项。
- 第一版不是硬依赖。若使用美元指数或金融条件代理，必须标注代理来源。

### A 事件层

事件层拆成两部分：

- A1 慢变量压力：油价、利率、美元 / 金融条件、通胀 / 真实利率。该部分也在模块 E 中结构化输出。
- A2 离散事件触发器：地缘冲突、政策变化、龙头财报、信用事故、监管 / 司法冲击、爆仓 / 违约 / 黑天鹅。

主方法使用 Google News RSS，不依赖新闻正文页。RSS 结构稳定、有时间戳，适合 daily 自动化。

推荐查询模板需按当天日期替换：

- `Iran oil YYYY-MM-DD markets`
- `China stocks YYYY-MM-DD A-share`
- `Japan stocks YYYY-MM-DD`
- `Nasdaq YYYY-MM-DD stocks`
- `healthcare stocks YYYY-MM-DD`
- `credit default liquidity YYYY-MM-DD markets`

输出字段：

- 时间。
- 来源。
- 标题。
- 事件类型：地缘 / 政策 / 财报 / 信用事故 / 监管 / 市场事件。
- 是否构成 override。

override 规则：

- 明确战争升级、违约、爆仓、系统性流动性冲击、重大监管 / 司法冲击，可直接提升为橙或红。
- 中等级别事件风险至少提升到黄，等待其他模块确认。

## 市场风险映射

绿灯：

- 特征：情绪层未失真，传导层稳定，结构层未明显恶化，事件层无重大风险，慢变量压力可控。
- 动作：允许正常配置；不追高但不需要额外防守；观望标的按正常标准评估；新增仓位不需要额外折扣。

黄灯：

- 特征：任一核心模块出现脆弱信号但未形成跨模块确认；或慢变量压力明显上升；或出现中等级别事件风险。
- 动作：保留更多现金；新信号按正常仓位的 25%-50% 试错；不满仓追高 beta；不主动追纳指 / 日股的拥挤方向；观望标的提高赔率、回撤和流动性要求。

橙灯：

- 特征：两个独立模块同时恶化；或事件层已明显冲击风险偏好；或慢变量压力与结构恶化叠加。
- 动作：停止高 beta / 高相关主题加仓；防守资产优先；检查沉淀层是否需要对冲思维；暂停高 beta / 高相关观望标的纳入；保留低相关、高确定性或防守型标的观察；新仓只允许极少数高把握、低相关方向，并使用显著低于正常的试错仓位。
- 软橙：情绪层 + 结构层恶化，但信用、利率、事件层未确认。代表高位脆弱，不直接等同于系统性风险传导。
- 硬橙：信用 / 利率传导恶化、事件 override、单一核心模块红色、或慢变量压力与结构恶化共振。代表风险已从表层脆弱进入更可执行的防守状态。

红灯：

- 特征：重大事件触发；风险传导层明确恶化；情绪层从脆弱切换到避险；或多个模块同时红。
- 动作：进入防守模式；新仓暂停；只允许极少量逆向仓位；更关注流动性、赎回节奏、可逆性、QDII 的退出代价。

## T+1 基金执行映射

本层不是 T+0 股票交易建议。输出必须考虑基金申赎截止、T+1 净值确认、QDII 净值滞后、汇率、溢价、赎回到账和流动性。

绿灯：

- 新增 / 申购：正常申购 / 正常定投，计划资金可按 100% 执行。
- 已有仓位：不需要因风险灯号赎回。
- QDII：正常评估，但仍检查溢价、额度和汇率。

黄灯：

- 新增 / 申购：降至正常计划的 50%-75%，高 beta 主题基金偏下限。
- 定投：宽基定投可继续，主题定投减半或暂停一期。
- 已有仓位：不因单一黄灯主动赎回，先检查集中度和流动性。

软橙：

- 新增 / 申购：宽基新增降至正常计划的 25%-50%；暂停高 beta / 单一主题基金新增。
- 定投：宽基定投可小额继续，主题定投暂停一期等待确认。
- 已有仓位：不建议因单日软橙立即赎回；连续 2 个交易日软橙再考虑降低主题暴露。
- 升级触发：若信用、利率或事件层确认恶化，软橙升硬橙。

硬橙：

- 新增 / 申购：暂停权益基金新增；只保留现金、短债、低波或极低相关方向。
- 定投：权益宽基定投暂停一期，高 beta 主题定投暂停。
- 已有仓位：高 beta / QDII / 单一主题基金可考虑分批降低暴露，避免一次性砍仓。

红灯：

- 新增 / 申购：停止新增权益基金申购，优先保留现金和低波动资产。
- 定投：暂停权益类定投。
- 已有仓位：优先分批处理高波动、流动性差、溢价高、赎回慢的品种。

## 输出模板

固定输出以下结构：

```markdown
# 美股风险监控 / T+1 基金执行参考

## 1. 总体结论
- 当前市场风险灯：绿 / 黄 / 橙 / 红
- 当前基金执行灯：绿 / 黄 / 软橙 / 硬橙 / 红
- 一句话解释

## 2. 情绪 / 脆弱度层
- VX1!-VX2!
- VIX 辅助项
- 本层结论

## 3. 风险传导层
- HY OAS
- IG OAS
- MOVE / 利率波动代理
- SOFR-OIS（若可用）
- 本层结论

## 4. 市场内部结构层
- SPY vs RSP
- QQQ vs 等权重纳指
- IWM vs SPY
- XLF / SOXX / IYT
- 集中度 / 领导权狭窄度判断
- 本层结论

## 5. 慢变量压力层
- 油价
- 利率
- 美元 / 金融条件（若有）
- 本层结论

## 6. 事件层
- 最近 24 小时关键事件
- 事件类型
- 是否构成 override
- 本层结论

## 7. 市场风险动作映射
- 当前应该做什么
- 当前不该做什么
- 对高 beta / 观望标的 / 沉淀层分别意味着什么

## 8. T+1 基金执行建议
- 新增/申购
- 定投
- 赎回/降低暴露
- QDII
- 明日观察触发器
```

## 注意事项

- VX1!-VX2! 是首要闸门，但不能单独替代所有模块。
- 事件层并行运行，不假设风险按固定顺序发生。
- SOFR-OIS 是高级确认项，不可用时不要阻塞主流程。
- 内部结构层必须覆盖集中度 / 领导权狭窄度。
- 不要用简单总分制；用模块结论和跨模块确认。
- 橙灯必须区分软橙 / 硬橙；情绪 + 结构恶化但信用 / 利率 / 事件未确认时，不应直接给硬橙基金动作。
- 基金执行层必须区分新增资金、已有仓位、定投和 QDII，不要把市场风险灯直接翻译成 T+0 股票交易动作。
- 对 every unavailable metric 明确标注，并说明 fallback 或代理逻辑。
- 若脚本因网络受限失败，仍应用已成功模块生成降级报告，并清楚标注缺口。
