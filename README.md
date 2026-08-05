# TagCleaner

本地图片打标与 Anima LoRA 训练数据整理工作台。项目核心使用 Python 标准库提供本地 API，前端为原生 HTML / CSS / JavaScript；Pillow 只负责为超大视觉请求创建内存中的缩放 JPEG 副本，绝不修改原图。

## 系统要求

- Windows 10/11 x64。目前的一键安装、DPAPI 密钥加密和桌面快捷方式以 Windows 为目标。
- 64 位 Python 3.10 或更高版本，建议使用 [python.org](https://www.python.org/downloads/windows/) 官方安装包并保留 tkinter。
- 现代浏览器，以及可访问所配置模型供应商的网络环境。
- 运行依赖只有 `Pillow>=10.0,<13.0`，详见 `requirements.txt`；tkinter 随官方 Windows Python 提供，不是 pip 包。

## 一键安装

下载或克隆项目后，双击 `install_tagcleaner.bat`。安装器不需要管理员权限，会：

1. 检查 Python 3.10+；
2. 在项目目录创建隔离的 `.venv`；
3. 安装 `requirements.txt`；
4. 验证 Pillow 与 tkinter，并运行完整单元测试；
5. 创建桌面 `TagCleaner` 快捷方式并启动本地服务。

命令行安装：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_tagcleaner.ps1
```

可选参数：`-NoShortcut` 不创建桌面快捷方式，`-NoStart` 安装后不启动，`-SkipTests` 跳过安装自检，`-Port 8766` 改用其他端口。

## 启动

双击桌面快捷方式或项目根目录的 `start_tagcleaner.bat`。启动器优先使用 `.venv`，确认 TagCleaner API 已就绪后才打开浏览器；若端口被其他程序占用，会明确报错而不会误打开其他服务。

也可以在 PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_tagcleaner.ps1
```

脚本默认监听 `127.0.0.1:8765`，可用 `-Port 8766` 指定其他端口；`-NoBrowser` 仅用于自动化检查时跳过浏览器启动。

不使用安装器时，可手工创建环境并启动：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py --port 8765
```

然后打开 <http://127.0.0.1:8765>。

## 升级与移除

- 更新代码后重新运行 `install_tagcleaner.bat`，安装器会复用 `.venv` 并同步依赖。
- 双击 `stop_tagcleaner.bat` 可安全停止默认端口的后台服务；自定义端口使用 `stop_tagcleaner.bat -Port 8766`。
- 要重建环境，先关闭 TagCleaner，再删除 `.venv` 并重新运行安装器。
- 要移除程序，关闭服务后删除项目目录和桌面快捷方式。API 设置与密钥位于 `%LOCALAPPDATA%\TagCleaner`，需要时可单独删除。

## 使用方式

- 在左侧输入图片文件夹的本地路径并扫描。该方式由本机 Python 服务直接读取原目录，不会复制图片；图片旁的同名 `.txt` 会解析为 tags，并支持在空行后保存一段自然语言描述。
- 点击“选择路径”会打开本机原生文件夹选择器，只返回目录路径并填入路径框；确认后仍需点击扫描。
- 标签词典支持直接编辑翻译键；翻译键持久化在 `.tagcleaner-data/translations.json`。
- 替换条目支持删除、写入、条件三组多 tag 输入，每组都支持“全量 / 任意”模式；规则持久化在 `.tagcleaner-data/replacement-rules.json`。
- 替换规则提供“执行选中”和“执行全部”两个入口；执行全部会忽略当前筛选和勾选，处理已加载目录中的所有图片。
- “保存改动”只回写发生变化图片的同名 `.txt`，不会重写图片二进制。
- 收藏栏和收藏 tag 持久化在 `.tagcleaner-data/favorites.json`。
- LLM 二次清洗按“视觉反推 → A-pose 识别（按需）→ 数据清洗”三阶段执行；视觉反推会同时生成辅助 tags 和一段自然语言视觉描述。清洗阶段以原始 WD14 tags 为主证据，保留其拼写与顺序，再把不重复且有视觉描述支持的 LLM tags 作为辅助候选。每个阶段可以独立选择火山方舟或 Gemini 供应商及模型。普通模式会使用匹配到的角色词典，画风模式会跳过角色描述与审计。
- 批量 LLM 清洗最多同时处理 2 张图片；同目录缓存采用锁内读取、合并、原子替换，避免并发完成时覆盖其他图片的缓存条目。两路上限用于降低供应商长响应时的并发超时概率。
- 运行区会列出全部两路活动任务的图片名、阶段、阶段耗时、请求次数与退避重试状态，并持续记录批次总耗时；默认单次 API 超时为 300 秒，仍可在供应商设置中调整。
- 每完成一张图片，左侧图片列表会立即同步新的 tags 数量、自然语言、缓存状态与未保存状态，并在运行区保留最近完成记录。“自动保存 LLM 结果”默认开启，后端会用临时文件原子替换的方式只写入刚完成图片的同名 `.txt`；关闭后结果仍进入缓存和当前会话，但等待手工保存。
- 右侧“清除 LLM 缓存标记”需要长按至黄色进度走完才会执行；它只移除当前已加载图片的 `.tagcleaner-cache.json` 条目和列表 `L` 状态，不修改 `.txt` 或图片，并会在 LLM 任务运行期间禁用与拒绝请求。
- 项目内 `skills/character-tag-auditor` 负责 A-pose 多模态审计，`skills/prompt-pyramid` 负责最终 tags 归一与排序；两项 Skill 和 sparse/full 审计模式可在 `SKILL PIPELINE` 中配置。
- “画风模式”表示当前数据用于训练画风：开启后不发送角色 A-pose 描述、不执行角色审计，并在最终清洗提示词中强制删除全部画风、艺术家、媒介与技法描述，同时保留人物、外观、服装、姿势、表情、场景、构图等内容信息。
- 画风批次中若所有选中图片拥有同一个首位 tag，且该 tag 不是常见质量、画风或媒介词，系统会将它识别为数据集激活词（例如 `ktks`），在模型提示词和结果校验两层强制保留。
- 关闭“画风模式”时会按角色 tag 查找 A-pose 词典并使用角色外观描述；检测到 `alternate_costume`、`official_alternate_costume` 及对应 outfit 写法时仍会跳过角色外观描述与审计。
- “供应商 / PROVIDERS”页面只保存供应商地址、协议和密钥；“调用 / CALLS”页面分别选择标签识别、A-pose 识别、数据清洗模型。数据清洗默认使用火山方舟 `deepseek-v4-flash`，直接复用火山供应商 Key；旧简写 `deepseekv4flashga` 会自动规范化。
- 模型目录按钮会尝试读取供应商的 `/models` 接口并标记视觉能力；火山 API Plan 当前可能不开放模型目录时，会保留本地已知模型并允许手动输入。标签识别和 A-pose 识别在目录明确标记为文本模型时会阻止提交。
- 自定义提示词支持 `{{tags}}`、`{{vision_tags}}`、`{{merged_tag_candidates}}`、`{{vision_description}}`、`{{protected_dataset_tags}}`、`{{character_descriptions}}`、`{{natural_language_policy}}`、`{{dataset_mode_policy}}` 等运行时占位符；即使旧模板没有这些占位符，后端也会追加原始 WD14 tags、视觉辅助 tags、合并候选、受保护激活词、模式规则及强制 JSON 契约，不会走分叉线路。
- 角色词典持久化在 `.tagcleaner-data/character-descriptions.json`，以 A-pose 图片文件名作为唯一 key。

## LLM 配置与密钥

- 火山方舟默认使用 API Plan 基址 `https://ark.cn-beijing.volces.com/api/plan/v3`，标签识别和 A-pose 默认模型为 `doubao-seed-2.1-turbo`，数据清洗默认模型为 `deepseek-v4-flash`；运行时会按所选协议补全 `/chat/completions` 或 `/responses`。
- 视觉请求默认使用 `HIGH` 精度并将图片按比例限制到 2,000,000 像素；可在供应商页调整 `IMAGE DETAIL` 与 `MAX PIXELS`，以在细节和响应速度之间取舍。
- 原图超过 `MAX PIXELS` 或达到 10 MB 时会生成仅用于 API 请求的高质量 JPEG 内存副本。视觉请求明确超时后，第二、三次请求会再保守降到最多约 160 万、100 万像素，JPEG 质量分别为 88、84；HTTP 限流或服务端错误不会误触图片压缩。原始图片和备份始终不会被改写。该路径需要 Pillow，缺失时会对该图片给出明确错误并继续批次。
- Gemini 默认使用 OpenAI 兼容端点 `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`，默认模型为 `gemini-3.5-flash`；中转站可改为自己的完整地址，也可切换原生 Gemini 协议。
- 非密钥设置保存在 `%LOCALAPPDATA%\TagCleaner\settings.json`。
- API Key 保存在 `%LOCALAPPDATA%\TagCleaner\secrets.json`，Windows 下使用当前用户的 DPAPI 加密，不会写入项目目录，也不会由本地 API 返回明文。
- 也可使用环境变量 `TAGCLEANER_VOLCANO_KEY` 与 `TAGCLEANER_GEMINI_KEY`，环境变量优先于本机密钥文件。
- 本地服务默认只监听 `127.0.0.1`，并不开放跨域 API。
- 自定义供应商地址会接收对应请求和 API Key；只应填写可信的官方端点或可信中转站。

火山方舟图片理解协议参考：[图片理解](https://docs.volcengine.com/docs/82379/1362931?lang=zh)。Gemini 兼容协议参考：[OpenAI 兼容性](https://ai.google.dev/gemini-api/docs/openai)。

## 标签文件格式

仅使用 tags 时保持原格式：

```text
1girl, character_name, blue_hair
```

允许自然语言时使用 tags、空行、描述段：

```text
1girl, character_name, blue_hair

A medium close-up with cool window light from the left.
```

每个包含图片的目录可以生成 `.tagcleaner-cache.json`。缓存会分别记录 `wd14Tags`、`visionTags`、`mergedTagCandidates`、`visionDescription`，以及图片指纹、三阶段供应商/模型路由、模型与提示词配置摘要、Skill 内容哈希、数据模式、角色审计和最终清洗结果；图片、配置、调用路由、画风模式、Skill 内容或审计模式变化后会自动失效。任务启动时会冻结配置快照，避免同一批图片中途切换供应商造成分叉。

## 本地数据与仓库边界

- `.tagcleaner-data/`：翻译、替换规则、收藏和角色词典等当前工作站状态；默认不提交 Git。
- 数据集内 `.tagcleaner-cache.json`：逐图 LLM 缓存；默认不提交 Git。
- `%LOCALAPPDATA%\TagCleaner\settings.json`：非密钥 LLM 设置。
- `%LOCALAPPDATA%\TagCleaner\secrets.json`：当前 Windows 用户的 DPAPI 加密密钥。
- `.venv/`、缓存、日志、临时文件和常见密钥文件均由 `.gitignore` 排除。

## 自检与故障排查

发布前或环境变更后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile server.py llm_service.py tests\test_server.py
```

仓库内的 GitHub Actions 会在 Windows 的 Python 3.10 与 3.14 环境中执行相同的依赖安装、单元测试和语法检查，防止发布版本与一键安装环境分叉。

- 安装器提示找不到 Python：安装 64 位 Python 3.10+，并勾选 Python Launcher 或加入 PATH。
- tkinter 验证失败：使用 python.org 官方 Windows 安装包修改安装，启用 `tcl/tk and IDLE`。
- 端口被占用：运行 `start_tagcleaner.bat -Port 8766`，或关闭占用 `8765` 的程序。
- 页面显示服务未连接：确认终端没有报错，并访问 `http://127.0.0.1:8765/api/health`。
- 超大图片无法压缩：重新运行安装器，确认 Pillow 安装成功；原图不会被修改。
- API 测试失败：检查端点、协议、模型名、网络和 Key；测试按钮不会清除或覆盖已保存 Key。

## 项目结构

```text
server.py                 本地 HTTP API 与数据集状态
llm_service.py            供应商调用、缓存、重试与三阶段 LLM 流程
static/                   原生前端
skills/                   运行时提示词 Skill
tests/                    unittest 与本地监听发包测试
requirements.txt          Python 运行依赖
install_tagcleaner.*      一键安装入口
start_tagcleaner.*        健康检查、启动与自动打开浏览器
stop_tagcleaner.*         校验服务身份并安全停止后台进程
```

## 替换规则语义

- 条件使用原始 tags 判断。
- 删除为“全量”时，删除列表中的所有 tag 都存在才执行；“任意”时任意一个存在即可执行，实际会移除列表中命中的 tag。
- 写入为“全量”时，写入列表中的所有 tag 都不存在才执行；“任意”时只要存在一个缺失 tag 就执行，并只补入缺失项。
- 删除与写入同时存在时，只有删除条件成功，才会继续判断写入。
