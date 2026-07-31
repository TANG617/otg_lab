# A05 — Stop-and-go：P baseline 对比 PV/PVA

## 结论

在 E07 operational P-only baseline 明确进入 pulse region 的所有坐标上，
五种 PV 和五种 PVA 方法都把 rest-to-rest pulse fraction 与 stop-and-go
event rate 降为 0：**true**。因此加入 velocity target
对 stop-and-go 的改善得到完整 20-input × 4-limit-scale 矩阵支持。

但成熟窗口是严格 constant velocity，真实和差分 acceleration 都为 0。matched
PV/PVA 在 80 个坐标的四个 primary stop-go 指标上逐 stencil 等价到
`1e-12`：**true**。secondary tracking/profile
指标保留浮点差分残差，最大差异单独报告，不冒充全指标 bitwise 等价。所以该实验支持的是
**PV（velocity component）解决 P-only stop-and-go**；它不能证明非零
acceleration component 带来额外改善。PVA 与 PV 一样有效，是因为此处 A=0。

## P-only pulse region 的消除结果

| 分量 | 差分 | pulse 坐标 | 消除数 | 最大残余 pulse | 最大残余 Hz |
|---|---|---|---|---|---|
| PV | Backward O1 | 37 | 37 | 0 | 0 |
| PV | Backward O2 | 37 | 37 | 0 | 0 |
| PV | Centered O2 | 37 | 37 | 0 | 0 |
| PV | Future O1 | 37 | 37 | 0 | 0 |
| PV | Future O2 | 37 | 37 | 0 | 0 |
| PVA | Backward O1 | 37 | 37 | 0 | 0 |
| PVA | Backward O2 | 37 | 37 | 0 | 0 |
| PVA | Centered O2 | 37 | 37 | 0 | 0 |
| PVA | Future O1 | 37 | 37 | 0 | 0 |
| PVA | Future O2 | 37 | 37 | 0 | 0 |

## Matched PV/PVA negative control

| 差分 | 配对坐标 | stop-go 最大差 | secondary 最大差 | stop-go 等价 |
|---|---|---|---|---|
| Backward O1 | 80 | 0 | 9.98e-11 | true |
| Backward O2 | 80 | 0 | 1.98e-10 | true |
| Centered O2 | 80 | 0 | 4.3e-11 | true |
| Future O1 | 80 | 0 | 1.08e-09 | true |
| Future O2 | 80 | 0 | 3.15e-05 | true |

所有 960 arms 完成，exact-profile fraction 为 1，constraint/fallback/solver
guardrails 为零。primary evaluation 使用 `t=0.5–2.5 s`，排除差分启动阶段。
