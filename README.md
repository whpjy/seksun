# seksun-meas

## 第一页最小化 2D/3D 对应实现

当前版本以 `example` 中的 `F019A56005-01` 为典型样本，只解析 PDF 第一页。处理链先将文本对象结构化为线性尺寸、孔径、半径、角度和粗糙度标注，再使用“类型 0.4 + 尺寸 0.4 + 上下文 0.2”的确定性评分生成 2D/CAD 候选对应。分数接近的候选会保留为 `ambiguous`，不会被强制绑定。

每个 PDF/STEP 对比任务新增产物：

- `manufacturing_specification.json`：全部第一页 2D 标注、CAD 特征、评分、候选对应和溯源信息。
- `model.stl`：由 OCCT 对 STEP 网格化导出，供前端 Three.js 交互显示。
- `comparison.json.comparison_rows`：前端列表所需的逐标注期望值、测量值、状态与 CAD 特征 ID。

最小实现中，`C10` 六孔组已执行刚性阵列配准和逐孔尺寸检验；尚未有确定性 CAD 适配器的线性尺寸、圆角、角度和粗糙度会明确标记为 `unmapped` 或 `ambiguous`，供后续人工复核。

本项目包含两个 Open CASCADE 工具：

- `occt-analyzer`：读取 STEP 并生成特征分析 JSON；
- `occt-projector`：读取 STEP（可选分析 JSON）并生成 `front.svg`、`top.svg`、`right.svg`、`three_views.svg` 和 `views.json`。

## 完整 Web 服务

项目已提供 FastAPI 文件处理接口，并可与同级的 `seksun-web` 一起启动：

```powershell
docker compose -f compose.fullstack.yaml up --build
```

启动后访问：

- Web 页面：<http://localhost:3000>
- API 文档：<http://localhost:8080/docs>
- 健康检查：<http://localhost:8080/health>

上传接口：

```text
POST /api/v1/jobs
Content-Type: multipart/form-data
字段名: file
```

接口会同步完成 STEP 分析和三视图生成，并返回分析 JSON、SVG、视图清单与 ZIP 下载地址。

## PDF / STEP 对比（首个闭环）

当前已实现检验特性 `C10` 的孔组对比：系统直接读取原始矢量 PDF 的文本和路径内容流，自动提取孔径、公差、数量、局部视图比例和二维孔阵列，再将其与 STEP 中的逻辑圆柱特征进行旋转、镜像和平移不变的整体匹配，并逐孔输出通过/失败结果。

```text
POST /api/v1/comparisons
Content-Type: multipart/form-data
字段：pdf（.pdf）、step（.stp/.step）
```

接口会为每个任务自动生成并保存：

- `measurement_plan.json`：从 PDF 自动恢复的 C10 测量要求。
- `vector_extraction.json`：视图比例、六孔矢量圆和相对中心坐标。
- `pdf_extraction_diagnostics.json`：文本、路径和候选数量等诊断信息。
- `comparison.json`：PDF 与 STEP 的逐孔比较结果。

### 对比历史与服务器持久化

每次调用 `POST /api/v1/comparisons` 都会创建一个服务器会话。原始 PDF、STEP、
STL 和全部结构化输出保存在项目隐藏目录 `.seksun-meas/jobs/{job_id}`，历史索引
保存在 `.seksun-meas/history.sqlite3`。前端本机只通过 API 加载数据，不保存文件副本。

历史接口：

```text
GET /api/v1/comparisons
GET /api/v1/comparisons/{job_id}
GET /api/v1/comparisons/{job_id}/inputs/pdf
GET /api/v1/comparisons/{job_id}/inputs/step
```

服务器首次部署前创建可写的隐藏目录：

```bash
mkdir -p .seksun-meas/jobs
chown -R 10001:10001 .seksun-meas
chmod -R 750 .seksun-meas
```

`compose.service.yaml` 将该目录绑定到容器的 `/var/lib/seksun-meas`。因此重新构建
或替换容器不会删除历史，`docker compose down -v` 也不会删除这个绑定目录。
不要把 `.seksun-meas` 提交到 Git；项目 `.gitignore` 已包含相应规则。

当前自动解析范围是原生矢量 PDF 中的“数量 × 孔径 ± 对称公差”标注，圆轮廓需由标准三次贝塞尔圆路径构成。扫描 PDF、非对称公差和其他尺寸类型将在后续阶段扩展。

