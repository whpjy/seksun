# OCCT Analyzer

不依赖 NX/UG 的 STEP 几何分析验证程序。当前版本读取 STEP，并输出：

- 实体、壳、面、边和顶点数量
- 全局及逐实体的曲面类型统计
- 逐实体的圆柱半径/直径和圆环主副半径归组
- 完整圆柱面的内外侧判断、同轴合并和孔/外圆柱候选
- 跨实体同轴孔合并与独立孔轴线分组
- 四孔矩形阵列、孔距方向和对角距识别
- 全局及逐实体的主要平面配对与厚度候选
- 圆环内外半径配对及折弯圆柱内外半径配对
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

## 批量回归

将多个 STEP 文件放入 `input` 后执行：

```bash
chmod +x scripts/analyze-all.sh
./scripts/analyze-all.sh
```

每个模型会生成独立 JSON，并生成 `output/batch_summary.csv`。

## 当前边界

- 只读取 STEP，不直接读取 NX `.prt`。
- STEP 导入单位统一转换为毫米。
- 当前已完成完整圆柱面的轴向特征归组；槽、厚度和更严格的孔过滤仍待迁移。
- 当前没有装配层级、名称、颜色或 PMI 输出。
## 0.9.0 圆环面空间数据

`radius_pair_analysis.torus_patches` 输出每个圆环面的 `face_id`、三维中心、轴向、表面质心、主半径和次半径。投影器使用这些数据把弯曲半径分析结果关联到具体投影位置。该信息来自精确 B-Rep 曲面，不包含人工推测的公差。

## 0.10.0 板厚平面配对

`thickness_analysis.dominant_pairs` 输出构成主板厚的平面法向、两个平面偏移、面积和聚合质心。投影器使用这些数据在适合的侧视图中定位两张对应面，并生成局部板厚尺寸。

## 0.11.0 平面内部开口

根级 `planar_openings` 从平面面的内轮廓线中提取非圆形开口，排除圆孔，并合并前后表面上边界相同的重复轮廓。每项包含平面法向、三维包围盒、边数和来源线框数量。
