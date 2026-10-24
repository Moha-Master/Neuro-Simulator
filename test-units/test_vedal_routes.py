"""vedal 的路由表必须覆盖模块文档里列出的每一条端点。

为什么单独有这么一个测试：本轮就踩过一次——`@app.get("/manage/modules")` 的装饰器
在一次编辑中被新插入的代码段整行替换掉，函数体成了没有路由的孤儿。症状很隐蔽：

- 应用能起、单元测试全绿（没人调那个端点）；
- 访问 `/manage/modules` 命中的是最后注册的 SPA 回退，返回 `index.html`；
- dashboard 的 `useModuleStatus` 拿到 HTML → JSON 解析失败 → 首页卡片与所有
  「模块离线态」一起失效，看起来像"模块没在跑"，其实是路由没了。

所以这里把 `vedal/server.py` **模块 docstring 的路由清单**当契约：文档里写了就
必须真的注册，少一条就失败。写法上刻意从 docstring 解析而不是另抄一份常量——
抄一份等于允许文档与代码各说一套，而这两份内容的读者正好是人和机器。
"""

import re
import unittest

from neuro_simulator.vedal import server as vedal_server

# "- GET  /health                    健康检查" / "- POST /manage/module_run/<module_name>  …"
_LINE = re.compile(r"^-\s+(GET|POST|PUT|DELETE)\s+(/\S*)", re.MULTILINE)


def documented_routes() -> list[tuple[str, str]]:
    """从模块 docstring 解析出 (方法, 路径) 列表。"""
    doc = vedal_server.__doc__ or ""
    found: list[tuple[str, str]] = []
    for method, raw in _LINE.findall(doc):
        # 文档里的占位写法是 <module_name>，FastAPI 的是 {module_name}
        path = re.sub(r"<([a-z_]+)>", r"{\1}", raw)
        # "…/logs[/events]" = 两个端点，别让缩写把 /events 藏起来
        if "[" in path and path.endswith("]"):
            base, suffix = path.split("[", 1)
            found.append((method, base))
            found.append((method, base + suffix.rstrip("]")))
        else:
            found.append((method, path))
    return found


def _shape(path: str) -> str:
    """把路径参数名抹掉：文档写 `<module>`、FastAPI 写 `{module_name}`，是同一个端点。

    只抹**参数名**，路径段的名字与数量仍参与比较——否则 `/logs/{x}` 会掩盖
    `/logs/{x}/events` 这种真的多出来的段。
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


class TestDocumentedRoutesExist(unittest.TestCase):
    def setUp(self) -> None:
        self.app = vedal_server.create_app()
        self.routes = {
            (method, _shape(getattr(route, "path", "")))
            for route in self.app.routes
            for method in (getattr(route, "methods", None) or [])
        }
        self.paths = {_shape(getattr(route, "path", "")) for route in self.app.routes}

    def test_docstring_lists_something(self) -> None:
        """解析器本身别悄悄失灵（正则改了、docstring 换了格式）。"""
        self.assertGreaterEqual(len(documented_routes()), 8, documented_routes())

    def test_every_documented_route_is_registered(self) -> None:
        missing = [
            (method, path)
            for method, path in documented_routes()
            if (method, _shape(path)) not in self.routes
        ]
        self.assertEqual(
            missing,
            [],
            "这些端点写在 vedal/server.py 的文档里，但路由表里没有（装饰器是不是被吃掉了？）",
        )

    def test_modules_endpoint_answers_json_not_the_spa(self) -> None:
        """回归：装饰器丢失时这个端点会落到 SPA 回退，返回 index.html。"""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        # 不打真实端口：把模块发现、地址解析与 PID 读取换成固定值
        # （这个用例只关心路由是否活着，不关心进程存不存在）
        patches = (
            patch.object(vedal_server, "_module_names", lambda: ["neuro_sama", "stream"]),
            patch.object(vedal_server, "module_url", lambda name: f"http://127.0.0.1/{name}"),
            patch.object(vedal_server, "_live_pid", lambda name: None),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        resp = TestClient(self.app).get("/manage/modules")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/json", resp.headers["content-type"])
        self.assertIn("modules", resp.json())

    def test_disk_logs_path_is_documented_once(self) -> None:
        """磁盘端点只有一条（没有 /events）：实时跟随走模块自己的 `/logs/events`。"""
        paths = {_shape(path) for _, path in documented_routes()}
        self.assertIn("/manage/logs/{}", paths)
        self.assertNotIn("/manage/logs/{}/events", paths)
        self.assertNotIn("/manage/logs/{}/events", self.paths)


if __name__ == "__main__":
    unittest.main()
