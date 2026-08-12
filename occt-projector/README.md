# OCCT 三视图投影器

使用 Open CASCADE 的精确隐藏线消除（HLR）从 STEP 模型生成工程图 SVG：

- `front.svg`：主视图，沿 -Y 观察，X 水平、Z 竖直。
- `top.svg`：俯视图，沿 +Z 观察，X 水平、Y 竖直。
- `right.svg`：右视图，沿 -X 观察，-Y 水平、Z 竖直。
- `three_views.svg`：第一角法组合图，右视图位于主视图左侧，俯视图位于主视图下方。
- `views.json`：视图范围、线条数量和自动标注统计。

深色实线表示可见轮廓，灰色虚线表示隐藏轮廓，细点划线表示中心线。三个视图使用相同比例，并保持投影对齐关系。

## 自动标注

当前版本输出：

- 主视图总体宽度和高度。
- 俯视图总体深度。
- 完整圆孔的中心标记。
- 相同直径孔的合并孔径标注。
- 矩形孔阵列的孔数量、直径和横纵节距。
- 孔中心相对投影视图左边界、下边界的基准定位尺寸。
- 分析器高可信度主板厚度的参考标注。
- 非歧义弯曲内外半径对及其估算重复数量。
- 当可见投影中存在同心的内外圆弧证据时，自动生成指向具体圆弧的半径引线。

矩形孔阵列只定位一个基准孔，其余孔由横纵节距定义；非阵列孔分别输出水平和竖直定位尺寸。SVG 中通过 `data-hole-groups` 保留分析器孔组 ID，方便后续追踪和编辑。

孔径和孔位语义来自同级 `occt-analyzer` 生成的 JSON。投影器会校验 JSON 的 `source_file` 与 STEP 文件名一致，防止关联错误。第三个命令行参数可以省略；省略时仍可生成不带孔语义的基础三视图。

## 任意方向正交投影

第四个命令行参数可传入自定义视图 JSON。此时投影器按给定观察方向和画面横轴
生成独立 SVG，供智能体探索模型空间：

```bash
occt-projector input.step output analysis.json view-definitions.json
```

```json
{
  "views": [
    {
      "id": "explore_01",
      "title": "explore_01",
      "direction": [1, -1, 1],
      "x_direction": [1, 1, 0]
    }
  ]
}
```

方向向量无需预先归一化，但两向量不能为零或互相平行。自定义模式最多接受
12个视图，`views.json` 的投影方法为 `ORTHOGRAPHIC_CUSTOM`，且不生成
`three_views.svg`。

板厚和弯曲半径标注带有 `(REF)`，表示来自几何分析的参考尺寸。半径引线只有在分析器半径与投影可见圆弧同时匹配时才输出；无法建立空间关联时自动退回半径汇总说明。歧义半径对、未识别厚度和已被分析器排除的厚度不会输出。普通 STEP 通常不包含设计公差、基准体系、材料、螺纹和加工说明。本工具生成的是自动测量参考图；生产图仍需工程师补充或审核这些信息。

## Docker 运行

服务器目录建议为 `/data/wh/seksun/occt-projector`，并与 `/data/wh/seksun/occt-analyzer` 同级：

```bash
cd /data/wh/seksun/occt-projector
chown -R 10001:10001 output

env \
  -u HTTP_PROXY -u HTTPS_PROXY \
  -u http_proxy -u https_proxy \
  -u ALL_PROXY -u all_proxy \
  docker compose build

docker compose run --rm projector
```

结果位于 `output/front.svg`、`output/top.svg`、`output/right.svg`、`output/three_views.svg` 和 `output/views.json`。

当前清单版本为 `0.12.0`。投影器优先使用 analyzer `0.12.0` 输出的 `datum_dimensions` 生成孔位尺寸链；旧版分析 JSON 仍会回退到投影边界计算。投影器会从非圆形平面内轮廓中选择主视图面积最大的紧凑开口，并排除大面积、高边数的压筋/内侧轮廓；当前测试模型会选择中央约 `38.441 × 47.577 mm` 的10边开口，而不是 `76.846 × 114.846 mm` 的20边压筋轮廓。开口宽度尺寸线布置在上方 Ø9 孔与上部轮廓之间，避免尺寸线穿过圆孔。圆孔不会进入开口候选；不能形成有效二维范围的轮廓也会被排除。
