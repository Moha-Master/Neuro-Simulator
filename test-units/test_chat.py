"""测试对话脚本：REPL 输入 -> POST /chat -> 消费 SSE -> 分色易读展示。

用法：
    venv/bin/python -m neuro_sama.chat_client [base_url]
命令：
    /quit  退出
    /reset 新开一个会话（清空多轮上下文）
    /mem   查看服务端记忆区状态
展示分色：
    发送=绿  reasoning=紫斜体  tool调用=黄  tool结果=暗橙  正文=白  usage/done=灰
"""

import argparse
import json

import httpx

# ===== ANSI 颜色 =====
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
ITALIC = "\033[3m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
ORANGE = "\033[38;5;208m"
MAGENTA = "\033[35m"
RED = "\033[31m"

DEFAULT_URL = "http://127.0.0.1:8000"


def _sse_events(resp: httpx.Response):
    """从 SSE 响应流里还原 (event, data_json) 序列。"""
    event = None
    data_lines: list[str] = []
    for line in resp.iter_lines():
        if line == "":
            if event is not None and data_lines:
                yield event, json.loads("\n".join(data_lines))
            event, data_lines = None, []
        elif line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if event is not None and data_lines:
        yield event, json.loads("\n".join(data_lines))


class Renderer:
    """按事件类型分色渲染；流式 delta 逐字打印，切换类型时换行加标签。"""

    def __init__(self) -> None:
        self._mode: str | None = None

    def _switch(self, mode: str, label: str) -> None:
        if self._mode != mode:
            if self._mode is not None:
                print(f"{RESET}")
            if mode is not None:
                print(label, end="", flush=True)
            self._mode = mode

    def flush(self) -> None:
        if self._mode is not None:
            print(f"{RESET}")
        self._mode = None

    def start(self, d: dict) -> None:
        self.flush()
        conv = d.get("conversation_id", "?")
        print(
            f"{DIM}── neuro-sama  (model={d.get('model', '?')}, "
            f"channel={d.get('channel', '?')}, conversation={conv[:8]}) ──{RESET}"
        )

    def reasoning(self, d: dict) -> None:
        self._switch("reasoning", f"{MAGENTA}{ITALIC}neuro » [thinking] {RESET}{MAGENTA}{ITALIC}")
        print(d.get("delta", ""), end="", flush=True)

    def content(self, d: dict) -> None:
        self._switch("content", f"{CYAN}{BOLD}neuro » {RESET}")
        print(d.get("delta", ""), end="", flush=True)

    def tool_start(self, d: dict) -> None:
        self.flush()
        args = json.dumps(d.get("arguments"), ensure_ascii=False, indent=None)
        print(f"{YELLOW}neuro » [tool] {d['name']}({args}){RESET}")

    def tool_result(self, d: dict) -> None:
        mark = "ok" if d.get("ok") else "ERROR"
        color = ORANGE if d.get("ok") else RED
        result = d.get("result", "")
        if len(result) > 400:
            result = result[:400] + "…"
        print(f"{color}    {mark}: {result}  ({d.get('duration_ms', 0)}ms){RESET}")

    def usage(self, d: dict) -> None:
        self.flush()
        print(
            f"{DIM}    · step {d.get('step')} tokens in={d.get('input_tokens')} "
            f"out={d.get('output_tokens')}{RESET}"
        )

    def done(self, d: dict) -> None:
        self.flush()
        mem = d.get("_memory")
        mem_line = f"  memory: {mem['count']} entries ({mem['used']}/{mem['char_limit']} chars)" if mem else ""
        usage = d.get("total_usage", {})
        print(
            f"{DIM}✓ done  tokens in={usage.get('input_tokens', 0)} "
            f"out={usage.get('output_tokens', 0)}{mem_line}{RESET}"
        )

    def error(self, d: dict) -> None:
        self.flush()
        print(f"{RED}✗ error: {d.get('message')}{RESET}")


def main() -> None:
    parser = argparse.ArgumentParser(description="neuro-sama /chat 测试客户端")
    parser.add_argument("base_url", nargs="?", default=DEFAULT_URL)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    state = {"channel": "web", "conversation_id": None}  # None 时由服务端生成，随 start 事件回传
    renderer = Renderer()
    print(f"{DIM}neuro-sama chat client → {base}   (/reset 新会话, /mem 查状态, /quit 退出){RESET}")

    def prompt() -> str:
        conv = state["conversation_id"]
        return f"\n{GREEN}{BOLD}you{RESET}{DIM} [{state['channel']}:{conv[:8] if conv else 'new'}] » {RESET}"

    with httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
        while True:
            try:
                user = input(prompt())
            except (EOFError, KeyboardInterrupt):
                print()
                break

            user = user.strip()
            if not user:
                continue
            if user == "/quit":
                break
            if user == "/reset":
                state["conversation_id"] = None
                print(f"{DIM}↺ new conversation (server will assign){RESET}")
                continue
            if user == "/mem":
                resp = client.get(f"{base}/health")
                resp.raise_for_status()
                print(f"{DIM}{json.dumps(resp.json(), ensure_ascii=False, indent=2)}{RESET}")
                continue

            print(f"{GREEN}you » {user}{RESET}")
            try:
                with client.stream(
                    "POST",
                    f"{base}/chat",
                    json={
                        "channel": state["channel"],
                        "conversation_id": state["conversation_id"],
                        "message": user,
                    },
                ) as resp:
                    resp.raise_for_status()
                    for event, data in _sse_events(resp):
                        if event == "start":
                            state["conversation_id"] = data.get("conversation_id")
                        getattr(renderer, event, renderer.error)(data)
            except httpx.HTTPError as e:
                print(f"{RED}✗ request failed: {e}{RESET}")


if __name__ == "__main__":
    main()