当图纸不包含上述孔径模板时，智能审核接口不会再将任务标记为失败。系统会完成
STEP精确分析和多模态观察，并进入 `requirement_discovery` 模式：从PDF文本层提取
有位置坐标的尺寸、直径、半径、角度和参考尺寸候选，交由模型归纳，但不输出未经
确定性绑定的合格/不合格结论。任务终态为 `needs_review`，工程师选择目标检验特性
后，再为该标注类型建立对应的确定性解析和CAD映射适配器。

可以独立检查 PDF 自动提取结果：

```powershell
python -m service.pdf_extraction ..\example\F019A56005-01_EWZ_01.pdf
```

不启动 OCCT 服务时，也可以使用依赖无关的 STEP 诊断读取器复核已有测量计划：

```powershell
python -m service.comparison `
  --step ..\example\f019a56005-01.stp `
  --plan data\pdf-extractions\F019A56005-01_EWZ_01\measurement_plan.json `
  --vector data\pdf-extractions\F019A56005-01_EWZ_01\vector_extraction.json
```

案例结果为六孔全部匹配，其中五孔为 `Ø6.5`，一孔为 `Ø7.0`，因此不满足图纸要求的 `6 × Ø6.5 ±0.1`。

## 本机启动（Windows / Docker Desktop）

本机不需要安装 Open CASCADE 或 C++ 编译环境，只需安装并启动 Docker Desktop（Linux containers）。在 PowerShell 中执行：

```powershell
.\run-local.ps1 -StepFile "D:\models\part with space.stp" -Build
```

首次运行使用 `-Build` 构建镜像，之后可省略。结果写入：

```text
occt-analyzer/output/<文件名>.json
occt-projector/output/<文件名>/
```

如果希望保留脚本临时复制到 `occt-analyzer/input` 的 STEP 文件，加上 `-KeepInput`。也可以直接把 STEP 文件放入 `occt-analyzer/input` 后，继续使用各目录下的批处理脚本。

Docker Desktop 未启动时会看到 `failed to connect to the docker API`，启动 Docker Desktop 后重试即可。

## 服务器运行

原有的 `docker compose` 用法保持不变，分别进入 `occt-analyzer` 或 `occt-projector` 目录执行 README 中的命令即可。

## AI 智能审核（第一阶段）

服务提供异步智能审核接口：

```text
POST /api/v1/agent-runs
Content-Type: multipart/form-data
字段：pdf、step
```

创建任务后可通过以下接口查询状态和实时事件：

```text
GET /api/v1/agent-runs/{run_id}
GET /api/v1/agent-runs/{run_id}/events
GET /api/v1/agent-runs/{run_id}/stream
```

复制 `.env.example` 为 `.env` 并填写 `DASHSCOPE_API_KEY`。默认使用
`qwen3.7-plus-2026-05-26`；当几何配准残差超过阈值或证据不完整时，
路由到 `qwen3.8-max`。将 `AGENT_MODEL_MODE=mock` 可在不调用外部模型的
情况下运行完整工具调用和前端事件流程。

模型负责选择和解释工具操作，PDF提取、OCCT测量及确定性比较结果始终作为
最终工程证据。`model_io.json` 保存可观察的模型输入输出用于开发调试，但不会
保存API密钥、鉴权头或模型隐藏推理内容。

智能审核会把PDF第一页和OCCT生成的前、顶、右固定投影视图转换为有界PNG，
作为Qwen的多模态观察输入。模型还可调用 `render_spatial_view` 选择观察方向和
画面横轴；OCCT生成精确隐藏线投影后，图片会再次返回模型，并作为探索图显示在
前端证据链中。视觉内容用于理解图纸与视图，尺寸、公差和最终结果仍必须来自
结构化工具。当前阶段尚不包含实体剖切、面隐藏/隔离或面ID回溯，前端会明确
显示这一能力边界。

对于不合格任务，智能体至少执行两步空间探索：先生成全局斜视图，再根据返回
图像选择互补方向复核失败CAD特征。后续视图通过 `based_on_view_id` 记录探索链，
并用 `focus_feature_id` 将确定性比较证据中的异常孔组标红。系统拒绝重复方向、
不存在的焦点特征以及同一模型轮次内的批量投影，确保后续视角确实建立在上一张
观察图之上；单次任务最多生成三个探索视图。

探索产物可通过受限接口读取：

```text
GET /api/v1/agent-runs/{run_id}/artifacts/spatial/{view_id}/{file}
```
