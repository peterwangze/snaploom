# 截图和贴图工具设计与验收

以 2026 年 11 月 11 日可查的 Snipaste 2.11.3 桌面版为基线，覆盖 Free 与 Pro 的用户能力。另以《Snipaste 功能调研》逐条追踪功能细节和平台差异。截屏、标注、贴图、历史、白板、文字提取及安装在断网时均应可用。需要网络的第三方 OCR 服务在显式选用后才收到图像。 [产品官网](https://www.snipaste.com/) · [官方版本记录](https://www.snipaste.com/download.html)

## 用户操作

| 场景 | 路径 | 验收 |
| --- | --- | --- |
| 首次启动 | 主页和托盘菜单直接展示截图、贴剪贴板、历史、设置。旁边显示当前有效快捷键。首次调用截图或窗口检测时，结合该功能解释系统权限。 | 无需注册即可完成第一张截图。拒绝权限后可继续打开图片和标注。再次触发时可恢复授权。键位被占用时保留旧设置，指出冲突并允许重新绑定。 |
| 快速截屏 | 保留 F1 截图和 F3 贴图默认键位，允许重绑。默认冻结当前桌面，移动鼠标可预选窗口或控件，拖动即可框选。选区显示像素尺寸。Enter 完成复制，Esc 退出。工具栏直接提供复制、置顶、保存、取消。 | 默认键位从其他应用生效。用鼠标或键盘都能完成区域截图并复制。成功后焦点回到原应用。快速连按热键不会叠加覆盖层或覆盖当前编辑。 |
| 精确截屏 | 用方向键逐像素移动选区，用调整模式改变选区尺寸。提供区域、窗口、当前屏幕、全部屏幕、上次区域、固定尺寸、比例锁定、延时、游标、刷新与实时截图。框选时放大镜显示取样点和颜色。 | 100%、125%、150%、200% 混合缩放、竖屏、负坐标和热插拔可重复测试。输出尺寸与标尺一致。全屏和窗口输出支持按设置排除本应用或游标。 |
| 标注与导出 | 紧凑工具栏先显示常用工具，可展开样式和扩展工具。保留标注对象可选、可改、可隐藏、可撤销重做。提供矩形、椭圆、多段线、箭头、手绘、高亮、文字、步骤编号、马赛克、模糊、橡皮、放大、白板和组合编号。保存、复制、复制为文件、自动保存、快速保存、系统共享和自定义输出使用同一渲染结果。 | 鼠标拖拽与键盘导航均可选工具。高亮覆盖文字仍可阅读。实色遮盖导出后被覆盖的像素已替换。复制和文件导出具有相同裁剪与标注。IME 可输入中日文。取消保存保留编辑。 |
| 贴图 | 在截图工具栏直接置顶，或通过 F3 读取剪贴板的图片、普通文字、富文本、颜色和本地图片文件。贴图提供拖动、缩放、透明度、旋转、镜像、点透、缩略图、取色、GIF 帧速/暂停/逐帧、编辑、置顶和恢复。 | 创建或显隐贴图时原应用可继续打字。进入编辑时按需获取焦点，退出后归还焦点。点透后能在托盘一键恢复交互。系统窗口关闭请求与贴图菜单关闭具有一致结果。 |
| 管理贴图与截图 | 历史显示缩略图、时间、尺寸和组，支持回到选区、复制、再次编辑、置顶与删除。贴图管理支持分组、当前组/全部显隐、暂时独显、备份恢复、虚拟桌面与找回屏幕外窗口。 | 重启恢复贴图和组。断开再接回副屏可恢复原布局。保存失败时保留原设置。取消关闭或销毁整组保留所有成员。可关闭截图历史并清空已有记录。 |
| 辅助流程 | 提供多语言界面、启动与便携配置、主题字体与托盘图标、角落触发、快捷键搜索、预设命名和区域、CLI 自动化、离线 OCR、二维码和条形码识别。 | CLI 通过单实例进程调用截图和贴图，阻塞模式正确区分成功、超时、取消和系统失败。网络断开后 OCR 与扫码仍可使用。扫码结果需要主动复制或打开，链接保留原文以便核对。 |

键盘焦点和图标说明使用原生或具备语义的控件。截图框移动、尺寸变化和导出完成提供可读状态。亮色、暗色、高对比度模式均使用状态标识和文字说明。设置页显示快捷键是否实际绑定及触发条件，支持以动作名称检索。管理员权限、截屏权限和系统级窗口可见性由平台诊断给出明确状态。

## 根据用户反馈改进

- Windows 混合 DPI 截图出现放大或偏移的报告。将逻辑坐标、实际像素和每屏缩放分别保存，在每块屏幕上裁剪再拼接。回归夹具覆盖左侧和上方的负原点、主屏切换、不同缩放和旋转。 [混合 DPI 反馈](https://github.com/Snipaste/feedback/issues/3794) · [Windows DPI 感知](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process)
- 有用户报告取景放大镜卡顿。首帧缓存于内存，移动鼠标时取样现有帧。窗口探测在独立任务中执行，连续事件合并，先显示区域框，探测超时仍可框选。按录屏或事件计时记录打开延迟和拖动丢帧。 [放大镜反馈](https://github.com/Snipaste/feedback/issues/3796)
- 贴图显隐抢占键盘焦点与标准关闭事件失效是两条独立回归场景。保持浮窗显示与键盘激活分开。鼠标穿透与键盘焦点分开保存，保留退出穿透的全局路径。 [macOS 贴图焦点](https://github.com/Snipaste/feedback/issues/3801) · [Linux 贴图焦点](https://github.com/Snipaste/feedback/issues/3778) · [Linux 标准关闭](https://github.com/Snipaste/feedback/issues/3798)
- macOS 通知中心和 Linux Wayland 窗口探测失败分别验证。前者过滤系统叠层和本应用窗口。后者使用合成器的截图入口，根据当前会话报告支持的能力，并提供系统选区和本应用裁剪。 [macOS 通知中心](https://github.com/Snipaste/feedback/issues/3795) · [KDE 原生窗口](https://github.com/Snipaste/feedback/issues/3800) · [niri 截图](https://github.com/Snipaste/feedback/issues/3806)
- 保存设置时采用版本化数据和原子替换。程序崩溃、升级、配置损坏、系统禁止写入均有可验证的恢复路径。 [macOS 设置丢失](https://github.com/Snipaste/feedback/issues/3785)
- HDR 截屏使用有定义的源色彩空间和 SDR 映射。用梯度、肤色、已知 RGB 色块以及 HDR 和 SDR 混用的显示器回归测试，分别验证导出与取色。 [HDR 偏色反馈](https://github.com/Snipaste/feedback/issues/3809)
- 滚动截图是用户长期请求的增强项。设计为独立模式，预览拼接接缝，遇到重复纹理或固定页眉时允许调整拼接点。按人工滚动采集的路径可沿用授权的截图接口。 [上游滚动截图讨论](https://github.com/Snipaste/feedback/issues/19)

## 实现结构与系统接口

应用主进程处理单实例、热键、托盘、权限、窗口、存储、CLI 和导出。捕获适配器输出逐屏像素缓冲、逻辑坐标、像素尺寸、缩放、色彩空间及可用能力。编辑模型保存底图、裁剪与矢量操作，屏幕预览、剪贴板和导出共用合成逻辑。贴图以独立窗口承载图像，通过内部事件同步位置、组与显隐状态。截图历史元数据和图像独立写入，完成提交后再清理旧数据。读取剪贴板只在用户调用贴图、显式启用相关功能或主动导入时发生。富文本以受限样式渲染，外部图片、脚本和远程字体不执行也不下载。

| 环境 | 捕获和快捷键 | 贴图和焦点 |
| --- | --- | --- |
| Windows | 进程使用 Per-Monitor V2。对普通桌面捕获可使用 GDI 或 Desktop Duplication。窗口捕获选择 Windows Graphics Capture 等可用路径。以系统窗口边界和 UI Automation 获取检测候选。注册系统热键并回报注册结果。 | 置顶窗口采用非激活显示。穿透、透明度和点击焦点独立控制。验证多显示器、混合 DPI、虚拟桌面、睡眠唤醒和资源管理器重启。 |
| macOS | 使用 ScreenCaptureKit 获取用户授权的屏幕或窗口。只有请求控件层级检测时才提示辅助功能权限。高分屏由逻辑点与像素缓冲比例映射。 | 浮窗非激活显示，编辑时显式切换。验证 Spaces、全屏应用、菜单栏、睡眠唤醒和外接屏恢复。 |
| Linux X11 | 使用 X11 根窗口捕获、RandR 输出布局、EWMH 窗口层级。可用时借助 AT-SPI 探测控件。验证键位冲突、窗口管理器关闭、工作区和剪贴板所有权。 | 通过 EWMH 请求置顶和窗口状态，按 X11 输入区域实现穿透。用两个不同窗口管理器验证焦点和关闭。 |
| Linux Wayland | 优先走 XDG Screenshot 或 ScreenCast/GlobalShortcuts portal，按实际接口版本和目标掩码启用窗口、区域等功能。用户完成系统选取后可继续精裁、标注和导出。热键不受 portal 支持时保留托盘入口和桌面环境可绑定的 CLI。 | 合成器管理任意窗口位置、层级及焦点。可用时接入相应层协议以支持置顶。标准窗口、贴图管理与导出路径可独立使用。能力状态按 GNOME、KDE、wlroots/niri 版本实测记录。 |

Wayland portal 的截图目标、全局热键和捕获方式由实际后端提供。原生窗口枚举与覆盖层权限也依合成器而定。在功能矩阵中分别记录已支持、替代路径和当前系统未暴露能力。受保护内容和安全桌面按操作系统规则处理。 [XDG Screenshot](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Screenshot.html) · [XDG GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html) · [XDG ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html) · [ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit) · [Windows GDI 捕获](https://learn.microsoft.com/en-us/windows/win32/gdi/capturing-an-image) · [EWMH 规范](https://specifications.freedesktop.org/wm/latest-single/)

## 开发与交付顺序

1. 锁定对标版本并建立功能台账，先跑通三平台安装、热键、抓屏、贴图和权限探测的小型样例，再确认跨平台框架及本地适配方式。
2. 构建贯穿截图、裁剪、标注、复制、保存和贴图的正常入口。以混合 DPI 截图和不抢焦点贴图验证系统适配。
3. 完成基础版和专业版其余功能，逐项录入实现入口、平台差异与测试证据。覆盖设置迁移、历史与组备份、文件/剪贴板格式、GIF、白板、CLI、OCR、扫码和自定义输出。
4. 用可复现测试和真实桌面会话完成回归。每个计划分发的安装包都在相应平台全新环境测试。保存已验证的系统版本、架构、显示设置和包的 SHA-256。
5. 从同一 Git 提交生成安装包、版本记录和校验文件。以版本标签发布到用户指定的 Git 仓库，在对应 Release 下载后验证文件名、大小、校验与可打开性。

## 离线安装验收

| 目标包 | 断网验收场景 |
| --- | --- |
| Windows 全量安装程序 | 按实际发布架构分别验证 x64、ARM64、x86。普通用户安装、启动、截图、复制到系统应用、卸载。校验应用版本与架构、签名状态和更新覆盖安装。 |
| macOS 磁盘映像 | 通用二进制或分别覆盖 Intel 与 Apple Silicon。通过 Finder 将应用安装到 Applications 后，在断网新用户环境通过默认 Gatekeeper 并完成捕获权限流程。Developer ID 签名与 Apple 公证票据应随分发文件装订。 |
| Linux AppImage | 在承诺支持的发行版与架构上测试双击或命令启动。覆盖 GNOME/KDE 的 Wayland、至少一个 wlroots 系会话和 X11。记录 FUSE 运行或提取运行路径与系统依赖。额外发布的发行版包也做单独安装验证。 |

断网测试从已下载的完整文件开始，检查安装器无需再取运行时或 OCR 模型。Linux 测试记录系统是否已有 Wayland portal、合成器或 X11 运行库。Windows 和 macOS 包通过平台各自的签名检查工具，Linux 包记录构建环境和校验值。下载资产与源代码使用同一版本号，Git Release 页明确列出实测平台与已知系统限制。 [Apple 分发打包](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution) · [Apple 公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
