"""模块资产（图片/视频/html 单文件）：落盘、白名单、SSRF 防护与 `/ui/assets` 路由。

机制在这里，策略（各自的包内模板目录、工作目录）由各模块注入：

- **目录布局**：包内模板（如 `stream/assets/`）首启复制到 `<workdir>/<module>/assets/`
  （只补缺失，与 config.json / neuro ui_media 的 deploy 同一套做法）；上传按类别落
  `images/`、`videos/`、`html/` 子目录。工作目录是权威——用户换/传素材不动 site-packages。
- **上传/URL 导入统一"导入即落盘"**（设计定稿 §5）：`POST /ui/assets`（multipart
  `file` 或表单 `url` 二选一）→ 魔数 + 扩展名双白名单 → sha256 前 12 位命名 →
  `GET /ui/assets/{path}` 取回（FileResponse，Range 支持，MIME 按扩展名白名单）。
  不做实时代理："后端中转"原则约束的是素材，活页面由 iframe kind 直嵌（第 6 期）。
- **SSRF 防护**（URL 导入）：只允许 http(s)；目标逐跳解析，拒 loopback / RFC1918 /
  link-local / 组播 / 保留 / 未指定地址（含云元数据 169.254.169.254）；重定向
  手动跟随（≤5 跳）且**每跳重新校验**；大小上限图片 20MB / 视频 200MB / html 20MB。
- **寻址约定**：元素 `src` 用返回的相对名（如 `images/ab12cd34.png`）；裸文件名
  （无 `/`）兼容包内模板——GET 时按 images/videos/html 顺序兜底查找，
  所以默认清单里的 `no_source.webp` 这类旧裸名不改 JSON 也能继续工作。

`test_ui_frontend_contract.py` 守卫：不允许 `app.mount("/ui", ...)`，必须显式路由。
"""

import hashlib
import ipaddress
import shutil
import socket
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

# 扩展名 → (MIME, 类别)。类别决定上传落哪个子目录；GET 的 MIME 白名单同源。
ASSET_TYPES: Dict[str, Tuple[str, str]] = {
    ".webp": ("image/webp", "image"),
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".gif": ("image/gif", "image"),
    ".svg": ("image/svg+xml", "image"),
    ".mp4": ("video/mp4", "video"),
    ".webm": ("video/webm", "video"),
    ".html": ("text/html", "html"),
    ".woff2": ("font/woff2", "image"),
}

# 大小上限（字节）：图片 20MB / 视频 200MB / html 20MB
SIZE_LIMITS = {"image": 20 * 1024 * 1024, "video": 200 * 1024 * 1024, "html": 20 * 1024 * 1024}

# 上传子目录（包内模板放在根或 images/，GET 有兜底查找，两者都能裸名命中）
SUBDIRS = ("images", "videos", "html")

_MAX_REDIRECTS = 5
_FETCH_TIMEOUT_S = 20.0


