# Neuro-Simulator 服务端

*本临时README由AI自动生成*

这是 Neuro Simulator 的服务端，负责处理直播逻辑、AI 交互、TTS 合成等核心功能。

## 功能特性

- **动态观众**：调用LLM，基于直播内容动态生成 Chat
- **配置管理**：支持通过 API 动态修改和热重载配置
- **外部控制**：完全使用外部API端点操控服务端运行

## 目录结构

``` main
neuro_simulator/
├── __init__.py
├── cli.py
├── agent/
│   ├── __init__.py
│   ├── core.py
│   ├── llm.py
│   ├── neuro_prompt.txt
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── chat_history.json
│   │   ├── core_memory.json
│   │   ├── init_memory.json
│   │   ├── manager.py
│   │   └── temp_memory.json
│   └── tools/
│       └── ...
├── api/
│   ├── __init__.py
│   └── system.py
├── assets/
│   └── neuro_start.mp4
├── core/
│   ├── __init__.py
│   ├── agent_factory.py
│   ├── agent_interface.py
│   ├── application.py
│   ├── config.py
│   ├── config.yaml.example
│   └── path_manager.py
├── services/
│   ├── __init__.py
│   ├── audience.py
│   ├── audio.py
│   ├── builtin.py
│   └── stream.py
└── utils/
    ├── __init__.py
    ├── logging.py
    ├── process.py
    ├── queue.py
    ├── state.py
    └── websocket.py
```

``` workin'dir
working_dir_example/     # 工作目录结构，供你参考
├── assets/              # 媒体文件夹，如缺失会使用自带资源覆盖
│   └── neuro_start.mp4  # 用来计算Start Soon长度，仅读取时长,请和客户端的视频保持一致
├── config.yaml          # 系统配置文件，由服务端自动管理，无需手动填写
└── agents/              # Agent相关文件夹
    ├── memories/        # Agent记忆文件夹
    │   ├── core_memory.json
    │   ├── init_memory.json
    │   └── temp_memory.json
    ├── neuro/           # Neuro主Agent
    │   ├── history.jsonl
    │   ├── neuro_prompt.txt
    │   └── tools.json
    └── tools/           # Agent工具文件夹
        └── builtin_tools/
```

## 安装与配置

1. 自己找一个最好是空的文件夹作为工作目录。
   - 程序会在未指定 `--dir, -D` 的情况下自动生成一个工作目录，路径为 `~/.config/neuro-simulator/`。
2. 启动程序，按照外面那个 README 中的方式完成配置。

3. 可以自行替换 `$dir/assets/neuro_start.mp4` 为其它视频文件，但记得手动替换 client 中的同名文件。

### Agent配置

服务端支持两种 Agent 类型：
~~1. **Letta Agent**：需要配置 Letta Cloud 或自托管的 Letta Server~~ 暂时下线，后会有期。  
2. **内建 Agent**：使用服务端自带的 Agent，支持 Gemini 和OpenAI API。

不管用的是什么 Agent，在管理面板中配置和分配好服务商相关就行了。

### 直接安装方式（无需二次开发）

若无需二次开发，可以直接使用 pip 安装：
```bash
# 直接使用 pip 安装为全局软件：
pip install neuro-simulator
```

```bash
# 系统 Python 环境不宜变动时，建议使用 pipx 安装为全局软件
pipx install neuro-simulator
```

```bash
# 使用 venv 方式安装：
python3 -m venv venv
# Windows
venv/Scripts/pip install neuro-simulator
# macOS/Linux
venv/bin/pip install neuro-simulator
```

### 二次开发方式

若需要二次开发，请克隆项目：
```bash
git clone https://github.com/your-username/Neuro-Simulator.git
cd Neuro-Simulator/server
python3 -m venv venv
# Windows
venv/Scripts/pip install -e .
# macOS/Linux
venv/bin/pip install -e .
```
安装时会自动构建 Dashboard 和 Client，请确保系统安装了 Node.js。

### 运行服务

```bash
# 使用默认配置 (位于~/.config/neuro-simulator/)
neuro

# 指定工作目录
neuro -D /path/to/your/config

# 指定监听地址和端口
neuro -H 0.0.0.0 -P 8080

# 组合使用
neuro -D /path/to/your/config -H 0.0.0.0 -P 8080
```

手动指定的监听地址和端口会覆盖配置文件中的设置。

如果没有指定，服务默认遵循配置文件中的设置，运行在 `http://127.0.0.1:8000`。

## API 接口

- `/ws/admin`: 用于控制面板的管理接口，提供直播控制、配置管理、日志监控、Agent交互等所有功能，详细规范请参阅 `WEBSOCKET_API.md`。
- `/ws/stream`: 客户端使用的直播接口。
- `/api/system/health`: 健康检查接口。

## 配置说明

配置文件 `config.yaml` 现在一般无需手动编辑，所有配置项在管理面板中均可进行可视化配置。

有关配置文件的完整示例，请参阅项目根目录下的 `docs/working_dir_example/` 文件夹

## 安全说明

服务端具有 CORS 配置，仅允许预配置的来源访问。如果莫名其妙地连不上服务端（尤其是在外网环境中），可以检查和更改此项设置。  

**公网部署请务必更改管理密钥**，建议更改端口为非常规端口以避免爆破。咱这个程序只是能用就行的水平，永远不要相信它的安保性能。

## 故障排除

- 确保所有必需的 API 密钥都已正确配置
- 检查网络连接是否正常
- 查看日志文件获取错误信息
- 确保端口未被其他程序占用

> 都是些 AI 生成的垃圾话，看看就好

*作为看这篇💩文档的奖励，如果你需要使用外部面板而不是服务器自托管面板，可以直接使用我部署的 https://dashboard.neuro.jiahui.cafe 连接到你的服务端，但是不保证始终能用，而且请配置好 CORS*
