# Extremity Z + S（双轴极端度 v5.1）

Extremity 判断价格是否处在"跨波动率周期"的极端位置。它用两根轴描述当根状态：位置 `Z` 和 5 日速度 `S`。轨道是因果滚动 2 年窗口的上下行半标准差，所以不同标的、不同波动率时期可以用同一套 `σ` 倍数比较。

- Python 实现：`src/indicators/extremity/__init__.py`（纯 numpy，无 I/O，无 pandas）
- TradingView 实现：同目录 `extremity_v5_1.pine`（Pine v6）
- 两端逐根一致：测试用独立的纯 `math` 标量参考逐行翻译 `.pine`，与 Python 输出对拍（浮点容差 `1e-12`，布尔列逐位相等）。

## 1. 定位与分工

Extremity 用来平替 `奶牛 D`，与 `PMARP(30, 120)` 互补。

| 指标 | 管什么 | 不管什么 |
|------|--------|----------|
| `PMARP(30, 120)` | 半年内牛市常规波段回踩 | 跨波动率周期的极值 |
| `Extremity` | 恐慌极值 `-2σ`；右侧防抖上穿反转；顶部防抖下穿收割；急杀回踩（假极端）识别 | 半年内的常规小回踩 |

## 2. 公式与动态轨道

```
OHLC ──ln──> x=ln C, o, h, l
   │
   ├─> 稳尺子 σ：25 根半衰期 EWMA
   │     equity: Yang-Zhang（含隔夜跳空）
   │     crypto: Garman-Klass（24h 连续）
   │
   ├─> 位置 Z = (x − EWMA84(x)) / (c_n · σ[t−5])
   ├─> 速度 S = (x − x[t−5])    / (σ[t−5] · √5)
   │
   └─> 动态轨道（第 t 根只读 t 之前已入队的 Z/S）
         mean_z、std_up、std_dn、std_s
         ──> 6 类盘面 + 防抖状态机 6 个报警
         ──> 判定完毕后才把 clamp(Z,±6)、clamp(S,±6) 入队
```

### 2.1 稳尺子 σ

- 半衰期 `half_life = 25` 根，`alpha = 1 − 0.5^(1/half_life)`。
- 所有 EWMA 用首个有效值做种子，递推式为 `acc + alpha·(v − acc)`。`NaN` 不更新状态。
- equity 用 Yang-Zhang：`var = var_o + k_yz·var_c + (1 − k_yz)·var_rs`。
  - `r_o = o − x[t−1]`（隔夜跳空），`r_c = x − o`（日内）。
  - `var_rs` 是 Rogers-Satchell 项 `max(0, (h−x)(h−o) + (l−x)(l−o))` 的 EWMA。
  - `k_yz = 0.34 / (1.34 + (n_eff+1)/max(n_eff−1, 1))`，`n_eff = 2/alpha − 1`。
  - 第 0 根没有前收盘，`σ[0] = NaN`。
- crypto 用 Garman-Klass：`max(0, 0.5·(h−l)² − (2ln2 − 1)·(x−o)²)` 的 EWMA，第 0 根起有值。
- `σ = sqrt(max(var, 1e-12))`。Z 和 S 用 5 根前的 `σ[t−5]`，当根暴跌不会同时放大分母。

### 2.2 两根轴

- 位置 `Z`：对数价相对 EMA84 锚点的偏离。`c_n = (ema_len − 1) / (2·√ema_len)` 把偏离折算到 `σ` 的量纲。
- 速度 `S`：5 日对数收益除以 `σ[t−5]·√5`。
- EMA 锚点是首根收盘做种子的显式 EWMA（`alpha = 2/(ema_len+1)`），不用 TradingView `ta.ema` 的 SMA 种子（Pine 文件顶部 D1 注释）。
- 前 `ema_len + speed_len = 89` 根是预热段（`warm = False`），Z、S 和全部轨道输出为 `NaN`。

### 2.3 动态轨道

- 窗口长度：equity `504` 根，crypto `730` 根（日线约 2 年）。
- 窗口内的 Z/S 先钳位到 `±6` 再入队，单根极端值不会撑爆轨道。
- 窗口不足 `prior_weight = 200` 根时，补 `eff_pri = max(0, 200 − w_len)` 根"单位方差、零均值"的先验，新股和短历史也有可用轨道。
- `mean_z = Σz / max(1, w_len + eff_pri)`。
- 上下行半标准差：`dz = z − mean_z`，`dz ≥ 0` 归上侧，否则归下侧。
  - `std_up = sqrt((ss_up + eff_pri/2) / max(1, n_up + eff_pri/2))`，`std_dn` 同理。
  - `std_s = sqrt((Σs² + eff_pri) / max(1, w_len + eff_pri))`。
- 轨道：`z_up_warn / z_up_ext = mean_z + {1.5, 2.0}·std_up`；`z_dn_warn / z_dn_ext = mean_z − {1.5, 2.0}·std_dn`；`s_ext = 2.0·std_s`，`s_dip = 1.5·std_s`。

