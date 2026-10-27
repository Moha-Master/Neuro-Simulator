
import os
import shutil
import subprocess
from sys import stderr

from hatchling.builders.hooks.plugin.interface import BuildHookInterface  # type: ignore


class CustomBuildHook(BuildHookInterface):
    # 需要随包构建的前端：(名称, 目录)。产物路径由 pyproject force-include 决定。
    # - dashboard：主应用 + 神经窗口（/ui/neuro/）与直播合成器（/ui/stream/）
    #   两个独立 HTML 入口，代码在 dashboard/src/ui/；
    # - client：stream 自托管的观看前端（/view，经 vedal 为 /manage/stream/view/）。
    FRONTENDS = [
        ("dashboard", "dashboard"),
        ("client", "client"),
    ]

    def initialize(self, version, build_data):
        super().initialize(version, build_data)

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("NodeJS `npm` is required for building frontends but it was not found")

        for name, rel_dir in self.FRONTENDS:
            frontend_dir = os.path.join(self.root, rel_dir)
            if not os.path.isdir(frontend_dir):
                stderr.write(f">>> Frontend directory not found at {frontend_dir}\n")
                continue
            stderr.write(f">>> Building {name} frontend\n")
            stderr.write("### npm ci\n")
            subprocess.run([npm, "ci"], check=True, cwd=frontend_dir)
            stderr.write("\n### npm run build-only\n")
            subprocess.run([npm, "run", "build-only"], check=True, cwd=frontend_dir)

        stderr.write("\n>>> Done\n")
