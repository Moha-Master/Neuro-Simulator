
import os
import shutil
import subprocess
from sys import stderr

from hatchling.builders.hooks.plugin.interface import BuildHookInterface  # type: ignore


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        super().initialize(version, build_data)

        stderr.write(">>> Building dashboard frontend\n")

        dashboard_dir = os.path.join(self.root, 'dashboard')
        if not os.path.isdir(dashboard_dir):
            stderr.write(f">>> Frontend directory not found at {dashboard_dir}\n")
            return

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "NodeJS `npm` is required for building dashboard but it was not found"
            )

        stderr.write("### npm ci\n")
        subprocess.run([npm, "ci"], check=True, cwd=dashboard_dir)

        stderr.write("\n### npm run build\n")
        subprocess.run([npm, "run", "build"], check=True, cwd=dashboard_dir)

        stderr.write("\n>>> Done\n")

        # 注：client 前端构建暂时禁用（client 重写后恢复）
        # stderr.write(">>> Building client frontend\n")
        # client_dir = os.path.join(self.root, 'client')
        # if not os.path.isdir(client_dir):
        #     stderr.write(f">>> Frontend directory not found at {client_dir}\n")
        #     return
        # stderr.write("### npm ci\n")
        # subprocess.run([npm, "ci"], check=True, cwd=client_dir)
        # stderr.write("\n### npm run build\n")
        # subprocess.run([npm, "run", "build"], check=True, cwd=client_dir)
