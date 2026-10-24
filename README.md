# SJTU Course Exporter

**Export SJTU classroom transcripts and slides into an AI-ready local course dataset.**

把上海交通大学课堂实录平台（[v.sjtu.edu.cn](https://v.sjtu.edu.cn)）中**你本人有权限访问**的课程资料，
批量导出为结构化、本地可长期保存、适合直接交给 ChatGPT / Claude / Gemini / NotebookLM / 本地 LLM 的
**AI-ready 课程语料**。

> **这不是下载工具。** 它不绕过登录、不破解权限、不处理 DRM，也**不下载视频**。
> 它只是把你已经能在网页上看到的内容（语音转写、平台 AI 概要、PPT 课件帧）整理成一份干净、
> 可搜索、可长期保存的本地文件。

---

## Features

- 🗣️ **逐条 ASR 转写提取** —— 带时间戳，原始文本（`raw_text`）永远保留，不做语义纠错
- 📚 **课次自动发现** —— 从页面实际读取，不写死「第11节/第12节」
- 🔁 **整门课程批量导出** —— 自动逐个切换课次、等待加载完成、抓取、记录、继续
- 🖼️ **PPT 课件提取** —— 采样帧 + 关键词 + 出现时间
- 🧭 **平台 metadata / 概要 / 章节导航** —— 关键词、内容概要、章节时间轴
- 🎛️ **范围 / 内容 / 格式 分开选** —— 一个「开始导出」按钮，不再是八个平级按钮
- 📁 **直接写进你自己选的文件夹** —— File System Access API，目录句柄记住，
  不再把上百个文件倒进「下载」文件夹；不支持的浏览器自动退回**单个 ZIP**
- 🧩 **标准 manifest** —— 版本化 JSON schema，浏览器与 Python 之间唯一契约
- ⏱️ **PPT × 语音时间轴对齐** —— 「第 17:00 这张 PPT，老师讲了什么」
- 📝 **Markdown / JSON / SRT 导出** —— 全文可搜索
- 🤖 **AI-ready 数据包** —— `full_course.md` + `ai_prompt.md`，还包含 ASR 纠错守则
- 🐍 **零依赖 Python CLI** —— Python 3.11+，只用标准库，不需要 pip install 任何东西

---

## Installation

### 1. Python 端（整理工具）

只需要 Python **3.11 或更高版本**。运行时零依赖，不需要 `pip install`。

```bash
git clone https://github.com/baokhanh09048-bit/SJTU-Course-Exporter.git
cd SJTU-Course-Exporter

# 直接运行（无需安装）：
python -m cli.main --help

# 或者安装成命令行工具：
pip install -e .
sjtu-course-exporter --help
```

### 2. Tampermonkey 安装

1. 在浏览器里安装 [Tampermonkey](https://www.tampermonkey.net/) 扩展。
2. 打开 Tampermonkey 面板 → **添加新脚本**。
3. 删除模板内容，粘贴 `userscript/sjtu-course-exporter.user.js` 的全部内容。
4. `Ctrl+S` 保存。

> 需要 **Chrome / Edge / Firefox** 桌面版。Safari 与移动端浏览器不保证可用。

### 3. Userscript 安装（一键）

如果本仓库已经发布到 GitHub，可以直接打开：

```
https://raw.githubusercontent.com/baokhanh09048-bit/SJTU-Course-Exporter/main/userscript/sjtu-course-exporter.user.js
```

Tampermonkey 会自动弹出安装页面。

---

## Usage

### 程序代码放哪里，课程资料放哪里

这两个是**完全分开**的两件事，请不要混在一起：

| | 位置 | 说明 |
| --- | --- | --- |
| **程序代码** | `<源码目录>/SJTU-Course-Exporter` | 本仓库，git 管理，会被更新覆盖 |
| **课程资料** | `<资料目录>/SJTU-Course-Exports`（推荐） | 你自己的导出目录，脚本从不会碰代码目录 |

推荐第一次就把资料目录建成 `<资料目录>/SJTU-Course-Exports`，之后所有导出都放这里。
不要把导出结果放进代码目录 —— 那样会污染 git 仓库，而且升级时容易误删。

### 第一次使用（5 步）

1. 登录课堂实录平台，打开一门课的**点播播放页**，等右侧「**节次**」列表出现。
2. 右下角会出现 **SJTU 课程导出** 面板（可拖动，不会挡住播放器）。
3. 在面板里选 **导出范围**、勾 **导出内容**。
4. 点击「**选择保存文件夹**」，选一个目录（例如 `<资料目录>/SJTU-Course-Exports`）。
5. 点击「**开始导出**」，等进度条跑完。

完成后你的目录里会出现一个以**课程名**命名的文件夹。

### 面板怎么用

面板按「先选什么、再选什么」的顺序自上而下排列：

```
课程信息        ← 课程名 / 教师 / 已发现课次数 / 当前课次
   ↓
导出范围        ← 当前节 / 当前课次组 / 整门课程        （三选一）
   ↓
导出内容        ← 语音转写 / PPT 图片 / 平台概要 / 章节导航（可多选，默认全选）
   ↓
输出格式        ← AI 资料包（推荐） / 原始 Manifest JSON
   ↓
保存位置        ← 选择保存文件夹 + 保存到文件夹 / 下载单个 ZIP
   ↓
[ 开始导出 ]    ← 唯一的主按钮
   ↓
进度            ← 18 / 27 课次、ASR 条数、PPT 帧数、成功 / 失败
   ↓
高级设置 ▾      ← 默认折叠
```

#### 导出范围的三个选项

| 选项 | 导出什么 | 什么时候用 |
| --- | --- | --- |
| **当前节** | 只导出你现在正在看的那一节课（一个课次） | 只想补某一节，或先试一下效果 |
| **当前课次组** | 导出**和当前课次同一天**的所有节次 | 一次课连上三节（第 11/12/13 节），想一起拿走 |
| **整门课程** | 遍历页面上发现的**全部课次**，逐个自动切换、抓取 | 期末复习，一次拿全 |

> 「课次」= 平台上的一节课（例如「第1周 - 周三 第11节」）。
> 一门课通常有很多课次。「当前课次组」按**日期**分组，所以同一天连着的第 11/12/13 节算一组。

#### 导出内容的四个选项

默认全选。取消勾选会**真的不导出**对应内容（不只是不显示）：

- **语音转写** —— 逐条 ASR 文本 + 时间戳（→ `transcript.md`）
- **PPT 图片** —— 课件采样帧（→ `slides/<课次>/001.jpg`）
- **平台概要** —— 平台 AI 生成的关键词与内容概要
- **章节导航** —— 平台 AI 生成的章节时间轴

#### 输出格式的两个选项

| 选项 | 是什么 | 给谁用 |
| --- | --- | --- |
| **AI 资料包**（默认） | Markdown + SRT + PPT + manifest 的完整目录 | **普通用户推荐**。可以直接丢给 ChatGPT / NotebookLM |
| **原始 Manifest JSON** | 只有一份 `manifest.json` | 开发者 / 需要自己写脚本处理的人 |

高级选项里还有两个开关：**同时生成 SRT 字幕**、**同时保留 manifest.json**。
后者默认勾选，因为 Python CLI 需要它；如果你只用 AI 读 Markdown，可以取消。

#### 保存位置

见下面[保存位置与目录结构](#保存位置与目录结构)。

### 采集过程中的细节

- **不会用固定 sleep 等待。** 脚本用 `MutationObserver` 监听 DOM，
  直到「课次内容指纹」**确实发生变化**并稳定下来，才认为新数据加载完成。
- **交叉校验，绝不张冠李戴。** 切换课次后脚本会核对页面实际落在哪个课次；
  对不上就报失败，而不是把上一节的内容记到这一节名下。
- **对服务器温和。** 课次之间默认间隔 1.2 秒，PPT 图片之间 180 毫秒，全部串行。
- **失败会重试并记录**，单个课次失败不会中断整门课程。
- **支持断点续跑。** 已保存且图片完整的课次记录在 Tampermonkey 存储里；中断后再次运行会跳过这些课次。
  如果所选范围已全部完成，再点「开始导出」会重新导出全部课次。「当前节」始终重新导出。
- **缺图会明确提示。** 如果页面尚未提供某些 PPT 图片地址，进度区会显示「完成（有缺失）」及缺失数量，
  该课次不会记为已完成，可以稍后重试。
- **可以中止。** 导出开始后主按钮会出现「停止」，当前课次结束后优雅退出。

### 第 2 步：用 Python 整理（可选）

如果你选了「AI 资料包」，其实**已经可以直接用了** —— 不需要 Python。

只有在下面这些情况才需要 Python CLI：

- 浏览器环境不支持文件夹保存，你只拿到了一个 ZIP（先解压即可）
- 你想重新生成、改格式、或者只要 SRT

```bash
# 看看这份 manifest 里到底有什么
python -m cli.main validate manifest.json

# 生成完整的 AI-ready 数据包
python -m cli.main pack manifest.json --out course_export

# 或者只要合并版 Markdown
python -m cli.main merge manifest.json --out full_course.md

# 或者只要 SRT 字幕
python -m cli.main srt manifest.json --out subtitles/
```

### 离线解析已保存的网页

如果你只是把网页 `Ctrl+S` 保存成了 HTML，也可以用离线解析器：

```bash
python -m cli.main inspect "课堂实录.html"
python -m cli.main build-manifest "课堂实录.html" --out manifest.json
```

`inspect` 会打印它在这份 HTML 里**真实读到**的内容：课程名、教师、课次列表、
逐条转写（条数 / 首尾时间）、平台概要（关键词 / 概要 / 章节导航）、PPT 帧数。

> ⚠️ **一份保存的 HTML 只包含「保存那一刻屏幕上显示的那一节」。**
> 其他课次只有列表条目，没有转写内容。要导出整门课程，请用 userscript。

### Python CLI 命令一览

| 命令 | 作用 |
| --- | --- |
| `inspect <html>` | 报告离线 HTML 中可提取的内容 |
| `build-manifest <html> --out <file>` | 从 HTML 生成 manifest |
| `validate <manifest>` | 校验 schema + 敏感信息检查 |
| `merge <manifest> --out <file>` | 合并整门课程为单个 Markdown |
| `srt <manifest> --out <dir>` | 每个课次一个 SRT |
| `pack <manifest> --out <dir>` | 生成完整数据包 |
| `ai-prompt <manifest>` | 单独打印/输出下游 AI 说明 |
| `api-candidates` | 打印从平台 JS 中提取的内部接口清单 |

---

## 保存位置与目录结构

### 保存位置：先由你选一次，之后自动记住

浏览器**不会**允许网页自己决定往哪个绝对路径写文件 —— 这是安全设计，任何工具都绕不过。
所以第一次必须由你主动点「**选择保存文件夹**」。

选定之后：

1. 脚本把目录句柄存进浏览器的 **IndexedDB**（只存句柄，**不存任何 Cookie / token**）。
2. 下次打开播放页会自动恢复这个位置，并在日志里提示「已恢复保存位置」。
3. 浏览器可能在一段时间后让权限失效。脚本会检查权限，需要时再次弹出授权框。
4. 想换目录，再点一次「选择保存文件夹」即可。

> ⚠️ **一个必须说明的限制**：出于隐私考虑，File System Access API
> **不会把完整路径告诉网页**。面板上只能显示你选的**文件夹名称**（例如
> `SJTU-Course-Exports`），显示不出 `<资料目录>/SJTU-Course-Exports`。
> 这是浏览器的限制，不是脚本的缺陷 —— 请自己记住选的是哪个目录。

### 保存方式：文件夹 或 单个 ZIP

| 保存方式 | 何时可用 | 结果 |
| --- | --- | --- |
| **保存到文件夹**（默认） | Chrome / Edge 等支持 File System Access API 的浏览器 | 直接在你选的目录里写出完整目录树 |
| **下载单个 ZIP** | 所有浏览器；也是不支持上述 API 时的自动回退 | **一个** `.zip` 文件，解压后就是同样的目录树 |

无论哪种方式，**一次整门课程导出最多只产生一个目录或一个 ZIP**，
不会把几百个文件直接倒进你的「下载」文件夹。

如果只导出「当前节」且只选了一个文件（例如 Manifest JSON），会直接存那一个文件，
不会再套一层 ZIP。

### 整门课程 / 课次组 的目录结构

```
<你选择的目录>/
└─ 孙子兵法/
   ├─ manifest.json                  # 结构化原始数据（Python CLI 需要）
   ├─ full_course.md                 # ★ 整门课程合并版，直接喂给 AI
   ├─ index.md                       # 目录与统计
   ├─ ai_prompt.md                   # ★ 给下游模型的说明与纠错守则
   ├─ transcripts/
   │  ├─ 第01周_第11节.md            # 单课次实录（按 PPT 分段）
   │  └─ 第01周_第12节.md
   ├─ subtitles/                     # 仅当勾选「同时生成 SRT 字幕」
   │  └─ 第01周_第11节.srt
   ├─ summaries/
   │  └─ 第01周_第11节.md            # 平台 AI 概要（明确标注性质）
   └─ slides/
      ├─ 第01周_第11节/
      │  ├─ 001.jpg                  # PPT 采样帧，按出现顺序编号
      │  └─ 002.jpg
      └─ 第01周_第12节/
```

### 只导出「当前节 / 当前课次组」的目录结构

临时导出**不会**把文件散落在根目录，每个课次有自己的文件夹：

```
<你选择的目录>/
└─ 孙子兵法/
   ├─ manifest.json
   ├─ ai_prompt.md
   └─ sessions/
      └─ 2026-09-16_第12节/
         ├─ transcript.md
         ├─ summary.md
         ├─ transcript.srt           # 仅当勾选 SRT
         └─ slides/
            ├─ 001.jpg
            └─ 002.jpg
```

（导出多个课次时，根目录还会多出 `full_course.md` 和 `index.md`。）

### full_course.md 的格式

```markdown
# 孙子兵法

## 第1周 2026-09-16 第11节

- 教室：示例教室
- 课次 ID：`2026-09-16-第11节-示例教室-cadd7c61`

### 平台 AI 概要

> ⚠️ 以下为 SJTU 平台 AI 自动生成内容，不是教师原话。

**关键词**：…

### 课堂实录

_说明：平台只提供每张 PPT 的出现时间，段落结束时间由下一张 PPT 的开始时间推断。_

#### 07:19 [PPT 002] 示例关键词甲、示例关键词乙、示例关键词丙

**[07:19]** 这是示例转写文本，用于展示导出格式。…
```

老师原话和平台 AI 概要**永远不会混在一起**。

---

## AI usage

把 `course_export/` 整个目录（或至少 `full_course.md` + `ai_prompt.md`）交给模型即可。

`ai_prompt.md` 会告诉下游模型：

- 这些资料包含三类**性质不同**的材料：老师课堂 ASR / PPT / 平台 AI 概要
- ASR 会出现同音字、断句错误、专有名词错误、古文识别错误
  （例如「全胜」可能被识别成「全称」「全省」「权称」）
- **必须保留原始 ASR**；可以结合 PPT 与上下文纠正明显错误；无法确定时标注 `【ASR存疑】`
- **不得凭常识偷偷补充老师没有讲的内容**
- 必须区分：老师课堂内容 / PPT / 平台 AI 总结 / 模型推断

并给出期望产出：完整讲义、知识结构、人物/概念/篇名/战例索引、
**开卷考试 Ctrl+F 速查资料**、老师反复强调的内容、考试要求、论文要求、易考考点。

> **SJTU Course Exporter 本身不调用任何付费模型 API。** 数据包是中立的，
> 用哪家模型由你决定。

### ASR 清洗原则

采集层**永远保存 `raw_text`**。Python 额外产生 `normalized_text`，
但 normalization 只做机械处理：

- 空格整理、重复标点、HTML entity、换行、Unicode NFC、中文标点前后空格

**不做**语义级纠错。`「全称」→「全胜」` 这种判断交给后续 AI，
并且必须显式标注。测试里有专门的用例守护这条规则。

---

## Privacy

> **This tool is intended only for exporting course materials that the user is already authorized to access.**

明确不做、也不会做的事：

- ❌ 绕过 SJTU 身份认证 / 破解登录
- ❌ 绕过 DRM
- ❌ 绕过课程权限 / 获取无权限课程
- ❌ 把 Cookie / token 写入仓库或导出文件
- ❌ 自动上传教师课程内容到公共服务器
- ❌ 下载视频（本阶段明确不做）

技术上的保证：

- **Python 端从不联网。** 它只读本地 JSON / HTML。
- **浏览器端从不读取 Cookie 或 token**，只读页面 DOM。
- **签名 URL 一律脱敏。** 视频链接里的 `?key=…` 会被替换成 `REDACTED`；
  `cli/manifest.py` 里的 `assert_no_secrets` 是一道硬性关卡 ——
  只要 manifest 里出现凭证字段或未脱敏的签名 URL，导出就会失败。
- 导出的 manifest 里带有 `id_source: "synthetic"`、`end_time_source:
  "inferred_next_slide"` 等标记，让下游知道哪些值是推断的。

⚠️ **请务必注意：**

课程录像、PPT、教师讲话可能受到**版权、隐私及学校规章**约束。
默认**只供个人学习和备份**。请不要公开传播、不要上传到公共服务器、
不要用于训练公开发布的模型。

代码本身可以开源，但**不要**上传：真实课程 transcript、真实 PPT、
真实课程 URL token、个人信息。

---

## Limitations

- **只导出你保存/打开的那一节（离线模式）。** 一份保存的 HTML 只有一个课次的转写内容。
  整门课程需要 userscript 逐课次点击切换。
- **不下载视频。** 视频字段只记录「存在」，并抹掉签名。
- **PPT 是采样帧，不是原始课件。** 平台把 PPT 渲染成 JPEG 采样帧（见
  `docs/reverse-engineering-notes.md` §7）。更高保真的原始 `.pptx`/`.pdf`
  需要走另外的接口，目前**尚未在线验证**。
- **平台 AI 概要是二次加工。** 它可能出错，且**不是**教师原话。
- **章节/PPT 的结束时间是推断值。** 平台只给开始时间。
- **内部 API 尚未启用。** 平台确实有更稳定的 JSON 接口（已从它自己的 JS 里提取出来，
  见 `python -m cli.main api-candidates`），但请求参数**尚未在线验证**，
  所以当前默认走 DOM 解析。这是刻意为之 —— 不臆造没验证过的接口。
- **选择器会随平台改版失效。** 所有选择器集中在
  `adapters/selectors.py` 与 userscript 的 `SEL` 两处，且都带 fallback 列表。
- **文件夹模式需要 Chrome / Edge 等支持 File System Access API 的浏览器。**
  Firefox / Safari 会退回到「下载单个 ZIP」。这是浏览器能力差异，不是配置问题。
- **网页看不到完整绝对路径。** 面板只能显示你选的文件夹名称，见上文说明。
- **PPT 图片是平台采样帧**，不是原始 `.pptx`。分辨率与平台一致，无法更高。

---

## Development

```bash
# 安装开发依赖
pip install -e ".[dev]"

# Python 端：260 个用例
python -m pytest -q

# Userscript 端：91 项检查（必需，别跳过）
cd tests/userscript && npm install && node run.js

# 用真实的保存页面做验证（把 HTML 放到 local_samples/，该目录已被 gitignore）
python -m pytest tests/test_adapter.py::TestRealSample -v
python -m cli.main inspect "local_samples/课堂实录.html"
```

`python -m pytest` 会自动带上 userscript 运行时测试；没有 Node 或 jsdom 时会跳过。

### 为什么 userscript 必须在真实 DOM 里跑

`node --check` 只能查语法，查不出运行时行为。这个项目**曾经因此发布过一个会让页面
完全卡死的版本**：面板的 `refresh()` 写入 DOM，而 `MutationObserver` 监听
`document.documentElement` 并在回调里再次调用 `refresh()` —— 无限微任务循环，
页面再也不把控制权交还给事件循环。因为 `@match` 覆盖整个站点，连课程列表和登录页
都一起卡死，**用户根本进不去播放页**。

`tests/userscript/harness.js` 用 jsdom 加载**真实的** userscript，配合匿名化 fixture
和 Tampermonkey API stub 执行它。卡死被检测为进程超时（卡死的页面永远不会 yield）。
它还断言导出的 manifest 内容正确、签名 URL 已脱敏、以及**课次切换失败会被如实上报
而不是静默错配**。

`tests/test_userscript_runtime.py` 里有一个自检用例：喂给 harness 一个故意死循环的脚本，
确认它真的能发现——防止这套检测机制本身失效。

**由此得到的设计规则：永远不要 `observe` 自己会写入的子树。**

### 测试分层

| 层 | 数量 | 覆盖什么 |
| --- | --- | --- |
| `tests/test_{adapter,html_parser,manifest,packer,ppt,transcript}.py` | 219 | DOM 解析、adapter、manifest、PPT 对齐、打包、CLI（**与本轮改动前完全一致**） |
| `tests/test_userscript_core_frozen.py` | 17 | 用 SHA-1 钉住 userscript 的采集核心，防止误改 |
| `tests/userscript/unit.js` | 42 | 纯函数：内容筛选、路径规划、ZIP 写入、Markdown 渲染 |
| `tests/userscript/run.js`（DOM 用例） | 49 | 真实 jsdom 执行：UI 结构、进度区、两种保存方式、批量导出 |
| `tests/test_userscript_runtime.py` | 24 | 驱动上面两套，并用 Python `zipfile` **独立校验**产出的 ZIP |

ZIP 是**手写**的（无第三方依赖），所以用 CPython 的 `zipfile` 重新打开并校验每个
CRC32 —— 两个独立实现互相印证，而不是自己证明自己。

### 项目结构

```
SJTU-Course-Exporter/
├─ userscript/sjtu-course-exporter.user.js   # 浏览器端：采集 + UI + 保存
├─ adapters/
│  ├─ dom.py          # 零依赖 HTML DOM + CSS 选择器子集
│  ├─ selectors.py    # ★ 所有选择器与接口路径的唯一来源
│  ├─ textutil.py     # 时间戳、保守 normalization
│  └─ sjtu_replay.py  # SJTUReplayAdapter：页面 → 数据模型
├─ cli/
│  ├─ main.py         # argparse CLI
│  ├─ manifest.py     # schema / 校验 / 敏感信息关卡
│  ├─ transcript.py   # SRT
│  ├─ ppt.py          # PPT × 语音对齐
│  └─ packer.py       # Markdown / index / prompt / 输出目录
├─ docs/
│  ├─ architecture.md
│  └─ reverse-engineering-notes.md   # ★ 真实逆向记录（14 节）
├─ examples/manifest.example.json    # 匿名化示例
├─ tools/fetch_platform_bundles.py   # 可复现的 bundle 分析工具
└─ tests/
   ├─ test_*.py                      # 260 个 Python 用例
   ├─ fixtures/sample_page.html      # 匿名化结构 fixture
   └─ userscript/
      ├─ unit.js                     # 纯函数单测（内容筛选 / 路径 / ZIP）
      ├─ harness.js                  # 在 jsdom 里真实执行 userscript
      └─ run.js                      # 驱动所有用例并输出 91 项检查
```

### userscript 的分层

采集核心（section 0–5）与交互层是分开的，重构 UI 时**没有改动任何提取逻辑**：

| section | 内容 | 本轮是否改动 |
| --- | --- | --- |
| 0–5 | 选择器、工具、metadata 解析、**提取器**、等待策略 | ❌ 逐字节保留 |
| 6 | 输出规划（内容筛选 + 路径规划，纯函数） | ✅ 新增 |
| 7 | ZIP 写入器（手写，无依赖） | ✅ 新增 |
| 8 | Markdown / SRT 渲染（支持内容开关） | ✅ 改造 |
| 9 | 保存：File System Access API + IndexedDB + ZIP 回退 | ✅ 新增 |
| 10–11 | 设置 / 状态 / 面板 UI | ✅ 重写 |
| 12 | 编排：范围选择、抓取循环、保存 | ✅ 改造（复用 `captureOne`） |
| 13–14 | Boot / 测试接缝 | ✅ 保持 + 测试接缝 |

`tests/test_userscript_core_frozen.py` 用 SHA-1 钉住 section 0–5，任何误改都会让测试失败。

设计要点、分层原因与扩展方式见 [`docs/architecture.md`](docs/architecture.md)。
页面结构、接口线索与「哪些还没验证」见
[`docs/reverse-engineering-notes.md`](docs/reverse-engineering-notes.md)。

### 想帮忙？

最有价值的贡献是**在线验证内部接口**。请打开课程播放页 →
DevTools → Network → Fetch/XHR，然后按
[`docs/reverse-engineering-notes.md`](docs/reverse-engineering-notes.md) §14
的清单记录请求。**这一条能把整个工具从「点页面」升级成「调接口」。**

---

## License

[MIT](LICENSE)