### 2.4 停牌保护 `STD_EPS = 1e-9`

`std_up`、`std_dn`、`std_s` 任一 `≤ 1e-9`（停牌、长期恒价）时，当根 `warm = True` 但 `live = False`：

- `u_dn`、`u_up` 为 `NaN`；
- 6 类盘面和 6 个报警全部为 `False`；
- 轨道与 Z/S 照常输出，滚动窗口照常入队。复牌后窗口里出现非零值，`live` 自动恢复。

### 2.5 防锯齿状态机

- `u_dn = (mean_z − z) / std_dn`：低于中枢多少个下行 σ（正数越大越超跌）。
- `u_up = (z − mean_z) / std_up`：高于中枢多少个上行 σ。
- 进线：未武装且 `u ≥ k`。退线：已武装且 `u ≤ k − 0.25`。`u` 为 `NaN` 时不触发，保持原状态。
- `0.25σ` 缓冲让价格在轨道附近来回抖动时只报一次。

### 2.6 六类盘面（仅 `live` 时为真）

| 字段 | 条件 | 含义 |
|------|------|------|
| `panic_bottom` | `z ≤ z_dn_ext` 且 `s ≤ −s_ext` | 恐慌超跌，禁追空 |
| `deep_oversold` | `z ≤ z_dn_ext` 且 `s > −s_ext` | 深度超跌，已减速 |
| `warn_oversold` | `z_dn_ext < z ≤ z_dn_warn` | 超跌警戒 |
| `climax_top` | `z ≥ z_up_ext`，或 `z ≥ z_up_warn` 且 `s ≥ s_ext` | 喷发过热，止盈警戒 |
| `slow_grind` | `z ≥ z_up_warn` 且非 `climax_top` | 慢牛钝化，禁逆势 |
| `fake_dip` | `z > mean_z − 1.2·std_dn` 且 `s ≤ −s_dip` | 急杀回踩：速度极端但位置不极端 |

## 3. 参数与输出

### 3.1 参数

| Python 参数 | Pine 输入 | 默认 | 说明 |
|-------------|-----------|------|------|
| `market` | `volMode`（Auto 按 `syminfo.type`） | 必填 | `"equity"` 用 Yang-Zhang，`"crypto"` 用 Garman-Klass |
| `rail_bars` | `railYears = 2.0` | `None` → `RAIL_BARS[market]` | 轨道窗口根数；equity 504，crypto 730 |
| `ema_len` | `emaLen` | `84` | 位置锚点 EWMA 周期，`≥ 2` |
| `speed_len` | `speedLen` | `5` | 速度窗口与 σ 滞后根数，`≥ 1` |
| `half_life` | `halfLife` | `25.0` | 稳尺子半衰期（根） |
| `prior_weight` | `priorWeight` | `200.0` | 短历史先验根数，`≥ 0` |
| `k_warn` | `kWarn` | `1.5` | 警戒倍数 |
| `k_ext` | `kExt` | `2.0` | 极值倍数，须 `> k_warn` |
| `k_dip` | `kDip` | `1.5` | 急杀回踩速度倍数 |
| `hyst_buf` | `hystBuf` | `0.25` | 防抖缓冲，须 `0 ≤ hyst_buf < k_warn` |

Python 与 Pine 的差异：

- Python 必须显式传 `market`。Pine 默认按 `syminfo.type` 自动选算法，也可手动指定。
- Pine 按周期自动折算 `railYears`（上限 2000 根、下限 30 根）。Python 直接收 `rail_bars`，不做周期折算。
- Pine 输入框的 `minval / maxval` 比 Python 校验更严。Python 只拒绝数学上无意义的值。
- 非法输入一律抛 `ValueError`：OHLC 非一维、长度不等、为空、含非有限值、含非正价格、`high < low`；参数传 `bool`、类型错误或越界；`half_life` 过大导致 `alpha` 下溢为 0。`open/close` 不要求落在 `[low, high]` 内。

### 3.2 输出（29 个等长 1D 数组）

float64（15 个）：

| 字段 | 说明 | 何时有值 |
|------|------|----------|
| `sigma` | 稳尺子 σ（当根，未滞后） | equity 第 1 根起，crypto 第 0 根起 |
| `z`, `s` | 位置 Z、速度 S（未钳位） | `warm` |
| `mean_z` | 窗口中枢 | `warm` |
| `std_up`, `std_dn`, `std_s` | 上行 / 下行 / 速度半标准差 | `warm` |
| `z_up_warn`, `z_up_ext`, `z_dn_warn`, `z_dn_ext` | 四条位置轨道 | `warm` |
| `s_ext`, `s_dip` | 速度极值线、急杀回踩线（正数） | `warm` |
| `u_dn`, `u_up` | 下行 / 上行标准化偏离倍数 | `live` |

