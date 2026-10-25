"""前端结构契约：合并成**一份**前端之后要守住的东西。

过去两个前端工程各持一份逐字相同的元素引擎，靠 `test_ui_engine_parity.py`
防漂移；现在引擎只有一份（`dashboard/src/ui/engine/`），那份测试就没意义了。
但合并引入了新的风险面，换成这个测试来守：

1. **只有一份引擎**：旧的 `stream/ui`、`neuro_sama/ui` 前端工程必须已经消失
   （它们留在包内就等于"前端又分散回 Python 包了"）。
2. **引擎不认识任何页面私有模块**：组件注册表/资产寻址/动作表都是注入的，
   引擎里出现 `./components`、`./chat` 这类 import 就说明耦合又长回来了。
3. **两个独立入口齐全**：合成器与 neuro 窗口各有一个 HTML 入口，
   入口上带着模块 API 基址（画面页要靠它连后端）。
4. **场景数据说要用什么，画面就得有**：场景里 `content.kind == "component"`
   的组件名必须在对应页面的组件注册表里；图片/视频素材必须真的存在
   （前端在 `dashboard/public/`，模块媒体在模块的 `assets/`）。
5. **模块不再托管页面**：`/ui` 只剩 API（事件流/场景清单/媒体），静态页面
   由 dashboard 的构建产物经 vedal 提供。
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
ENGINE = DASH / "src/ui/engine/elements.ts"
STREAM_UI = DASH / "src/ui/stream"
NEURO_UI = DASH / "src/ui/neuro"
STREAM_ENTRY = DASH / "ui/stream/index.html"
NEURO_ENTRY = DASH / "ui/neuro/index.html"
PUBLIC = DASH / "public"
STREAM_MANIFEST = ROOT / "server/neuro_simulator/stream/defaults/scenes.json"
NEURO_MANIFEST = ROOT / "server/neuro_simulator/neuro_sama/defaults/neuro_scenes.json"
NEURO_MEDIA = ROOT / "server/neuro_simulator/neuro_sama/assets"
STREAM_SERVER = ROOT / "server/neuro_simulator/stream/server.py"
NEURO_SERVER = ROOT / "server/neuro_simulator/neuro_sama/server.py"


def _manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _components_of(manifest: dict) -> set[str]:
    names: set[str] = set()
    for scene in manifest.get("scenes") or []:
        for el in scene.get("elements") or []:
            content = el.get("content") or {}
            if content.get("kind") == "component" and content.get("name"):
                names.add(content["name"])
    return names


def _registered(ui_dir: Path) -> set[str]:
    """从组件注册表里认出已注册的组件名。"""
    # 归一化引号：断言看的是"注册了哪些组件"，不是引号风格
    src = (ui_dir / "components.ts").read_text(encoding="utf-8").replace("'", '"')
    block = re.search(r"COMPONENTS[^=]*=\s*\{(.*?)\n\};?", src, re.S)
    if not block:
        return set()
    return set(re.findall(r'"?([A-Za-z][A-Za-z.]*)"?\s*:', block.group(1)))


class TestStreamNeuroMirrorContract(unittest.TestCase):
    """画面事件聚合（方案 A）：stream 是唯一 SSE，neuro iframe 吃转发帧。

    守的是协议形状而不是实现细节：新增事件名、转发入口、握手与回退都必须在，
    而 `/stream-view` 自己**不能**再建 EventSource。
    """

    def test_stream_feed_has_neuro_event(self):
        src = (STREAM_UI / "feed.ts").read_text(encoding="utf-8").replace("'", '"')
        block = re.search(r"const EVENT_NAMES\s*=\s*\[(.*?)\]", src, re.S)
        self.assertIsNotNone(block, "feed.ts 缺少 EVENT_NAMES")
        self.assertIn('"neuro"', block.group(1))
        self.assertIn("interface NeuroMirrorEvent", src)
        self.assertIn("onAny", src)

    def test_stream_main_forwards_neuro_to_iframe(self):
        src = (STREAM_UI / "main.ts").read_text(encoding="utf-8")
        self.assertIn("neuro?.forward", src)
        self.assertIn("NeuroMirrorEvent", src)
        self.assertIn("if (!neuroNeedsSnapshot", src)
        self.assertIn("ns: 'stream-feed'", src)

    def test_stream_neuro_layer_has_forward_and_feed_ready(self):
        src = (STREAM_UI / "neuro.ts").read_text(encoding="utf-8")
        self.assertIn("forward (event: unknown)", src)
        self.assertIn("feed_ready", src)
        self.assertIn("this.feedReady", src)
        self.assertIn("onFeedReady", src)

    def test_neuro_main_has_remote_handshake_and_fallback(self):
        src = (NEURO_UI / "main.ts").read_text(encoding="utf-8")
        self.assertIn("RemoteFeed", src)
        self.assertIn("feed_ready", src)
        self.assertIn("FEED_READY_TIMEOUT_MS = 500", src)
        self.assertIn("new NeuroFeed()", src, "超时后必须能回退到本地自连")
        self.assertIn("interface FeedSource", src)

    def test_stream_view_chat_has_no_event_source(self):
        src = (DASH / "src/composables/useStreamChat.ts").read_text(encoding="utf-8")
        self.assertNotIn("new EventSource", src)
        self.assertIn("'stream-feed'", src)
        self.assertIn("requestSnapshot", src)


class TestSingleFrontend(unittest.TestCase):
    def test_old_frontend_projects_are_gone(self):
        """旧的独立前端工程必须已经删除（前端不再散落在 Python 包里）。"""
        for rel in (
            "server/neuro_simulator/stream/ui/src",
            "server/neuro_simulator/neuro_sama/ui/src",
            "server/neuro_simulator/stream/ui/package.json",
            "server/neuro_simulator/neuro_sama/ui/package.json",
        ):
            self.assertFalse((ROOT / rel).exists(), f"{rel} 还留在仓库里")

    def test_engine_is_shared_and_page_agnostic(self):
        self.assertTrue(ENGINE.is_file(), ENGINE)
        # 归一化引号：断言看的是"依赖了谁"，不是引号风格
        src = ENGINE.read_text(encoding="utf-8").replace("'", '"')
        for forbidden in ("./components", "./assets", "./chat", "./chess", "./wordle", "./speech"):
            self.assertNotIn(
                f'from "{forbidden}"', src, f"引擎不该直接依赖页面私有模块: {forbidden}"
            )
        for needed in ('from "./types"', 'from "./action"', 'from "./schema"'):
            self.assertIn(needed, src, f"引擎缺少共享依赖: {needed}")

    def test_both_entries_exist_with_module_base(self):
        for entry in (STREAM_ENTRY, NEURO_ENTRY):
            self.assertTrue(entry.is_file(), entry)
            html = entry.read_text(encoding="utf-8")
            self.assertIn("data-module-base", html, f"{entry.name} 缺模块 API 基址")
            self.assertIn("/src/ui/", html, f"{entry.name} 没引用 dashboard 里的入口脚本")

    def test_scene_components_are_registered(self):
        """场景里用到的组件必须在对应页面的注册表里，否则那一层会静默变空。"""
        for manifest_path, ui_dir in (
            (STREAM_MANIFEST, STREAM_UI),
            (NEURO_MANIFEST, NEURO_UI),
        ):
            needed = _components_of(_manifest(manifest_path))
            registered = _registered(ui_dir)
            self.assertTrue(needed, f"{manifest_path.name} 没有用到任何组件？")
            missing = needed - registered
            self.assertFalse(missing, f"{ui_dir.name} 缺组件注册: {sorted(missing)}")

    def test_highlight_overlay_belongs_to_neuro_window(self):
        """高亮消息浮层只在 neuro 场景：stream 清单/注册表/main.ts 都不再有它。"""
        stream_components = _components_of(_manifest(STREAM_MANIFEST))
        self.assertNotIn("highlight", stream_components)
        self.assertNotIn("highlight", _registered(STREAM_UI))

        neuro_components = _components_of(_manifest(NEURO_MANIFEST))
        self.assertIn("highlight", neuro_components)
        self.assertIn("highlight", _registered(NEURO_UI))

        stream_main = (STREAM_UI / "main.ts").read_text(encoding="utf-8")
        self.assertNotIn("HighlightOverlay", stream_main)
        self.assertTrue((NEURO_UI / "highlight.ts").is_file())

        feed = (NEURO_UI / "feed.ts").read_text(encoding="utf-8")
        self.assertIn("'highlight'", feed)

    def test_scene_assets_exist(self):
        """场景引用的素材必须真的在：图片在前端 public/，视频在模块的 media 目录。"""
        for manifest_path in (STREAM_MANIFEST, NEURO_MANIFEST):
            for scene in _manifest(manifest_path).get("scenes") or []:
                for el in scene.get("elements") or []:
                    content = el.get("content") or {}
                    kind, src = content.get("kind"), content.get("src")
                    if kind == "image":
                        self.assertTrue(
                            (PUBLIC / src).is_file(), f"缺前端素材: {src}（{manifest_path.name}）"
                        )
                    elif kind == "video":
                        self.assertTrue(
                            (NEURO_MEDIA / src).is_file(),
                            f"缺模块媒体: {src}（后端要探测它的时长，必须在模块包里）",
                        )

    def test_modules_no_longer_serve_static_pages(self):
        """模块的 /ui 只剩 API：静态页面由 dashboard 构建产物提供。"""
        for server in (STREAM_SERVER, NEURO_SERVER):
            src = server.read_text(encoding="utf-8")
            self.assertNotIn('app.mount("/ui"', src, f"{server.name} 还在托管静态页面")
            self.assertNotIn("StaticFiles", src, f"{server.name} 还挂着静态目录")


if __name__ == "__main__":
    unittest.main()
