# arXiv 阶段论文工程

本目录是论文阶段初稿 *From Position Samples to Executable Commands:
Timing and Feasibility for Jerk-Limited Reference Following* 的独立 LaTeX
工程。正式正文的唯一源文件是 `main.tex`、`sections/*.tex` 和
`appendix/*.tex`。`logic/` 中的 Markdown 只保存已锁定的论证结构、
claim--evidence 边界、符号和审阅决策，不是另一份正文。

工程只使用登记过的当前证据和冻结证据。V4 已在本次论文更新前严格执行
一次，现永久冻结为 `failed_test_visible_frozen` /
`invalid_method_identity`；虽然观测效应很大，但由于预注册有效性门禁失败，
只能作为非确认性结果。论文构建只读取 V3/V4 有界证据，绝不重跑或恢复
V3/V4，也不执行 V5。打包后的 LaTeX 工程自包含，编译时不访问本目录之外
的文件。

## 工具链与构建

标准工具链为 PDFLaTeX、BibTeX、`natbib` 和 `latexmk`；默认命令还需要
Python 3 与 `uv`。如果当前 Python 环境已经包含仓库锁定的依赖，可以在
调用 Make 时设置 `PYTHON=python3`。工程不使用 shell escape、在线下载
字体、编译时网络访问、SVG 在线转换或专有出版商模板。

在本目录执行：

```sh
make logic-check
make evidence
make figures
make tables
make pdf
make check
```

`make pdf` 生成 `build/main.pdf`，并刷新提交源码所需的根目录
`main.bbl`。`make static-check` 只运行逻辑、claim、引用、数字、
provenance、V3/V4 不可变性和绝对路径检查，不编译正文。`make check`
还会执行完整 PDF 构建、LaTeX 日志 QA 和 arXiv 干净目录编译。

所有实验数字必须由脚本生成，禁止手抄：

- `generated/numbers.tex`：带机器可读 provenance 的数字宏；
- `generated/tables/*.tex`：数据源支持的表格片段；
- `figures/generated/*.pdf`：生成或校验过的矢量图；
- `generated/generation_manifest.json`：生成产物清单。

这些文件只能通过 Makefile 暴露的脚本更新，不得手工修改冻结证据或生成
片段。

## arXiv 源码包

```sh
make arxiv-source
```

arXiv 目标生成 `dist/arxiv_stage_source_v1.zip`、v1 manifest 和
SHA-256，且不覆盖旧 v0 包。脚本会核验每个成员的哈希，并在无仓库访问的
全新临时解压目录中独立编译。v1 包包含 Appendix F、生成的 V4 表图，以及
可携带的 claim/evidence/number provenance。该 ZIP 是阶段初稿源码包，
不代表已投稿或已接收。

## 出版元数据

`metadata.tex` 保存作者本人提供的姓名、单位、联系方式和 PDF 作者字段。
后续如需增加 ORCID、基金或致谢，仍须由作者本人提供并审核；工程不会猜测
出版元数据。
