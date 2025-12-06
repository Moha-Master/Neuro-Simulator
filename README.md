# Neuro Simulator

***关注Vedal喵，关注Vedal谢谢喵***

*本临时README和所有代码均由AI生成*

## 虽然还是有不少点子，但是近段时间有点忙，暂缓开发💩

Neuro Simulator 是一个模拟 Neuro-sama 直播的项目。  
它通过使用自带的有记忆 Agent，~~也可调用 Letta（一个为 LLM 添加自主记忆功能的项目）~~，以及相同构造的 Chatbot Agent 作为虚拟观众，模拟一场 Neuro-sama 的 Just Chatting 直播。  
它能生成实时的虚拟聊天内容，并通过 TTS 合成语音，提供沉浸式的 Twitch vedal987 频道观看体验。  

## 特性

和其他类似的 AI Vtuber 项目不同，本项目旨在尽可能模拟 Neuro-sama 于 Twitch 上的 Tuesday Stream，因此在 Vtuber 部分不会有太多的自定义部分，也不会考虑引入 Neuro/Evil 以外的 Live2D。  
后续有低成本低性能方法实现 Evil 音色模仿的时候，可能加入 Evil Neuro。

### 预览

*这图是较旧版本的，现在小牛已经和现实中一样换新家了*

演示视频：[哔哩哔哩](https://www.bilibili.com/video/BV1RPsqzrEvS)

<img src="docs/assets/start.gif" width="500" />

### 核心亮点

- **多客户端支持**：支持多个客户端连接，实时广播内容。
- **配置热重载**：通过 Web 控制面板实时修改和热重载配置。
- ~~**双 Agent 模式**：支持 Letta Agent 和内建 Agent，提供更多自定义选项~~ 对 Letta Agent 的支持暂时下线，后续有缘再见。

## 快速开始

1.  **准备外部服务**：确保你拥有必要的 API 密钥，包括 LLM（Gemini/OpenAI）和 Azure TTS；~~如果使用 Letta，也请注册好相关的 API 。~~
2.  **安装服务端**：已上传至 PyPi 作为可用 pip 安装的软件包，你可以用任何一个 pip 安装到全局或 venv 中。
    ```bash
    pip install neuro-simulator
    ```
    推荐使用 pipx，可以在不更改系统 Python 依赖的情况下直接安装为全局软件。

3.  **运行服务端**：
    ```bash
    neuro
    ```
    现在无需手动填写 `config.yaml`，程序在启动时会自动创建一份包含默认设置的配置文件，只需在管理面板中填写和为 Agent 分配 API 服务商即可。
      - 不指定 `--dir, -D` 则自动创建和默认使用 `~/.config/neuro-simulator/` 作为工作目录。
      - 程序会在工作目录下自动生成文件夹结构，拷贝需要的文件到目录内，保持程序包本体只读。

    程序启动后，如果工作正常，会默认在 `http://127.0.0.1:8000` 提供对外服务，你可以在设置中修改这个端口。

4.  **打开管理面板**：现在程序已经内置管理面板，直接在浏览器中打开 `http://127.0.0.1:8000/dashboard/` 即可，内置面板会自动连接到服务端。

    **配置流程**：
      1. 在管理面板中打开“配置”页面。
      2. 在“LLM服务商”中添加一个以上的选项。
      3. 在“TTS服务商”中添加一个以上的 Azure TTS 服务。
      4. 在“Neuro”中分配一个 LLM 服务商和一个 TTS 服务商。
      5. 在“Chatbot”中分配一个 LLM 服务商。

5.  **打开客户端**：现在程序已经内置客户端，在浏览器中访问 `<http协议>://<服务端地址>/` 即可。  
    但是这种方式下自动从哔哩哔哩获取最近回放的功能似乎不工作，需要对哔哩哔哩 API 进行反代。如果你安装了 nodejs，则可以使用 npm 运行开发服务器的方式使用客户端。

更多更复杂或者更简单的使用方式，请参见三个部分的详细文档

## 项目结构（稍微过时，待更新）

```
Neuro-Simulator/
├── server/           # 服务端
├── client/           # 客户端
├── dashboard_web/    # Web控制面板
├── docs/             # 文档和示例文件
│   ├── letta_agents_example/  # Letta Agent 模板示例
│   ├── assets/       # README中使用的媒体文件
│   └── working_dir_example/   # 工作目录示例
└── README.md         # 项目说明文档
```

## 详细文档

有关安装、配置和使用的详细信息，请参阅详细的 README 文件：

- [服务端 README](server/README.md)
- [客户端 README](client/README.md)

## 开发计划和已实现功能

- 服务端
  - [x] Neuro Agent 模块
    - [x] 基于 Letta 的 Neuro Agent
    - [x] 内建的 Neuro Agent
      - [x] 基本的对话和上下文功能
      - [x] 自动和手动记忆管理，包括系统提示、核心记忆、临时记忆
      - [x] prompt 完整内容编辑
      - [ ] 工具调用
        - [x] 内建工具及调用
          - [x] 记忆管理
          - [ ] 直播标题修改
          - [x] 对外发言
          - [ ] 发起投票和查看结果
          - [x] 旋转和缩放模型
          - [ ] 调整 TTS 语速
          - [ ] 禁言指定名称用户
          - [ ] 播放音效
        - [ ] 模块化热插拔工具
        - [ ] 连接到 MCP 服务器
        - [ ] 兼容 Offical [Neuro SDK](https://github.com/VedalAI/neuro-sdk)
    - [ ] 拉起 Evil Agent 并进行对话
  - [ ] Evil Agent 模块，~~卖掉了~~ 待 Neuro Agent 完善、有低成本低性能方法实现 Evil 音色模仿的时候加入
  - [ ] 对 Neuroverse 更多成员的 AI Agent 复现，进而允许 Neuro Agent 向其发送 DM（语音聊天可能不太现实）
  - [x] Chatbot 模块
    - [x] 无状态 Chatbot（即将弃用）
      - [x] Username 的自动生成和预置替换补充
      - [x] prompt 编辑
      - [x] 调用 Gemini 和 OpenAI API 格式的LLM
      - [x] 基于 Neuro Agent 的上一句内容而做出 Reaction
      - [ ] ~~在 prompt 中包含直播标题等更多信息~~ 将在更强大的 Chatbot Agent 中实现
      - [x] ~~实现更长的上下文~~ 将在更强大的 Chatbot Agent 中实现
    - [x] Chatbot Agent
      - [x] 更好的用户名生成逻辑
      - [x] 更长的上下文，包括自身输出和 Neuro Agent 内容
      - [ ] 在 prompt 中包含直播标题、Neuro 和 Twitch 相关背景知识等更多信息
      - [x] 类似 Neuro Agent 的记忆系统，实现有状态 Chatbot Agent
  - [ ] 真实的 Filter 模块，取代 Agent prompt 中的自我 Filtered.
  - [x] 对外 API 接口，包括常规的 http 和 ws 端点
  - [x] Twitch Chat 及管理
    - [x] 普通 Chat
      - [x] 随机池机制，从中抽取消息注入 Neuro
    - [x] 醒目留言 Highlight Messages
      - [x] 队列机制，先进先出
      - [ ] 可选的 TTS Guy
    - [ ] Subs 和 Donations 相关机制
    - [ ] 禁言指定名称用户
  - [x] 配置管理和热重载
  - [x] TTS 合成和推送
    - [x] Azure TTS
    - [ ] 待定 TTS
    - [X] 剔除 Emoji 等特殊字符
  - [x] 进行直播和直播环节
    - [x] Just Chatting 单人直播
      - [x] Neuro Stream
      - [ ] Evil Stream
    - [ ] Karaoke 歌回直播
    - [ ] Twin 闲聊或主题直播
    - [ ] 艺术鉴赏直播环节
  - [x] 多客户端连接和广播
    - [ ] 动态推送开场 Starting Soon 视频
    - [x] 直播阶段
      - [x] Starting Soon
      - [x] 神の上升
      - [x] 常规直播
      - [ ] Ending
    - [x] 直播和聊天室内容
    - [x] 醒目留言 Highlight Messages
    - [x] 直播标题和标签等信息
  - [ ] 在非直播的空闲时间自动获取真实世界中 Neuro 的近期直播内容，更新完善记忆内容（不是微调训练）
  - [ ] 对 Ollama 等本地 LLM 的优化
  - [x] 服务端托管管理面板
    - [x] Web 控制面板
    - [ ] 使用 PyInquiry 的命令行面板
- 客户端
  - [ ] 仅包含直播区域的客户端，适用于希望使用 OBS 或其他软件推流画面的用户
  - [x] 可自定义的用户信息和服务端地址
  - [x] 仿 Twitch 直播界面
    - [x] 响应式设计
    - [x] 主要界面元素
    - [ ] 次级页面
    - [x] Twitch Chat 和其他互动
      - [x] 普通聊天的发送和查看
      - [x] 使用 Bits 和 Channel Points 发送醒目留言 Highlight Messages
      - [ ] 表情发送
      - [ ] Bits & Channel Points 逻辑
      - [ ] 小牛最爱的 Subs 和 Donations 相关逻辑
      - [ ] 参与投票
    - [x] 主播和直播信息
      - [x] 头像、名称信息
      - [x] 动态更新的直播标题、分区、标签
      - [x] 动态更新的人数和直播时长
      - [ ] 下滑的更多主播信息
    - [x] 使用 html 模拟的直播场景
      - [x] 仿真字幕样式
        - [x] 字体和描边效果
        - [x] 逐字跳出效果
        - [x] 随内容量的字号调整效果
      - [x] 仿真 Twitch Chat Overlay
      - [x] Neuro 立绘
        - [x] 开场上升
        - [ ] 表情差分
        - [x] 旋转缩放
        - [ ] 自然晃动
        - [ ] 音效播放
      - [ ] Evil 立绘
      - [x] 醒目留言 Highlight Messages Overlay
        - [x] 根据来源（Bits 或 Channel Points）显示不同颜色背景
        - [x] 展示用户名和内容
        - [x] 更真实的进入/退出动画
  - [x] 仿 Twitch 离线界面（主播首页）
    - [x] 响应式设计
    - [x] 自动获取最新哔哩哔哩官号回放
    - [ ] 自动获取上一场哔哩哔哩直播回放对应的时间、分区、回放视频链接
    - [ ] 真实的主播首页竖屏模式界面（目前沿用针对竖屏优化过的横屏样式）
    - [ ] 下滑的更多主播信息
    - [ ] 其他次级页面
  - [x] 服务端托管客户端
  - [ ] 更多客户端（若实现原生客户端则弃用 tauri）
    - [x] 可托管的 Web 静态页面
    - [x] Windows 客户端
      - [x] 基于 Tauri
      - [ ] 原生
    - [x] Linux 客户端
      - [x] 基于 Tauri
      - [ ] 原生
    - [ ] Android 客户端
      - [ ] 基于 Tauri
      - [ ] 原生
- Web 控制面板
  - [ ] 更加便捷的启动器/启动脚本
  - [x] 指定服务端 URL 进行连接
  - [x] 直播的开始、停止、重启
  - [x] 配置的查看、编辑、热重载
  - [x] 服务端实时日志查看
    - [ ] 按照等级进行日志筛选
  - [x] Agent管理
    - [x] 对话上下文查看
      - [x] 对话方式展现
      - [x] 上下文方式展现
      - [x] 实时更新
      - [ ] 可视化展现 Agent 的详细执行流程
    - [x] 实时日志查看
      - [ ] 按照等级进行日志筛选 
    - [x] 记忆查看和手动管理
    - [x] 工具
      - [x] 简易的内建工具查看
      - [ ] 工具管理取决于服务端开发进度
- 杂项，有一些可能永远不会实现，有一些可能很快就能完成
  - [ ] 一键启动器和整合包，适用于不需要分开部署的情况，或者面向普通小白用户
  - [ ] 基于静态立绘制作的简易Live2D，能动就行
  - [x] 目前不会考虑语音输入

    > 除非你和 Neuro 在直播中有过语音联动，那我可以叫上 Gemini 一起，给你单独定制一个（

  - [ ] 等待补充…

## 贡献

欢迎提交 Issue 和 Pull Request 来帮助改进项目，虽然大概率会是 Gemini 2.5 或者 Qwen Coder 来处理🤣
