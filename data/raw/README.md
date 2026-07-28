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
