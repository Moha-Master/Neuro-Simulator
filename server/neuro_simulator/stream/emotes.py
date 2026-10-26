"""频道表情：拉清单 + 预下载图片（前端只吃同源接口）。

外部依赖全部收敛到后端刷新：
- 三个匿名 global 清单：7TV / BTTV / FFZ；
- 7TV GraphQL 搜索：频道专属表情（`vedal*` / `neuro*` / `evil*`）在 7TV 有
  精确大小写同名镜像，匿名可搜可下；
- Twitch 原生订阅表情必须 OAuth，**故意不做**（不写绕认证代码）。

工作目录权威（与 neuro 开场视频同款做法）：图片落
`<workdir>/stream/emotes/{manifest.json,images/,custom/}`。`custom/` 是人工投放的
最高优先级表情，每次读取现扫，不依赖刷新。

manifest 只由 `refresh()` 写，原子替换（tmp + os.replace）；任何单源失败都保留
旧清单里的该源条目，绝不拿空清单覆盖好数据。
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote

import httpx

from .chatbot import EMOTE_CANON

logger = logging.getLogger(__name__)

REFRESH_S = 24 * 3600
REQUEST_TIMEOUT_S = 10.0
DOWNLOAD_CONCURRENCY = 4
RETRY_COUNT = 3
USER_AGENT = "neuro-simulator/0.1 (local streaming simulator)"

SEVEN_TV_GLOBAL = "https://7tv.io/v3/emote-sets/global"
SEVEN_TV_GQL = "https://7tv.io/v3/gql"
BTTV_GLOBAL = "https://api.betterttv.net/3/cached/emotes/global"
FFZ_GLOBAL = "https://api.frankerfacez.com/v1/set/global"

SEVEN_TV_SEARCH_QUERY = (
    "query($q:String!){ emotes(query:$q, page:0, limit:10){ "
    "items{ id name host{ url files{ name } } } } }"
)

SOURCE_ORDER = ("7tv_global", "bttv_global", "ffz_global", "7tv_search")
GLOBAL_SOURCES = (
    ("7tv_global", SEVEN_TV_GLOBAL),
    ("bttv_global", BTTV_GLOBAL),
    ("ffz_global", FFZ_GLOBAL),
)

_CUSTOM_MIME = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_EXT_MIME = _CUSTOM_MIME


class RefreshBusy(RuntimeError):
    """已有一次刷新在跑（POST /emotes/refresh 映射成 409）。"""


@dataclass(frozen=True)
class _Candidate:
    name: str
    source: str
    emote_id: str
    url: str
    ext: str
    mime: str
    animated: bool


@dataclass(frozen=True)
class _Resolved:
    path: Path
    mime: str
    source: str
    animated: bool


# 测试可替换（避免真睡）；不要把它改成 asyncio.sleep 的直接别名引用。
_sleep = asyncio.sleep


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    """文件名只消毒 '/'：Linux 上 ':' 合法，BTTV 的 `:tf:` 类名字保留原形。"""
    return name.replace("/", "_")


def _mime_for_ext(ext: str) -> str:
    return _EXT_MIME.get(ext.lower(), "application/octet-stream")


def _ext_from_url(url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return suffix if suffix in _EXT_MIME else ""


def _entry(manifest: dict, name: str) -> Optional[dict]:
    emotes = manifest.get("emotes") if isinstance(manifest, dict) else None
    value = emotes.get(name) if isinstance(emotes, dict) else None
    return value if isinstance(value, dict) else None


# ---------- 源清单解析 ----------


def _seven_tv_payload(payload: Any) -> Iterable[dict]:
    items = payload.get("emotes") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return ()
    return (item for item in items if isinstance(item, dict))


def _seven_tv_file(files: Any, animated: bool) -> Optional[str]:
    names = []
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict) and f.get("name"):
                names.append(str(f["name"]))
    if not names:
        return None
    if animated:
        for preferred in ("4x.gif", "3x.gif", "2x.gif", "1x.gif"):
            if preferred in names:
                return preferred
        for name in names:
            if name.lower().endswith(".gif"):
                return name
    for preferred in ("2x.webp", "1x.webp", "4x.webp"):
        if preferred in names:
            return preferred
    for name in names:
        if name.lower().endswith(".webp"):
            return name
    return names[0]


def _seven_tv_url(host: Any, emote_id: str, file_name: str) -> str:
    raw = host.get("url") if isinstance(host, dict) else None
    base = str(raw or "").strip()
    if not base:
        base = f"//cdn.7tv.app/emote/{emote_id}"
    if base.startswith("//"):
        base = "https:" + base
    elif base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    base = base.replace("{{id}}", emote_id).replace("{{file_name}}", file_name)
    if not base.rstrip("/").endswith("/" + file_name) and not base.endswith(file_name):
        base = base.rstrip("/") + "/" + file_name
    return base


def _parse_seven_tv(payload: Any, source: str) -> Dict[str, _Candidate]:
    result: Dict[str, _Candidate] = {}
    for raw in _seven_tv_payload(payload):
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        name = str(data.get("name") or "").strip()
        emote_id = str(data.get("id") or "").strip()
        if not name or not emote_id:
            continue
        animated = bool(data.get("animated"))
        host = data.get("host") if isinstance(data.get("host"), dict) else {}
        file_name = _seven_tv_file(host.get("files"), animated)
        if not file_name:
            continue
        ext = Path(file_name).suffix.lower() or ".webp"
        result[name] = _Candidate(
            name=name,
            source=source,
            emote_id=emote_id,
            url=_seven_tv_url(host, emote_id, file_name),
            ext=ext,
            mime=_mime_for_ext(ext),
            animated=animated or ext == ".gif",
        )
    return result


def _parse_bttv(payload: Any) -> Dict[str, _Candidate]:
    result: Dict[str, _Candidate] = {}
    if not isinstance(payload, list):
        return result
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("code") or "").strip()
        emote_id = str(raw.get("id") or "").strip()
        if not name or not emote_id:
            continue
        animated = bool(raw.get("animated"))
        image_type = str(raw.get("imageType") or "").lower().lstrip(".")
        ext = f".{image_type}" if image_type in ("gif", "webp", "png") else (
            ".gif" if animated else ".png"
        )
        result[name] = _Candidate(
            name=name,
            source="bttv_global",
            emote_id=emote_id,
            url=f"https://cdn.betterttv.net/emote/{emote_id}/2x",
            ext=ext,
            mime=_mime_for_ext(ext),
            animated=animated or ext == ".gif",
        )
    return result


def _parse_ffz(payload: Any) -> Dict[str, _Candidate]:
    result: Dict[str, _Candidate] = {}
    if not isinstance(payload, dict):
        return result
    sets = payload.get("sets")
    if not isinstance(sets, dict):
        return result
    for emote_set in sets.values():
        if not isinstance(emote_set, dict):
            continue
        emoticons = emote_set.get("emoticons")
        if not isinstance(emoticons, list):
            continue
        for raw in emoticons:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            urls = raw.get("urls")
            url = str(urls.get("2") or "") if isinstance(urls, dict) else ""
            if not name or not url:
                continue
            animated = bool(raw.get("animated"))
            ext = _ext_from_url(url) or (".gif" if animated else ".png")
            result[name] = _Candidate(
                name=name,
                source="ffz_global",
                emote_id=str(raw.get("id") or name),
                url=url,
                ext=ext,
                mime=_mime_for_ext(ext),
                animated=animated or ext == ".gif",
            )
    return result


def _parse_search(payload: Any, name: str) -> Optional[_Candidate]:
    """GraphQL 搜索：只认**精确大小写全名**，多个同名取 id 最小（ULID 越老越小）。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    emotes = data.get("emotes") if isinstance(data, dict) else None
    items = emotes.get("items") if isinstance(emotes, dict) else None
    if not isinstance(items, list):
        return None
    matches = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("name") or "") == name
    ]
    if not matches:
        return None
    exact = min(matches, key=lambda item: str(item.get("id") or ""))
    parsed = _parse_seven_tv({"emotes": [exact]}, "7tv_search")
    return parsed.get(name)


class EmoteStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.images_dir = self.root / "images"
        self.custom_dir = self.root / "custom"
        self.manifest_path = self.root / "manifest.json"
        self._manifest: dict = {}
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None

    # ---------- 读取（同步、无 I/O 网络） ----------

    def _scan_custom(self) -> Dict[str, _Resolved]:
        resolved: Dict[str, _Resolved] = {}
        try:
            entries = list(self.custom_dir.iterdir())
        except FileNotFoundError:
            return resolved
        except OSError as e:
            logger.warning("读取 custom 表情目录失败: %s", e)
            return resolved
        for path in sorted(entries, key=lambda p: p.name):
            if not path.is_file():
                continue
            mime = _CUSTOM_MIME.get(path.suffix.lower())
            if mime is None:
                continue
            resolved[path.stem] = _Resolved(
                path=path,
                mime=mime,
                source="custom",
                animated=path.suffix.lower() == ".gif",
            )
        return resolved

    def _resolve(self) -> Dict[str, _Resolved]:
        """manifest ∪ custom（custom 覆盖同名 manifest 条目），过滤掉已不存在的文件。"""
        resolved: Dict[str, _Resolved] = {}
        emotes = self._manifest.get("emotes")
        if isinstance(emotes, dict):
            for name, entry in emotes.items():
                if not isinstance(entry, dict):
                    continue
                file_name = entry.get("file")
                if not file_name:
                    continue
                path = self.images_dir / str(file_name)
                if not path.is_file():
                    continue
                resolved[name] = _Resolved(
                    path=path,
                    mime=str(entry.get("mime") or "application/octet-stream"),
                    source=str(entry.get("source") or "unknown"),
                    animated=bool(entry.get("animated")),
                )
        resolved.update(self._scan_custom())
        return resolved

    def entries(self) -> list[dict]:
        resolved = self._resolve()
        return [
            {
                "name": name,
                "url": f"/emotes/images/{quote(name, safe='')}",
                "source": item.source,
                "animated": item.animated,
            }
            for name, item in sorted(resolved.items(), key=lambda pair: pair[0])
        ]

    def counts(self) -> dict[str, int]:
        counts = {source: 0 for source in SOURCE_ORDER}
        counts["custom"] = 0
        for item in self._resolve().values():
            counts[item.source] = counts.get(item.source, 0) + 1
        return counts

    def image(self, name: str) -> Optional[_Resolved]:
        """只按名字查内存索引；调用方永远不会把 name 拼进文件系统路径。"""
        return self._resolve().get(name)

    def snapshot(self) -> dict:
        resolved_entries = self.entries()
        return {
            "emotes": resolved_entries,
            "updated_at": self._manifest.get("updated_at"),
            "counts": self.counts(),
            "last_error": self._manifest.get("last_error"),
        }

    # ---------- 刷新 ----------

    async def refresh(self, reason: str = "manual") -> dict:
        if self._lock.locked():
            raise RefreshBusy("表情刷新已在进行中")
        async with self._lock:
            return await self._refresh_locked(reason)

    async def _refresh_locked(self, reason: str) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.custom_dir.mkdir(parents=True, exist_ok=True)

        old = self._manifest
        old_sources = old.get("sources") if isinstance(old.get("sources"), dict) else {}
        old_emotes = old.get("emotes") if isinstance(old.get("emotes"), dict) else {}

        fresh: Dict[str, _Candidate] = {}
        sources: dict[str, dict] = {}
        errors: list[str] = []
        failed_sources: set[str] = set()

        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            for source, url in GLOBAL_SOURCES:
                try:
                    payload = await self._get_json(client, url)
                    parsed = {
                        "7tv_global": lambda p: _parse_seven_tv(p, "7tv_global"),
                        "bttv_global": lambda p: _parse_bttv(p),
                        "ffz_global": lambda p: _parse_ffz(p),
                    }[source](payload)
                    sources[source] = {
                        "count": len(parsed),
                        "fetched_at": _local_iso(),
                        "error": None,
                    }
                    for name, candidate in parsed.items():
                        fresh.setdefault(name, candidate)
                except Exception as e:
                    failed_sources.add(source)
                    previous = old_sources.get(source)
                    if isinstance(previous, dict):
                        current = dict(previous)
                    else:
                        current = {"count": 0, "fetched_at": None}
                    current["error"] = f"{type(e).__name__}: {e}"
                    sources[source] = current
                    errors.append(f"{source}: {current['error']}")
                    logger.warning("表情源 %s 刷新失败: %s", source, current["error"])

            missing: list[str] = []
            search_error: Optional[str] = None
            search_found = 0
            for name in EMOTE_CANON.values():
                if name in fresh:
                    continue
                try:
                    payload = await self._search(client, name)
                    candidate = _parse_search(payload, name)
                except Exception as e:
                    search_error = f"{type(e).__name__}: {e}"
                    missing.append(name)
                    continue
                if candidate is None:
                    missing.append(name)
                else:
                    fresh[name] = candidate
                    search_found += 1

        if search_error is not None:
            failed_sources.add("7tv_search")
            previous = old_sources.get("7tv_search")
            if isinstance(previous, dict):
                sources["7tv_search"] = dict(previous)
            else:
                sources["7tv_search"] = {"count": 0}
            sources["7tv_search"]["error"] = search_error
            sources["7tv_search"]["missing"] = missing
            errors.append(f"7tv_search: {search_error}")
        else:
            sources["7tv_search"] = {"count": search_found, "missing": missing}

        custom = self._scan_custom()
        merged: Dict[str, _Candidate] = {}
        for name, item in custom.items():
            merged[name] = _Candidate(
                name=name,
                source="custom",
                emote_id="",
                url="",
                ext=item.path.suffix.lower(),
                mime=item.mime,
                animated=item.animated,
            )
        for name, candidate in fresh.items():
            merged.setdefault(name, candidate)

        # 单源失败时保留旧清单该源条目（不拿空清单覆盖好数据）
        for name, entry in old_emotes.items():
            if name in merged or not isinstance(entry, dict):
                continue
            if str(entry.get("source") or "") not in failed_sources:
                continue
            file_name = str(entry.get("file") or "")
            if not file_name or not (self.images_dir / file_name).is_file():
                continue
            merged[name] = _Candidate(
                name=name,
                source=str(entry.get("source")),
                emote_id=str(entry.get("id") or ""),
                url="",
                ext=Path(file_name).suffix.lower(),
                mime=str(entry.get("mime") or "application/octet-stream"),
                animated=bool(entry.get("animated")),
            )

        downloaded: dict[str, str] = {}
        pending: list[tuple[_Candidate, Path]] = []
        download_failures: list[str] = []

        for name, candidate in merged.items():
            if candidate.source == "custom":
                continue
            target = self.images_dir / f"{_safe_name(name)}{candidate.ext}"
            previous = old_emotes.get(name)
            if (
                isinstance(previous, dict)
                and previous.get("source") == candidate.source
                and str(previous.get("id") or "") == candidate.emote_id
                and previous.get("file") == target.name
                and target.is_file()
            ):
                downloaded[name] = str(previous.get("mime") or candidate.mime)
                continue
            pending.append((candidate, target))

        if pending:
            semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

            async def download(client, candidate: _Candidate, target: Path) -> None:
                async with semaphore:
                    try:
                        mime = await self._download(client, candidate, target)
                    except Exception as e:
                        download_failures.append(candidate.name)
                        logger.warning(
                            "表情图片下载失败 %s（%s）: %s: %s",
                            candidate.name,
                            candidate.source,
                            type(e).__name__,
                            e,
                        )
                        return
                    downloaded[candidate.name] = mime

            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_S,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as download_client:
                await asyncio.gather(
                    *(
                        asyncio.create_task(download(download_client, candidate, target))
                        for candidate, target in pending
                    )
                )

        manifest_emotes: dict[str, dict] = {}
        for name, candidate in merged.items():
            if candidate.source == "custom":
                continue
            if name not in downloaded:
                continue
            manifest_emotes[name] = {
                "file": f"{_safe_name(name)}{candidate.ext}",
                "source": candidate.source,
                "id": candidate.emote_id,
                "animated": candidate.animated,
                "mime": downloaded[name],
            }

        if download_failures:
            errors.append(f"download: {len(download_failures)} failed")
        manifest = {
            "version": 1,
            "updated_at": _local_iso(),
            "last_error": "; ".join(errors) if errors else None,
            "sources": sources,
            "emotes": manifest_emotes,
        }
        self._write_manifest(manifest)
        self._manifest = manifest
        logger.info(
            "表情清单刷新完成（%s）：%d 个表情，%d 张图片下载/复用",
            reason,
            len(manifest_emotes),
            len(downloaded),
        )
        return {
            "status": "ok",
            "sources": sources,
            "emote_count": len(self.entries()),
        }

    async def _get_json(self, client, url: str) -> Any:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    async def _search(self, client, name: str) -> Any:
        response = await client.post(
            SEVEN_TV_GQL,
            json={"query": SEVEN_TV_SEARCH_QUERY, "variables": {"q": name}},
        )
        response.raise_for_status()
        return response.json()

    async def _download(self, client, candidate: _Candidate, target: Path) -> str:
        last: Optional[Exception] = None
        for attempt in range(RETRY_COUNT):
            try:
                response = await client.get(candidate.url)
                response.raise_for_status()
                content = getattr(response, "content", None)
                if not isinstance(content, (bytes, bytearray)) or not content:
                    raise ValueError("空响应")
                header = ""
                headers = getattr(response, "headers", None)
                if headers is not None:
                    header = str(headers.get("content-type") or "")
                mime = header.split(";", 1)[0].strip() if header.startswith("image/") else candidate.mime
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_bytes(bytes(content))
                os.replace(tmp, target)
                return mime
            except Exception as e:  # 单图失败不中断整体
                last = e
                if attempt + 1 < RETRY_COUNT:
                    await _sleep(2**attempt)
        raise last if last is not None else RuntimeError("下载失败")

    def _write_manifest(self, manifest: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_name(self.manifest_path.name + ".tmp")
        tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.manifest_path)

    # ---------- 生命周期 ----------

    def _load_manifest(self) -> None:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning("表情 manifest 读取失败（将后台重建）: %s", e)
            return
        if isinstance(data, dict):
            self._manifest = data

    def _needs_refresh(self) -> bool:
        updated = self._manifest.get("updated_at")
        if not updated:
            return True
        try:
            moment = datetime.fromisoformat(str(updated))
        except ValueError:
            return True
        if moment.tzinfo is None:
            moment = moment.astimezone()
        return datetime.now().astimezone() - moment > timedelta(seconds=REFRESH_S)

    async def startup(self) -> None:
        """加载旧清单后立刻返回；缺失/过期才在后台刷新（不阻塞启动）。"""
        self._load_manifest()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="emotes-refresh")

    async def _run(self) -> None:
        try:
            if self._needs_refresh():
                try:
                    await self.refresh("startup")
                except RefreshBusy:
                    pass
                except Exception:
                    logger.exception("表情后台刷新失败（启动）")
            while True:
                await asyncio.sleep(REFRESH_S)
                try:
                    await self.refresh("daily")
                except RefreshBusy:
                    pass
                except Exception:
                    logger.exception("表情后台刷新失败（每日）")
        except asyncio.CancelledError:
            raise

    async def shutdown(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
