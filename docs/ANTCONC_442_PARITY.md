# AntConc 4.4.2 功能对齐与验收基线

基准版本：AntConc 4.4.2（2026-07-20）。依据为 Laurence Anthony 官方产品页及 4.4.2 Help PDF：

- https://www.laurenceanthony.net/software/antconc/
- https://www.laurenceanthony.net/software/antconc/releases/AntConc442/docs/help.pdf

本文区分“统计语义对齐”和“桌面产品完整复刻”。前者可以用固定语料和精确输出验证；后者还包括桌面媒体播放、模型下载、第三方 AI 服务、文件管理器等非统计能力，不能用网页核心算法测试冒充已完成。

## 功能矩阵

| AntConc 4.4.2 模块 | 本项目实现 | 验收状态 |
|---|---|---|
| KWIC | Words、Case、通配符、逐 Token Regex、Full Regex、POS、L/R/FILE/ID/ROW 三级排序、按值/模式频次排序、分页、随机 Results Set、随机种子、KPF、隐藏检索项、来源跳转 | 核心对齐 |
| Advanced Search | Search Query List、Context Query List、OR/AND、L10-R10、Not in context | 核心对齐 |
| Concordance Plot | 文档命中分布、10-200 bins、叠加词、零命中文档、DocID/Path/Tokens/Frequency/NormFreq/Dispersion 排序及反向排序 | 核心对齐 |
| File View | 完整源文本、全部命中高亮、当前命中定位、命中/词数/形符数、Row 跳转 | 核心对齐 |
| Cluster | 检索词左/右锚定、2-10 长度、最小频次/文档数、频次/范围/词项/条件概率排序、标准化频率 | 核心对齐 |
| N-Gram | 2-5 gram、最小频次/文档数、过滤、标点、开放槽 S1-Sn、S_TT、S_TTR、S_Ent | 核心对齐 |
| Collocate | L/R0-10、方向频次、范围/POS；MI/MI2/MI3、Dice、LogDice、LogRatio、MinSens、Mu、RRF、DRF、Z、T、Log-Likelihood、Chi-square、p、Bonferroni | 核心对齐 |
| Word | Type、Type+POS、Headword/Lemma、大小写、频次/范围、起始/词尾/反向排序、停用词/允许词、原始与每百万频率 | 核心对齐 |
| Keyword | 目标/参照语料、正负关键词、频次/范围、Log-Likelihood、Chi-square、LogRatio | 词表来源对齐；Cluster/N-Gram/Collocate 来源待补 |
| Wordcloud | 词频来源、停用词、标点、25-200 词、确定性布局、主题色 | 基础对齐；工具结果/草稿来源、Mask、PNG 导出待补 |
| ChatAI | 无 | 未实现；依赖本地或第三方模型及单独安全边界 |
| Corpus Manager / Builder | 用户上传、老师语料清单扫描、原文/双语标注分类、配对段落导入、加工、不可变 SQLite 索引、索引健康检查与修复 | 项目语料流程对齐；AntConc 全格式构建器未复刻 |
| Multimodal | 无 | 未实现；网页媒体授权和时间轴协议尚未定义 |
| Whisper / AI Model Manager | 无 | 未实现；模型下载、硬件探测和转写队列尚未定义 |

## 查询与统计语义

- 简单查询按连续 Token 匹配；中文无空格词先尝试原始 Token，未命中时再走安全分词。
- Full Regex 在完整 `document_streams.text` 上执行，再用字符偏移映射到 Token，拒绝零长度命中并设置超时。
- 随机 Results Set 使用固定种子的确定性排序；返回样本命中数和样本前可用命中总数，样本不分页。
- Advanced Search 将主查询和查询列表做并集并按位置去重；语境条件按命中项相对 Token 偏移计算。
- 开放槽 N-Gram 跨文档分别扫描，不跨句；报告槽位变体数、type/token ratio 和 Shannon entropy。
- 搭配和关键词使用 2x2 列联表，并保护零频、非法边际和非有限数值。

## 老师语料金标准

固定语料库：`老师语料·双语标注·湖南农民运动考察报告`，ID `71d92f26-c5e5-485f-ac83-3ebccb6a9acc`。

| 检查项 | 精确期望 |
|---|---|
| 中文 Word | 9,908 tokens；2,323 types；`的` 570 |
| 英文 Word | 13,601 tokens；2,542 types；`the` 1,398 |
| 中文 KWIC `农民` | 259 |
| 英文 Full Regex `\bpeasant\b` | 158 |
| Cluster `农民`、size=2、左锚定 | 28 types；首项 `农民协会` 49 |
| 英文 3-Gram、S2 开放槽、min=10 | 51 types；首项 `the <*> of` 207；136 个槽位变体；entropy 6.771328 |
| Collocate `农民`、L2/R2 | node 259；首项 `协会` 50 |
| Plot `农民` | 259 |
| CQP `[word="the"] [] [word="of"]` | 207 |
| 平行检索 `农民` | 259 个中文句对；首个英文对应句含 `peasant` |

## 自动化验收

```powershell
cd backend
$env:DATABASE_URL='postgres://corpus_platform:corpus_platform@127.0.0.1:5432/corpus_platform'
$env:DJANGO_SETTINGS_MODULE='config.settings.local'
.\.venv\Scripts\python.exe manage.py test

cd ..\frontend
npm run lint
npm run build
```

验收原则：所有新增统计能力必须同时有小型手算语料测试和老师语料回归测试；只有页面存在、没有精确数值断言，不算功能完成。
