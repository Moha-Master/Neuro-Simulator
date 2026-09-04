"""PID 文件工具（模块入口与 vedal 共用）。

约定：每个模块启动时把自身 PID 写入 <workdir>/<模块名>.pid，退出时删除。
由此 vedal（或任何工具）可以识别并管理**任何入口**启动的模块实例
（命令行手动启动、vedal 子进程托管，甚至 vedal 重启后遗留的实例）。

- write_pid_file：已有存活实例时拒绝启动（防双开，避免覆盖别人的 PID）
- read_pid_file：返回存活 PID；文件缺失/损坏/进程已死（残留）均返回 None
- 进程被 SIGKILL 时无法自行清理，残留文件由读取方按“进程已死”识别并清理
"""

import os
from pathlib import Path
from typing import Optional


def pid_file_path(workdir: Path, module: str) -> Path:
    return Path(workdir) / f"{module}.pid"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但无权发信号
    return True


def write_pid_file(workdir: Path, module: str) -> Path:
    """写入自身 PID；若已有存活实例则抛 RuntimeError（防双开）。"""
    existing = read_pid_file(workdir, module)
    if existing is not None:
        raise RuntimeError(f"{module} 已有实例在运行 (pid={existing})，请先停止它")
    path = pid_file_path(workdir, module)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return path


def read_pid_file(workdir: Path, module: str) -> Optional[int]:
    """返回 PID 文件中记录的存活 PID；无效或残留时返回 None（并顺手清理残留文件）。"""
    path = pid_file_path(workdir, module)
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid_alive(pid):
        return pid
    try:
        path.unlink()  # 进程已死，清理残留
    except OSError:
        pass
    return None


def remove_pid_file(workdir: Path, module: str) -> None:
    try:
        pid_file_path(workdir, module).unlink()
    except OSError:
        pass
