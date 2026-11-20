<div align="center">

<img src="ui/icon.png" width="160" alt="MaaShinyColors">

# MaaShinyColors

《偶像大师 闪耀色彩》Enza 网页版自动化工具

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078d4)](#下载与使用)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Release](https://img.shields.io/github/v/release/Anntinomy4095/MaaShinyColors)](https://github.com/Anntinomy4095/MaaShinyColors/releases/latest)
[![MaaFramework](https://img.shields.io/badge/Powered%20by-MaaFramework-ff69b4)](https://github.com/MaaXYZ/MaaFramework)

</div>

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework)。游戏运行在程序自带的窗口中，任务在右侧面板选择。当前为 0.1.0 测试版，游戏更新后部分页面可能需要适配。

## 下载与使用

可运行版本只在 [Releases](https://github.com/Anntinomy4095/MaaShinyColors/releases/latest) 提供（`MaaShinyColors.zip`）；页面上的 Source code 压缩包是源码，不能直接运行。

1. 从 [Releases](https://github.com/Anntinomy4095/MaaShinyColors/releases/latest) 下载 `MaaShinyColors.zip`，解压到可写目录（如 `D:\MaaShinyColors`，不支持 `Program Files`）。
2. 运行 `MaaShinyColors.exe`，在窗口中登录游戏。
3. 回到游戏首页，在右侧面板勾选任务，点击「开始」。

运行环境：Windows 10/11 x64、WebView2 Runtime。程序未签名，SmartScreen 提示时选择「仍要运行」。

首次使用：

- 首页「第一次使用」第 1 步点击「检查」，确认已回到首页。
- 商店、训练、Fes、营业、Produce 首次打开时有游戏教程，需先在游戏内看完。
- 推荐编队与自动编队需先在「资源」页下载卡资料并扫描持有卡牌。

「设置 → 窗口 → 后台运行」可随时开启或关闭。开启后，最小化会隐藏到托盘，点击托盘图标还原。

## 免责声明

- 本项目为免费开源的非官方工具，仅供学习交流，与 Bandai Namco Entertainment 及 enza 无关。
- 游戏名称、角色、画面及素材的权利归 ©Bandai Namco Entertainment Inc. 等原权利人。仓库不含游戏素材，识别模板仅随发布包提供。如权利人要求，将删除相关内容。
- 使用自动化工具可能违反 [enza 利用规约](https://enza.fun/menu/terms)，导致账号受限或停用，风险由使用者承担，详见 [安全与风险](docs/SAFETY.md)。
- 本项目按原样提供，不对功能可用性、运行结果及由此造成的损失负责。
- 本项目不收费，任何出售或代练行为与本项目无关。
- 禁止用于绕过反作弊或验证、干扰游戏服务等用途，同时适用 [MaaFramework 免责声明](https://github.com/MaaXYZ/MaaFramework#免责声明)。

## 功能

在右侧面板勾选任务后点「开始」，按顺序依次运行。

**日常（任务页）**

| 任务 | 内容 |
| --- | --- |
| 启动到首页 | 关闭登录奖励、公告等弹窗，回到首页 |
| 礼物箱 | 一括受取 |
| マニー商店 | 买当天「本日限定」折扣商品（默认跳过券、金训练券） |
| S 卡训练 | 用基础铜训练券 ×1；未选卡时自动选编队里未满级的 S 卡 |
| Grade Fes／彩排 | 按设定局数打 |
| Match Live | 推进下一关（输了按设定重试），剩余参加权跳过已通关的最新一关 |
| Collabo Fes | 打所选会场的 Hard，自动升到能打的最高等级，输了降一级；会场满员时等待 |
| 任务奖励 | デイリー、ウィークリー一括受取 |
| 营业 | 收取完成的营业，按原队伍再营业 12 小时 |
| 每日任务进度、资源 | 只读：任务达成数、マニー、体力、Fes 参加权 |

**Produce（Produce 页）**

| 功能 | 内容 |
| --- | --- |
| W.I.N.G. | 自动育成：每周行动、试镜、SP 技能；有进行中的一局时先核对再继续 |
| say "Halo" | 掘书线，围绕所选ノウハウ目标 |
| 周常 Auto Produce | 设置「オート1」（剧本、编队、育成方針、带书）后开始；编队可自动或指定 |

**编队与资料（资源页）**

- 卡资料下载，持有卡牌扫描（等级、凸）
- W.I.N.G. 推荐编队，周常 Auto 自动编队

**ノウハウ（ノウハウ页）**

- 书库扫描，按目标词条筛选新书

**其他**

- 运行中显示当前步骤，停止时在首页与任务行显示原因
- 后台运行（最小化到托盘）、存储清理、检查更新

规则说明：[自动编队](docs/AUTO_TEAM.md) · [自动 Produce](docs/AUTO_PRODUCE.md) · [安全与风险](docs/SAFETY.md)

使用限制：

- 不消耗ジュエル、付费货币和付费道具，不抽卡。
- リカバリーソーダ仅在周常 Auto 开启「ループ」时使用，有数量上限，默认关闭。
- 不读取账号、密码或 Cookie，不注入脚本，不重放网络请求，不处理验证码。
- 识别结果不确定时停止，消耗资源的操作不重试。

## 问题反馈

- 停止原因与后续操作显示在首页提示区和对应任务行。「画面与已知布局不一致」通常表示游戏页面已更新。
- 在 [Issues](https://github.com/Anntinomy4095/MaaShinyColors/issues) 提交问题，附上 `dev_assets/private/run_logs/diagnostics/failures/` 中的截图，并遮盖制作人名与 ID。
- `dev_assets/private/` 含登录状态，不要整体上传。

## 本机数据

设置、登录状态、截图与运行记录保存在程序目录下的 `dev_assets/private/`，不会上传。升级时将该文件夹复制到新版本目录。「设置 → 存储」可清理截图和运行记录。

## 数据来源

| 数据 | 来源 | 说明 |
| --- | --- | --- |
| 卡牌资料与缩略图 | [ShinyColorsDB](https://github.com/ShinyColorsDB) | 在「资源」页手动下载，仅存本机，不随仓库或发布包分发 |
| ノウハウ、Match 奖励、周常任务规则 | [wikiwiki.jp/shinycolors](https://wikiwiki.jp/shinycolors/) | 整理为源码中的规则表 |
| Produce 与卡牌规则 | [Shinycolors Wiki](https://shinycolors.wiki/) | 整理为源码中的规则 |
| 支援技能效果公式 | [note.com/ze3rx](https://note.com/ze3rx/n/ndc0cfb2da6ba) | 用于 W.I.N.G. 评分 |
| OCR 模型 | [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（PaddleOCR） | 随发布包提供 |
| 识别模板 | 游戏画面裁剪 | 仅随发布包提供 |
| 版本信息 | 本仓库 GitHub Releases | 手动检查更新时读取 |

游戏画面只在本机截图识别。

## 从源码运行

需要 Windows 10/11 x64、Python 3.12 及以上（x64，打包用 3.14）、.NET SDK 10。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1   # 创建虚拟环境 .venv
.venv\Scripts\Activate.ps1
python tools/fetch_ocr_candidates.py                              # 下载 OCR 模型
python -m unittest discover -s tests -v
python tools/check_project.py
dotnet build browser_host/MaaShinyColors.BrowserHost.csproj -c Debug -o dev_assets/private/host-debug
dev_assets\private\host-debug\MaaShinyColors.BrowserHost.exe --composition-game --enable-pipeline-input
```

识别模板不在仓库中，打包时从发布包读取：`python tools\package.py build --zip --recognition-source <MaaShinyColors.zip>`。

## 许可

代码与文档采用 [MIT](LICENSE) 许可，基于 MaaPracticeBoilerplate 模板。程序图标由作者绘制并保留权利，不适用 MIT，未经许可不得商用。

发布包包含的第三方组件：

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| [MaaFramework](https://github.com/MaaXYZ/MaaFramework)（`maafw`、`MaaAgentBinary`） | 识别与任务流水线 | LGPL-3.0 |
| [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets) OCR 模型（PaddleOCR） | 文字识别 | Apache-2.0 |
| [Microsoft Edge WebView2 SDK](https://www.nuget.org/packages/Microsoft.Web.WebView2) | 游戏与面板窗口 | BSD-3-Clause |
| [.NET 运行时](https://github.com/dotnet/runtime) | 宿主程序 | MIT |
| Microsoft Visual C++ 运行库 | MaaFramework 依赖 | Microsoft Visual C++ Redistributable 许可 |
| [CPython 3.14](https://www.python.org/) 嵌入版 | 引擎运行环境 | PSF-2.0 |
| [NumPy](https://numpy.org/) / [SciPy](https://scipy.org/) | 数值计算 | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | 图像处理 | MIT-CMU |
| [StrEnum](https://github.com/irgeek/StrEnum) | 枚举工具 | MIT |

## 致谢

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)：识别与任务框架
- [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate)：项目模板
- [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)：OCR 模型
- 「数据来源」中列出的各项目与作者
