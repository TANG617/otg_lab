# 原始记录轨迹

这里保存从外部记录直接导出的 CSV。文件内容保持原样，不在本目录做重采样、
差分或列改写；供实验使用前，必须先转换到 `data/trajectories/` 的规范
CSV，并由统一 loader 回读。

## 目录与命名

```text
data/raw/
  recorded_tasks/
    original_no_velocity_limit.csv
    simplified_no_velocity_limit.csv
    simplified_with_velocity_limit.csv
    0801.csv
```

命名由三部分组成：

- `recorded_tasks/`：这组文件来自任务轨迹记录；
- `original` / `simplified`：原始任务序列或简化任务序列；
- `no_velocity_limit` / `with_velocity_limit`：采集时是否启用速度限制。

全部文件使用源表头：

```text
elapsed time,timestamp,topic,value
```

当前转换只按行序读取 `value`，以实验声明的固定 `dt` 构造规范时间轴。源文件
中的 `elapsed time`、`timestamp` 和 `topic` 会保留用于溯源，但不会成为核心
跟踪器的时间轴。

`0801.csv` 是 E18 保留的历史真机 PV Future-O1 对照记录，SHA-256 为
`eb304992daa300d9c08c2479fa9cb939c3b7b4d0973e5d20e977fa27848a135b`。它保留
三个单轴 position topic：IK position target、Ruckig 输出 `values[0]`，以及只
用于 target 传递时序审计的 `values[4]`。`run_legacy_0801_replay` 不把这个文件
转换成单一固定网格 trajectory；它分别校验三个 topic，并只把真实输出映射到
唯一 1 ms tick，缺失 tick 不插值。重建后 E18 的默认输入是实验目录下的真机
`data/raw/none.csv`。

## 已使用这条记录的实验

E01 和 E02 当前使用：

```text
data/raw/recorded_tasks/original_no_velocity_limit.csv
  → data/trajectories/recorded_tasks_original_no_velocity_limit.csv
```

两者通过 metadata 中的源路径和 SHA-256 关联。

E08 当前使用：

```text
data/raw/recorded_tasks/simplified_with_velocity_limit.csv
  → data/trajectories/recorded_tasks_simplified_with_velocity_limit.csv
```

E08 复用 position 的固定 10 ms canonical 回放，不将 raw `elapsed time`
的采样抖动引入 E04 有限差分。

E18 legacy 入口使用：

```text
data/raw/recorded_tasks/0801.csv
  → 10 ms nominal PV Future-O1 target events
  → 1 ms Ruckig replay output vs recorded output values[0]
```

该 legacy 入口同时在共同 nominal 10 ms target 网格上比较十次 1 ms replan 与
一次 10 ms replan；这个诊断与重建后 E18 的 Sync.No 主问题分开保留。
