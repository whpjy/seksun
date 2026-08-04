# F019A56005-01_EWZ_01 表格提取

提取方式：PDF 文字对象、文字坐标和矢量表格线。未使用 OCR。

## 图纸标题栏

| 字段 | 提取值 |
|---|---|
| Drawing title | Solenoid module cover |
| Drawing number | F019A56005-01 |
| Document type | EWZ |
| Revision / Ind. | 01 |
| DP | 000 |
| Sheet | 1/1 |
| Format | A3 |
| Scale | 1:1 |
| Material | DC01 |
| Material standard / Mat.meets | N2580-1 |
| System | PE |
| Language | en/zh |
| MNR | -- |
| Weight | — |
| Treatment | — |
| Critical part / Crit. P. | — |
| Replaces / Repl. for | — |
| Replaced by / Repl. by | — |
| Missing details | — |
| Source / From | — |

## 修订记录

| Ind. | Change | YYYYMMDD | Drawn | Checked | Released | BWN | Responsible department | Additional information |
|---|---|---|---|---|---|---|---|---|
| 01 | F019A5H009 | 20250530 | YYI3WX | YYI3WX | shk1wx | 376 | RBCD/EFC | — |

## 通用制图与公差标准

| 项目 | 标准或取值 | 附加标记 |
|---|---|---|
| Missing TED | Refer to 3D data | — |
| Linear size | ISO 14405-1:2010-12 | E |
| Angular size | ISO 14405-3:2016-12 | LC |
| Linear dimension unit | mm | — |
| Default surface texture | Rzmax 25 | — |
| Surface texture standard | ISO 1302:2002-02 | — |
| General tolerance | DIN 6930-2:2011-10 | Class f |
| Burr height | DIN 9830:2011-10 | Class f |

## 技术要求

| 序号 | PDF 原文 | 结构化要求 |
|---:|---|---|
| 1 | The surface is firstly galvanized and then subjected to electrophoretic treatment. The surface is covered with black electrophoretic coating. | 表面先镀锌，再进行黑色电泳涂层处理 |
| 2 | The salt mist test conducted in accordance with Bosch-Norm-N42AP226-2016-CCT1. Test duration lasts for 800 hours. After the test, there should be no red rust appears on the surface. | 盐雾试验按 Bosch-Norm-N42AP226-2016-CCT1；持续 800 h；试验后不得出现红锈 |
| 3 | Minimum allowable thickness of galvanized 6um. | 镀锌层最小厚度 6 μm |
| 4 | Minimum allowable electrophoretic paint thickness 25um. | 电泳漆最小厚度 25 μm |
| 5 | Maximum allowable burrs for stamping 0.05mm. | 冲压毛刺最大允许值 0.05 mm |

## 密封区域要求

| 检验编号 | 项目 | 取值 | 区域 |
|---|---|---|---|
| 40 | Surface texture | Rzmax 10 | Sealing area |
| 42 | Geometric tolerance | 0.1（符号待确认） | Sealing area |

## 提取说明

- 标题栏、修订记录和通用标准表的行列关系来自矢量网格线与文字坐标。
- 空白值用 `—` 表示。
- `42` 对应的形位公差符号是矢量图形，不是普通文字，因此当前只确认数值为 `0.1`，具体公差类型仍需人工确认或增加符号路径识别。
- 技术要求原文中的部分中文内容未以可提取文字对象出现；上表中文列是对已提取英文原文的结构化说明。
