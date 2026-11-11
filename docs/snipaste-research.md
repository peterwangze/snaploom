# Snipaste 调研与功能验收清单

调研日期为 2026 年 11 月 11 日。参考版本为 Snipaste 2.11.3，该版本发布于 2026 年 1 月 18 日。官网提供 Windows x64、x86 和 ARM64 便携版，macOS Universal 包，以及 Linux x86_64 AppImage。以下范围包含文档中的免费版与 Pro 功能。平台专属功能按平台验证。此表定义验收目标，实际实现和测试结果须逐项核对。 [官方下载与发布记录](https://www.snipaste.com/download.html) · [免费版与 Pro 功能表](https://github.com/Snipaste/feedback/wiki/PRO)

## 保留的交互

- 托盘启动后随时可用。F1 进入截图，拖选或点击识别到的窗口，Enter 复制，F3 把剪贴板内容贴成浮窗。截图工具栏直接提供复制、保存、贴图和取消，避免每次打开主窗口。Windows 和 Linux 使用 Ctrl，macOS 对应 Cmd。 [快速开始](https://github.com/Snipaste/feedback/wiki/Getting-Started) · [快捷键](https://github.com/Snipaste/feedback/wiki/Key-Bindings)
- 选区与标注共用一个操作界面。可用鼠标快速完成，也保留逐像素调整、工具快捷键、取色和历史切换。贴图可拖动、滚轮缩放、双击收起、分组和穿透。高频功能配可见按钮和快捷键提示。 [快捷键](https://github.com/Snipaste/feedback/wiki/Key-Bindings) · [高级技巧](https://github.com/Snipaste/feedback/wiki/Advanced-Tips)
- 截图编辑、贴图状态与导出共用像素和标注数据。刷新截图保留标注，历史可重新编辑，图片复制、另存、打印与自动化按同一输出生成，避免屏幕看到的结果与导出内容不一致。 [Pro 功能](https://github.com/Snipaste/feedback/wiki/PRO) · [命令行参数](https://github.com/Snipaste/feedback/wiki/Command-Line-Options)

## 功能矩阵

| 编号 | 参考范围 | 交互与验收 |
| --- | --- | --- |
| C01 | 免费 · 区域、窗口与控件截图 | F1 或托盘进入，拖选或点击窗口与控件，Tab 切换探测模式。显示选区尺寸，拖动边和角调整，Space 配合拖动可移动选区。 |
| C02 | 免费 · 全屏、定时、自定义区域与带光标截图 | 可截全部显示器或当前显示器，指定坐标、宽高与延迟，截图时可切换是否包含原光标。倒计时完成后捕获。 |
| C03 | 免费 · 像素控制与取色 | 方向键移动选区，Ctrl/Cmd 与 Shift 修饰方向键改变尺寸，WASD 移动光标。放大镜显示采样、坐标及 RGB/HEX，复制后可作为色卡贴出。 |
| C04 | 免费 · 重用、历史与白板 | R 重用最后的选区，逗号和句号浏览历史。可进入空白画布，选填充色或只覆盖当前显示器。 |
| C05 | 免费 · 截图输出 | 复制、对话框保存、快速保存、自动保存、打印和贴图。支持文件名模板，保存成功和失败均有状态。 |
| C06 | Pro · OCR、二维码和条码 | 从选区及图片提取文本、预览和复制。断网时仍能识别内置语言。识别二维码及常见一维码，展示原文并由用户决定是否复制或打开。 |
| C07 | Pro · 快捷截图与窗口 | 按住设定修饰键并拖选可触发超级截图。直接截活动窗口，允许临时关闭遮罩窗口以配合文件对话框及 Windows Listary。 |
| C08 | Pro · 层级、尺寸与比例 | 在有原生辅助功能支持时可在父子控件之间切换。编辑选区内可输入宽高，显示像素或 DIP，可固定比例并保持最后一次设定。参考版明确不在 Linux 提供层级探测。 |
| C09 | Pro · 刷新与输出样式 | 保持原选区和标注刷新背景，支持圆角、边框、阴影、透明白板，使用配置的应用、Windows 系统分享或自定义输出。测试导出 Alpha。 |
| C10 | 2.11 · 显示兼容性 | 用横屏、竖屏、负坐标、100%/125%/150%/200% 缩放和 HDR 输出验证尺寸、选区、取色与颜色。休眠恢复或接拔显示器后可重新截取。 |
| A01 | 免费 · 基础标注 | 矩形、椭圆、直线、折线、箭头、画笔、荧光笔、马赛克、模糊、文字和橡皮擦。可改颜色、透明度和线宽，Shift 约束成正方形、圆或常用角度。撤销、重做和清空。 |
| A02 | Pro · 标注样式 | 支持编号、局部放大、圆角矩形、虚线、椭圆或圆形标记与模糊范围，双向与不同箭头样式，文字背景和可调描边。文字及适用图形可旋转。 |
| A03 | Pro · 非破坏性编辑 | 自由选择对象、移动和调整、删除、复制和再编辑，可保留 1–5 个线宽、按工具记忆颜色、拖拽排序色板、滚轮调透明度及导入导出色板。 |
| P01 | 免费 · 剪贴板与文件贴图 | 支持位图、纯文本、HTML 富文本、RGB/HEX 色值、图片文件及文件路径文本。可把原文本复制回来、用剪贴板替换浮窗、拖入文件，验证透明 PNG 和 GIF 动画与暂停。 |
| P02 | 免费 · 浮窗操作 | 移动、缩放、透明度、90° 旋转、水平/垂直翻转、固定尺寸缩略、取色、注释、保存与打印。支持靠齐、临时隐藏、关闭后恢复与明确销毁。鼠标穿透可从快捷键或托盘恢复。 |
| P03 | 免费 · 分组与持久化 | 新建和切换分组，隐藏或显示全部浮窗，重启恢复可见性、位置和大小，提供离屏窗口恢复。 |
| P04 | Pro · 进阶浮窗 | 置顶开关、自由选区缩略图、裁剪、灰度、反色、数字输入大小与缩放、透明或深浅棋盘背景、可编辑色卡。 |
| P05 | Pro · 多窗口管理 | 多选并批量移动、缩放设置、关闭或导出。支持独显部分贴图，恢复其他贴图。分组可添加、删除、重命名和排序，验证系统虚拟桌面行为。 |
| P06 | Pro · 网页图像 | 从浏览器复制或拖入图片。剪贴板有位图时直接使用，只有远程 URL 时经用户操作获取，离线时提供明确反馈。 |
| U01 | 免费与 Pro · 配置 | 系统主题、开机启动、托盘鼠标行为、全局与应用内快捷键自定义、按应用禁用、放大镜样式与隐藏、热角、截图提示音、辅助线、输出与历史策略。 |
| U02 | Pro · 剪贴板与系统集成 | 同时复制图像数据和文件引用，可粘贴到文件管理器生成文件。Windows 专属命令还可向前台应用粘贴为纯文本或图像。 |
| U03 | 免费与 Pro · CLI | 支持全屏/活动窗口/活动屏/坐标尺寸/上次/自定义捕获，延迟、阴影、保留编辑、阻塞与超时，输出到文件、剪贴板、OCR、打印、贴图、外部程序，支持一次多个输出、静默或跳过自动保存。 |
| U04 | 免费与 Pro · 远程控制驻留实例 | CLI 可传文本、HTML、图片文件及贴图坐标，管理分组、可见性和穿透，控制白板、扫码、取色、热键、热角和历史，打开设置、帮助和退出。 |

操作细节取自 [产品功能介绍](https://www.snipaste.com/) · [快速开始](https://github.com/Snipaste/feedback/wiki/Getting-Started) · [快捷键](https://github.com/Snipaste/feedback/wiki/Key-Bindings) · [Pro 功能](https://github.com/Snipaste/feedback/wiki/PRO) · [命令行选项](https://github.com/Snipaste/feedback/wiki/Command-Line-Options) · [高级设置](https://github.com/Snipaste/feedback/wiki/Advanced-Configs) · [更新日志](https://www.snipaste.com/download.html)。

## 用户报告与改进验收

| 反馈 | 产品改进 | 验收 |
| --- | --- | --- |
| Windows 混合缩放导致预览放大，Linux 150% 缩放导致截图模糊。 | 按显示器维护逻辑坐标与原生像素映射，截图和取色直接读取原生像素。 | 100% 横屏和 150% 竖屏混接，标定边框及单像素色块，验证尺寸、取色、反向拖动及跨屏。 [Windows #3794](https://github.com/Snipaste/feedback/issues/3794) · [Wayland #3275](https://github.com/Snipaste/feedback/issues/3275) |
| GNOME Wayland 出现闪烁、卡顿，KDE Wayland 偶发截图入口无响应。 | 异步申请系统截图，识别桌面提供的能力，区分拒绝、超时和服务不可用。 | 同一登录会话反复截图，测试权限拒绝、门户重启、显示器布局变化，GUI 保持可响应。 [GNOME #3719](https://github.com/Snipaste/feedback/issues/3719) · [KDE #3225](https://github.com/Snipaste/feedback/issues/3225) |
| 热键失效时排查困难，文字框吞掉字母和数字。 | 显示当前注册状态及修改入口。输入文字和 IME 组合时暂停画布单键绑定。 | 占用 F1 后从托盘启动，修复配置并重试。在文字框输入 T/M/B/1/2/3 和中文。 [热键 #3643](https://github.com/Snipaste/feedback/issues/3643) · [标注输入 #3770](https://github.com/Snipaste/feedback/issues/3770) |
| Linux 浮窗影响键盘焦点，透明浮窗难以找到或关闭。 | 默认创建浮窗不抢输入焦点，托盘或管理器可恢复点击、移回屏幕或关闭。 | 编辑器输入过程中贴图，键入可继续。为贴图开启完全穿透或移除所在显示器，仍可恢复。 [焦点 #3778](https://github.com/Snipaste/feedback/issues/3778) · [透明浮窗 #3772](https://github.com/Snipaste/feedback/issues/3772) |
| 用户请求关闭截图历史，担心识别质量及云端 API 的隐私。 | 支持零持久化的历史选项，本地 OCR 默认处理；网络识别属于独立明确启用的操作。 | 断网识别中英文。关闭历史后检查应用数据目录，取消或失败的截图不留磁盘副本。 [历史 #3774](https://github.com/Snipaste/feedback/issues/3774) · [OCR #3777](https://github.com/Snipaste/feedback/issues/3777) |
| macOS 屏幕录制与辅助功能权限分别影响截图和控件识别。 | 在对应功能首次调用时显示权限状态、系统设置入口和重试。 | 首次启动、拒绝、授权、撤销、重启后均有明确状态。 [Snipaste 故障排除](https://github.com/Snipaste/feedback/wiki/troubleshooting) · [Apple ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit) |
| 文件保存流程难以发现「复制图像为文件」。 | 输出菜单提供明确的复制图片与复制文件操作。 | 在 Explorer 或同类文件管理器的目标目录中粘贴，验证文件名和图像内容。 [文件粘贴 #3788](https://github.com/Snipaste/feedback/issues/3788) |

滚动长截图属于官方持续追踪的需求。GIF/MP4 录制也见于功能请求，参考版支持的是已有 GIF 的贴图播放。长截图可以作为体验改进单独验收。 [滚动截图 #19](https://github.com/Snipaste/feedback/issues/19) · [录屏请求 #3141](https://github.com/Snipaste/feedback/issues/3141) · [产品更新日志](https://github.com/Snipaste/feedback/wiki/Changelog)

## 实施和发布顺序

1. 核心桌面流程以托盘、快捷键、捕获、画布、剪贴板、保存和贴图闭环为基础。统一状态处理，允许取消并回到原应用。窗口/控件识别、合成与裁剪在平台适配器集中处理。
2. 注释保持可编辑数据模型，导出时光栅化。独立实现历史与浮窗组的持久化，明确恢复、删除和离屏处理。最后接入 OCR、扫码、自动化、系统分享和设置。
3. Windows 测 Win32 原生坐标、鼠标捕获与 UI Automation。macOS 测屏幕录制权限、Retina 和多屏，控件探测由 Accessibility 提供。Linux 分开验证 X11 和 Wayland。Wayland 的可选能力按桌面门户和合成器检测，截图授权取消后有可见反馈。 [Windows UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-screenscaling) · [Apple 屏幕捕获](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos) · [XDG Screenshot](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Screenshot.html) · [XDG GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)
4. 各平台产物在原生构建环境生成，对应平台执行安装、断网启动、截图标注保存、贴图、离线识别和卸载或清理验证。发布版本需要可下载的 Windows、macOS 和 Linux 离线包、每个文件的 SHA-256，以及与构建对应的 Git 标签和发布记录。
