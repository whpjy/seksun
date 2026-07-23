# seksun-meas

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

接口会同步完成 STEP 分析和三视图生成，并返回分析 JSON、SVG、视图清单与 ZIP 下载地址。任务数据默认保存在 Docker 卷 `meas-jobs` 中。

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
