"""mock 模式下示例需求的预制图。

在线演示里访客最常点的是"使用示例需求"，这套需求（亚瑟 / 梅林 / 罗宾，像素风）配了一组预先画好的图，
放在 app/static/examples/frames/，由 tools/slice_examples.py 从原稿切出。命中时直接返回这些图，
没命中（别的英雄、动作或画风）返回 None，交给 pixel_sprites 用代码现画。
"""
import io
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from app.ai import pixel_sprites

FRAMES_DIR = Path(__file__).resolve().parent.parent / "static" / "examples" / "frames"
MASTER_HERO = "亚瑟"
SHEETS = {
    ("亚瑟", "待机"): "arthur_idle",
    ("亚瑟", "攻击"): "arthur_attack",
    ("梅林", "待机"): "merlin_idle",
    ("梅林", "施法"): "merlin_cast",
    ("罗宾", "行走"): "robin_walk",
    ("罗宾", "射击"): "robin_shoot",
}


def _file_for(prompt: str, label: str) -> Path | None:
    info = pixel_sprites.parse(prompt, label)
    if info.style != "pixel":
        return None
    if info.is_master:
        if info.hero != MASTER_HERO:
            return None
        version = int(label.split()[1].lstrip("v") or 1)
        return FRAMES_DIR / f"master_v{(version - 1) % 2 + 1}.png"
    sheet = SHEETS.get((info.hero, info.action))
    return FRAMES_DIR / f"{sheet}_{info.frame % 4 + 1}.png" if sheet else None


def lookup(prompt: str, seed: int, label: str, size: tuple[int, int]) -> bytes | None:
    path = _file_for(prompt, label)
    if path is None or not path.exists():
        return None
    img = Image.open(path).convert("RGB")
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    # 重生成时画面不变，但写入提示词和 seed，让每个版本的文件内容不同（mock 审查按内容哈希打分）
    meta = PngInfo()
    meta.add_text("prompt", prompt)
    meta.add_text("seed", str(seed))
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()