bool（14 个）：

| 字段 | 说明 |
|------|------|
| `warm` | 预热结束且 Z/S 有限 |
| `live` | `warm` 且三条半标准差都 `> STD_EPS` |
| `panic_bottom`, `deep_oversold`, `warn_oversold`, `climax_top`, `slow_grind`, `fake_dip` | 六类盘面，见 2.6 |
| `enter_dn_warn`, `exit_dn_warn` | 下穿 −1.5σ 进线 / 防抖上穿 −1.5σ 退线 |
| `enter_dn_ext`, `exit_dn_ext` | 下穿 −2.0σ 进线 / 防抖上穿 −2.0σ 退线 |
| `exit_up_ext`, `exit_up_warn` | 防抖下穿 +2.0σ / +1.5σ 退线 |

## 4. TradingView 安装与警报

### 4.1 安装

1. 打开 TradingView 的 Pine Editor。
2. 粘贴同目录 `extremity_v5_1.pine` 的全部内容。
3. 点"添加到图表"。指标显示在副图。

### 4.2 内置警报

| `alertcondition` 名称 | 对应 Python 字段 | 用途 |
|------------------------|------------------|------|
| 下穿 -1.5σ (进超跌警戒) | `enter_dn_warn` | 开始关注，左侧观察 |
| 下穿 -2.0σ (进恐慌极值) | `enter_dn_ext` | 进入恐慌极值区，准备分批 |
| 防抖上穿 -2.0σ (极值止跌) | `exit_dn_ext` | 极值区止跌，右侧第一确认 |
| 防抖上穿 -1.5σ (超跌反转) | `exit_dn_warn` | 脱离超跌区，右侧反转确认 |
| 防抖下穿 +2.0σ (过热退潮) | `exit_up_ext` | 喷发退潮，收割第一批 |
| 防抖下穿 +1.5σ (强势衰减) | `exit_up_warn` | 强势衰减，收割剩余 |

### 4.3 创建警报时必须改 `Trigger`

创建警报时，把 `Trigger` 从默认的 `Once only` 改成 `Once Per Bar Close`。原因有两条：

- `Once only` 触发一次后警报就停止，后续所有信号都收不到。
- 盘中实时 K 线上 Z/S 和状态机会随价格反复变化。`Once Per Bar Close` 只在收盘确认后触发一次，与 Python 用日收盘计算的结果一致，不会被盘中假信号骚扰。

### 4.4 黄色 `Caution` 提示可以忽略

右上角面板用 `barstate.islast` 只在最后一根 K 线绘制。TradingView 检测到这一用法时会显示黄色 `Caution` 提示（脚本在实时 K 线上的表现可能与历史不同）。面板只做展示，不参与 Z/S、轨道、盘面或警报计算，这条提示可以安全忽略。

## 5. Python 调用示例

```python
from src.indicators.extremity import compute_extremity, RAIL_BARS

# opens/highs/lows/closes：按时间升序的日线序列（list、numpy 数组或 pandas Series 均可）
out = compute_extremity(opens, highs, lows, closes, market="equity")

last = -1
if out["live"][last]:
    print("u_dn =", out["u_dn"][last], "u_up =", out["u_up"][last])
if out["exit_dn_ext"][last]:
    print("防抖上穿 -2.0σ：极值止跌")

# 加密：730 根窗口 + Garman-Klass
btc = compute_extremity(btc_o, btc_h, btc_l, btc_c, market="crypto")
```

`from src.indicators import compute_extremity` 也可用。防抖状态机可单独调用：`hysteresis_signals(u, k_enter, hyst_buf)` 返回 `(enter, exit)` 两个布尔数组。

## 6. 典型触发频率（参考量级）

| 标的类型 | `-1.5σ` 进线 | `-2.0σ` 进线 |
|----------|--------------|--------------|
| 美股指数 / 大科技 | 约 2～3 轮/年 | 约 1.5～2.5 轮/年 |
| BTC | 约 3～3.5 轮/年 | — |

- 牛市年份基本静默，熊市年份集中触发。
- 来源：研发期回测（2021-11 ～ 2026-11，SPY / QQQ / SOXX / BTC / ETH 与 904 只美股大盘股）。这些数字只用于判断量级，本仓测试不复现。

## 7. 验证边界

- 测试覆盖：输入校验、短序列、停牌恒价与复牌恢复、亚 `STD_EPS` 微跳、因果前缀不变性、末根改价不影响当根轨道、防抖边界、与 Pine 标量参考逐根对拍（equity / crypto、`rail_bars=120`、`prior_weight=0, rail_bars=30`、停牌后复牌）。
- 未在 TradingView 真实 Pine VM 上跑过 `.pine`。对拍对象是逐行翻译 `.pine` 的 Python 标量参考，不是 TradingView 的实际输出。
- 代码兼容 Python 3.10（云端 3.10.12 + numpy 2.2.6）。
