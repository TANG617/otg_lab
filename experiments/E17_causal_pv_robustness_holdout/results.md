# E17_causal_pv_robustness_holdout

## 2026-07-31 local confirmatory execution

- Run: `20260731T125216.168015Z__0b20a1d9f771`（尚未复制到可发布 `results/`）
- Development 2,380 rows；冻结选择 `pv_local_poly`；holdout 14,280 rows。
- 1,320 个 work-envelope 配对全部改善；11/11 个扰动条件分别通过。最弱条件
  是 `position_noise=0.1 step`：中位 ripple reduction 79.03%，最差 cell
  56.74%，120/120 改善。
- 20/20 constant/ramp/sine/chirp/reversal 合成轨迹逐条通过；最差 ripple
  reduction 98.67%，最大 RMSE excess 为 `8.88e-17 rad`。
- Existing recorded raw-timestamp replay 不是 independent holdout：scheduled P
  RMSE `0.0029509965 rad`，local-poly RMSE `0.0033076103 rad`。固定步长
  Future-O1 因不规则 horizon 合同拒绝执行。
- 因而本实验支持单轴机理与合成鲁棒性，不支持宣称 local-poly 已改善 recorded
  上线性能。
- `acceptance.json: accepted=true`。
