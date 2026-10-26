"""把示例图原稿切成单帧，供 mock 模式的示例需求使用。

原稿放在 app/static/examples/gpt/（不提交到仓库）：
- master_v1.png / master_v2.png：风格母版，单个角色
- {英雄}_{动作}.png：横版序列帧，4 帧从左到右排成一行

输出到 app/static/examples/frames/，每帧居中裁成正方形并统一缩放，同一张原稿的各帧共用同一高度范围，播放时不会上下跳。
用法：python tools/slice_examples.py（只在本地准备素材时用，需要 numpy、scipy 和 pillow，不属于运行时依赖）
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent / "app" / "static" / "examples"
SRC, DST = ROOT / "gpt", ROOT / "frames"
SIZE = 512
FRAMES = 4


def background(arr: np.ndarray) -> np.ndarray:
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
    return np.median(border, axis=0)


def foreground(arr: np.ndarray, bg: np.ndarray, threshold: int = 60) -> np.ndarray:
    return np.abs(arr.astype(int) - bg).sum(axis=2) > threshold


def cut_points(mask: np.ndarray, n: int) -> list[int]:
    """在每个等分点附近找前景最少的列作为切口，特效跨过帧间空白时也能切开。"""
    w = mask.shape[1]
    cols = np.convolve(mask.sum(axis=0), np.ones(9) / 9, mode="same")
    cuts = [0]
    for k in range(1, n):
        lo, hi = int(w * (k / n - 1 / (2 * n))), int(w * (k / n + 1 / (2 * n)))
        x = np.arange(lo, hi)
        cuts.append(int(x[np.argmin(cols[lo:hi] + np.abs(x - w * k / n) * 1e-3)]))
    return cuts + [w]


def drop_edge_fragments(mask: np.ndarray, left_inner: bool, right_inner: bool) -> np.ndarray:
    """去掉从相邻帧越界过来的特效碎片：靠近帧间切口（边缘 15% 以内）的小连通块。

    只处理帧与帧之间的切口；整张图的最左、最右边不处理，最后一帧飞出去的箭之类会保留。
    """
    labels, n = ndimage.label(ndimage.binary_dilation(mask, iterations=3))
    if n < 2:
        return mask
    w = mask.shape[1]
    areas = ndimage.sum(mask, labels, range(1, n + 1))
    keep = mask.copy()
    for i, sl in enumerate(ndimage.find_objects(labels), 1):
        cx = (sl[1].start + sl[1].stop) / 2
        near_cut = (left_inner and cx < w * 0.15) or (right_inner and cx > w * 0.85)
        if near_cut and areas[i - 1] < areas.max() * 0.15:
            keep[labels == i] = False
    return keep


def square(img: Image.Image, bg: tuple, cx: float, cy: float, side: float) -> Image.Image:
    half = side / 2
    box = tuple(int(round(v)) for v in (cx - half, cy - half, cx + half, cy + half))
    out = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), bg)
    out.paste(img, (-box[0], -box[1]))
    # 像素风颜色本来就少，压到 256 色几乎看不出差别，文件小三分之二
    return out.resize((SIZE, SIZE), Image.LANCZOS).quantize(256, dither=Image.Dither.NONE)


def slice_sheet(path: Path) -> list[Image.Image]:
    img = Image.open(path).convert("RGB")
    arr = np.asarray(img)
    bg = background(arr)
    mask = foreground(arr, bg)
    # 剑光这类浅色特效和背景色差小，判断碎片时用更低的阈值
    soft = foreground(arr, bg, 20)
    cuts = cut_points(mask, FRAMES)
    boxes, dropped = [], []
    for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
        kept = drop_edge_fragments(soft[:, a:b], left_inner=k > 0, right_inner=k < FRAMES - 1)
        ys, xs = np.nonzero(mask[:, a:b] & kept)
        boxes.append((a + xs.min(), ys.min(), a + xs.max(), ys.max()))
        # 连带碎片边缘的过渡像素一起抹掉
        dropped.append(ndimage.binary_dilation(soft[:, a:b] & ~kept, iterations=2))
    y0, y1 = min(bx[1] for bx in boxes), max(bx[3] for bx in boxes)
    side = max(y1 - y0, max(bx[2] - bx[0] for bx in boxes)) * 1.12
    fill = tuple(int(v) for v in bg)
    frames = []
    for (a, b), bx, drop in zip(zip(cuts, cuts[1:]), boxes, dropped):
        part = arr.copy()
        part[:, :a] = bg
        part[:, b:] = bg
        part[:, a:b][drop] = bg
        frames.append(square(Image.fromarray(part), fill, (bx[0] + bx[2]) / 2, (y0 + y1) / 2, side))
    return frames


def slice_single(path: Path) -> Image.Image:
    img = Image.open(path).convert("RGB")
    arr = np.asarray(img)
    bg = background(arr)
    ys, xs = np.nonzero(foreground(arr, bg))
    side = max(ys.max() - ys.min(), xs.max() - xs.min()) * 1.25
    return square(img, tuple(int(v) for v in bg), (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, side)


def main() -> None:
    DST.mkdir(exist_ok=True)
    for path in sorted(SRC.glob("*.png")):
        if path.stem.startswith("master"):
            slice_single(path).save(DST / path.name, optimize=True)
            print(path.name)
            continue
        for i, frame in enumerate(slice_sheet(path), 1):
            frame.save(DST / f"{path.stem}_{i}.png", optimize=True)
        print(path.name, "->", FRAMES, "帧")


if __name__ == "__main__":
    main()