def sniff_ext(data: bytes) -> Optional[str]:
    """魔数嗅探扩展名（白名单内的才返回；认不出 = None）。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"wOF2":
        return ".woff2"
    if data[4:8] == b"ftyp":
        return ".mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    head = data[:4096].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        return ".svg"
    if b"<!doctype html" in head or b"<html" in head or b"<script" in head or b"<body" in head:
        return ".html"
    return None


def kind_of(ext: str) -> str:
    return ASSET_TYPES[ext][1]


def mime_of(ext: str) -> str:
    return ASSET_TYPES[ext][0]


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(url: str) -> None:
    """URL 导入的 SSRF 校验：http(s) + 主机名解析出的所有地址都不是内网/保留段。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise ValueError(f"URL 无法解析: {e}") from e
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只允许 http(s) 外链导入")
    host = parsed.hostname
    if not host:
        raise ValueError("URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise ValueError(f"主机名解析失败: {host}") from e
    if not infos:
        raise ValueError(f"主机名解析失败: {host}")
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            continue
        if _is_forbidden_ip(ip):
            raise ValueError(f"拒绝内网/保留地址: {host} → {addr}")


async def fetch_url(
    url: str,
) -> Tuple[bytes, str]:
    """抓取外链文件（手动跟随重定向，逐跳 SSRF 校验；超限抛 ValueError）。"""
    current = url
    limit = SIZE_LIMITS["video"]  # 全局最大；真实类别校验在落盘前做
    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT_S, follow_redirects=False, headers={"user-agent": "neuro-simulator-assets/1.0"}
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            validate_public_url(current)
            try:
                resp = await client.get(current)
            except httpx.HTTPError as e:
                raise ValueError(f"下载失败: {e}") from e
            if resp.is_redirect:
                location = resp.headers.get("location", "")
                if not location:
                    raise ValueError("重定向缺少 Location")
                current = str(resp.next_request.url) if resp.next_request else location
                continue
            if resp.status_code != 200:
                raise ValueError(f"下载失败: HTTP {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise ValueError("文件超过大小上限")
            data = resp.content
            if len(data) > limit:
                raise ValueError("文件超过大小上限")
            return data, current
    raise ValueError("重定向次数过多")


class AssetStore:
    """一个模块的资产目录（包内模板 deploy + 上传落盘 + 解析/清单）。"""

    def __init__(self, package_dir: Path, workdir: Path) -> None:
        self.package_dir = Path(package_dir)
        self.root = Path(workdir)

    # ---------- 部署 ----------

    def deploy(self) -> Path:
        """包内模板 → 工作目录（递归、只补缺失）；返回工作目录。"""
        self.root.mkdir(parents=True, exist_ok=True)
        if self.package_dir.is_dir():
            for src in self.package_dir.rglob("*"):
                if not src.is_file():
                    continue
                dst = self.root / src.relative_to(self.package_dir)
                if dst.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
        for sub in SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        return self.root

    # ---------- 解析 / 清单 ----------

    def resolve(self, rel: str) -> Optional[Path]:
        """相对名 → 工作目录内的实际文件；穿越/越界/不存在返回 None。

        裸文件名（无 `/`）按 images/videos/html 顺序兜底：包内模板的旧裸名
        `src` 不改 JSON 也能命中。
        """
        clean = (rel or "").strip().strip("/")
        if not clean or clean.startswith("."):
            return None
        candidates = [clean]
        if "/" not in clean:
            candidates = [f"{sub}/{clean}" for sub in SUBDIRS] + [clean]
        root = self.root.resolve()
        for cand in candidates:
            p = (root / cand).resolve()
            try:
                p.relative_to(root)
            except ValueError:
                continue
            if p.is_file():
                return p
        return None

    def list_assets(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not self.root.is_dir():
            return out
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root).as_posix()
            ext = p.suffix.lower()
            if ext not in ASSET_TYPES:
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            out.append(
                {
                    "name": rel,
                    "kind": kind_of(ext),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return out

    # ---------- 落盘 ----------

    def save(self, data: bytes, source_ext: Optional[str]) -> Dict[str, Any]:
        """魔数 + 扩展名双白名单校验后按 sha 落盘；返回给前端的描述。"""
        ext = sniff_ext(data)
        if ext is None:
            raise ValueError("不支持的文件类型（魔数不在白名单内）")
        if source_ext is not None and source_ext.lower() not in ASSET_TYPES:
            raise ValueError(f"不支持的扩展名: {source_ext}")
        kind = kind_of(ext)
        limit = SIZE_LIMITS[kind]
        if len(data) > limit:
            raise ValueError(f"文件超过大小上限（{kind} {limit // (1024 * 1024)}MB）")
        subdir = {"image": "images", "video": "videos", "html": "html"}[kind]
        target_dir = self.root / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(data).hexdigest()[:12] + ext
        path = target_dir / name
        if not path.exists():
            path.write_bytes(data)
        rel = f"{subdir}/{name}"
        return {
            "name": rel,
            "src": rel,
            "url": f"/ui/assets/{rel}",
            "size": len(data),
            "mime": mime_of(ext),
            "kind": kind,
        }


def register_asset_routes(app: FastAPI, store: Callable[[], AssetStore]) -> None:
    """挂 `/ui/assets` 三条显式路由（不许 `app.mount("/ui", ...)`）。"""

    @app.get("/ui/assets")
    async def ui_assets_list():
        return {"assets": store().list_assets()}

    @app.post("/ui/assets")
    async def ui_assets_upload(
        file: Optional[UploadFile] = File(default=None),
        url: Optional[str] = Form(default=None),
    ):
        if file is not None:
            data = await file.read()
            source_ext = Path(file.filename or "").suffix or None
        elif url and url.strip():
            try:
                data, final_url = await fetch_url(url.strip())
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            source_ext = Path(urllib.parse.urlsplit(final_url).path).suffix or None
        else:
            raise HTTPException(status_code=400, detail="需要 multipart file 或 url 之一")
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        try:
            result = store().save(data, source_ext)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return result

    @app.get("/ui/assets/{path:path}")
    async def ui_assets_file(path: str):
        found = store().resolve(path)
        if found is None:
            raise HTTPException(status_code=404, detail=f"没有这个资产: {path}")
        ext = found.suffix.lower()
        if ext not in ASSET_TYPES:
            raise HTTPException(status_code=404, detail=f"不支持的资产类型: {ext}")
        return FileResponse(found, media_type=mime_of(ext))
