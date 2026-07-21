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

矩形孔阵列只定位一个基准孔，其余孔由横纵节距定义；非阵列孔分别输出水平和竖直定位尺寸。SVG 中通过 `data-hole-groups` 保留分析器孔组 ID，方便后续追踪和编辑。

孔径和孔位语义来自同级 `occt-analyzer` 生成的 JSON。投影器会校验 JSON 的 `source_file` 与 STEP 文件名一致，防止关联错误。第三个命令行参数可以省略；省略时仍可生成不带孔语义的基础三视图。

板厚和弯曲半径标注带有 `(REF)`，表示来自几何分析的参考尺寸。歧义半径对、未识别厚度和已被分析器排除的厚度不会输出。普通 STEP 通常不包含设计公差、基准体系、材料、螺纹和加工说明。本工具生成的是自动测量参考图；生产图仍需工程师补充或审核这些信息。

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

当前清单版本为 `0.7.0`。成功连接分析器后，当前测试模型应生成 3 个孔径引出标注、8 条孔定位尺寸、1 条板厚参考标注和 1 组弯曲半径参考说明。
