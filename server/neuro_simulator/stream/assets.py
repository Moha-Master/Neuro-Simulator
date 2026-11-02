"""stream 模块的资产（背景图等场景素材）：包内模板 + 工作目录 + `/ui/assets`。

**目录布局**（设计定稿 §5）：
- 包内模板 `stream/assets/`（`images/` 放内置图）——随 wheel 分发；
- 首启复制到 `<workdir>/stream/assets/`（只补缺失，与 neuro ui_media 的 deploy
  同一套做法）；上传按类别落 `images/ | videos/ | html/`；
- 工作目录是权威：`GET /ui/assets/{path}` 只从工作目录读，用户换素材不用动包。

机制（白名单 / SSRF / 路由形状）在 `..asset_kit`，这里只写 stream 自己的策略：
包内模板位置与工作目录。neuro 模块用同一个 kit、自己的目录（形状对称）。
"""

from pathlib import Path

from .. import asset_kit

PACKAGE_ASSETS = Path(__file__).parent / "assets"


def package_assets_dir() -> Path:
    """包内模板目录（只读，deploy 的来源）。"""
    return PACKAGE_ASSETS


def assets_root(data_dir: Path) -> Path:
    """工作目录里的资产根：`<workdir>/stream/assets/`。"""
    return Path(data_dir) / "assets"


def deploy_assets(data_dir: Path) -> asset_kit.AssetStore:
    """首启部署（只补缺失）并返回绑定工作目录的 store。"""
    store = asset_kit.AssetStore(PACKAGE_ASSETS, assets_root(data_dir))
    store.deploy()
    return store
