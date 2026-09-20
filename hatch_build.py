
import os
import shutil
import subprocess
from sys import stderr

from hatchling.builders.hooks.plugin.interface import BuildHookInterface  # type: ignore


class CustomBuildHook(BuildHookInterface):
    # 需要随包构建的前端：(名称, 目录)。产物路径由 pyproject force-include/exclude 决定。
    # 注：旧 client/（仿 Twitch 整页）已被 stream 模块的 /ui 内嵌前端取代，不再构建。
    FRONTENDS = [
        ("dashboard", "dashboard"),
        ("stream ui", "server/neuro_simulator/stream/ui"),
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
