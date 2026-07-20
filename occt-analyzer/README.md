# OCCT Analyzer

不依赖 NX/UG 的 STEP 几何分析验证程序。当前版本读取 STEP，并输出：

- 实体、壳、面、边和顶点数量
- 表面积与体积
- 轴对齐包围盒

服务器建议部署目录：

```text
/data/wh/seksun/occt-analyzer
```

## 准备测试文件

将测试 STEP 文件复制为：

```text
input/test.stp
```

建议第一轮使用单个、尺寸已知的简单零件，不要使用大型装配。

## Docker 构建与运行

```bash
chown 10001:10001 output
docker compose build
docker compose run --rm analyzer
```

`chown` 让容器内的非 root 用户可以写入挂载的输出目录，只需执行一次。

成功后终端会显示 JSON，同时生成：

```text
output/result.json
```

## 分析其他文件

文件仍需放在 `input` 目录内：

```bash
docker compose run --rm analyzer \
  /data/input/part.step \
  /data/output/part.json
```

## 当前边界

- 只读取 STEP，不直接读取 NX `.prt`。
- STEP 导入单位统一转换为毫米。
- 当前没有孔、槽、圆角等制造特征识别。
- 当前没有装配层级、名称、颜色或 PMI 输出。
