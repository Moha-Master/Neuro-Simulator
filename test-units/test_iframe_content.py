"""iframe 元素内容（第 6 期）：kind 白名单、sandbox 安全校验。"""

import unittest

from neuro_simulator import scene_kit


def element(content):
    return {"id": "web", "content": content, "box": {"x": 0, "y": 0, "w": 0.3}, "z": 1}


class TestIframeContent(unittest.TestCase):
    def test_iframe_kind_allowed(self):
        ids = scene_kit.check_elements(
            [element({"kind": "iframe", "src": "pages/demo.html"})], "s"
        )
        self.assertEqual(ids, ["web"])
        scene_kit.check_elements(
            [element({"kind": "iframe", "src": "https://example.com/app"})], "s"
        )

    def test_iframe_requires_src(self):
        with self.assertRaises(scene_kit.SceneError):
            scene_kit.check_elements([element({"kind": "iframe"})], "s")

    def test_sandbox_default_ok_and_custom_ok(self):
        scene_kit.check_elements(
            [element({"kind": "iframe", "src": "a.html", "sandbox": "allow-scripts"})], "s"
        )
        scene_kit.check_elements(
            [element({"kind": "iframe", "src": "a.html", "sandbox": "allow-scripts allow-popups"})],
            "s",
        )

    def test_sandbox_rejects_allow_same_origin(self):
        for sandbox in ("allow-scripts allow-same-origin", "allow-same-origin"):
            with self.assertRaises(scene_kit.SceneError):
                scene_kit.check_elements(
                    [element({"kind": "iframe", "src": "a.html", "sandbox": sandbox})], "s"
                )

    def test_sandbox_must_be_string(self):
        with self.assertRaises(scene_kit.SceneError):
            scene_kit.check_elements(
                [element({"kind": "iframe", "src": "a.html", "sandbox": True})], "s"
            )

    def test_text_font_structure_passes_validation(self):
        content = {
            "kind": "text",
            "text": "hi",
            "font": {"family": "Inter", "size": 0.03, "color": "#fff", "align": "center"},
        }
        scene_kit.check_elements([element(content)], "s")


if __name__ == "__main__":
    unittest.main()
