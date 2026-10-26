"""mock 模式的示例图：纯代码绘制像素小人，不调用任何生图接口。

从提示词里解析英雄、职业、动作和帧序号，在 32×32 网格上画角色，再按最近邻放大到目标尺寸：
- 英雄名决定配色，职业决定武器，动作决定姿势，帧序号决定动作阶段；
- 5 种风格对应不同的背景和调色方式；
- 风格母版画成一张样张：同一角色的三个姿态 + 调色板。
"""
import colorsys
import hashlib
import io
import math
import random
import re
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from app.agents.styles import STYLES

GRID = 32

_HERO_RE = re.compile(r"([^，,（(：]+)（([^）]+)）")
_ACTION_RE = re.compile(r"动作：([^，]+?)(?:\s*第(\d+)/(\d+)帧)?(?:，|$)")

# 关键词按顺序匹配，"受击"要排在"攻击"前面
ROLE_WEAPON = [
    (("法师", "术士", "巫", "牧师", "魔", "祭司", "召唤", "道士"), "staff"),
    (("射手", "弓", "猎人", "游侠"), "bow"),
    (("坦克", "盾", "守护", "圣骑"), "shield"),
    (("刺客", "忍", "盗", "影"), "dagger"),
]
ACTION_POSE = [
    (("受击", "受伤", "被击"), "hurt"),
    (("死亡", "倒地", "阵亡"), "death"),
    (("防御", "格挡", "举盾"), "block"),
    (("施法", "释放", "技能", "魔法", "吟唱", "召唤"), "cast"),
    (("射", "拉弓"), "shoot"),
    (("攻击", "砍", "劈", "斩", "挥", "刺", "击", "打"), "attack"),
    (("行走", "走", "跑", "奔", "移动"), "walk"),
    (("跳",), "jump"),
]


@dataclass
class SpriteInfo:
    hero: str
    role: str
    action: str
    frame: int          # 从 0 开始
    frames: int
    style: str
    is_master: bool


@dataclass
class Palette:
    skin: tuple
    hair: tuple
    main: tuple
    main_dark: tuple
    second: tuple
    accent: tuple
    pants: tuple
    boots: tuple
    outline: tuple
    metal: tuple
    wood: tuple
    glow: tuple


def parse(prompt: str, label: str) -> SpriteInfo:
    is_master = label.startswith("MASTER")
    style = label.split()[-1] if is_master and label.split()[-1] in STYLES else next(
        (s.key for s in STYLES.values() if prompt.startswith(s.prefix)), "pixel")
    m = _HERO_RE.search(prompt)
    hero, role = (m.group(1).strip(), m.group(2).strip()) if m else ((label.split() or ["角色"])[0], "通用")
    a = _ACTION_RE.search(prompt)
    action = a.group(1).strip() if a else "展示"
    frame, frames = (int(a.group(2)) - 1, int(a.group(3))) if a and a.group(2) else (0, 1)
    return SpriteInfo(hero, role, action, frame, frames, style, is_master)


def _pick(text: str, table: list, default: str) -> str:
    return next((kind for words, kind in table if any(w in text for w in words)), default)


def _hsv(h: float, s: float, v: float, a: int = 255) -> tuple:
    r, g, b = colorsys.hsv_to_rgb(h % 1, max(0.0, min(s, 1.0)), max(0.0, min(v, 1.0)))
    return int(r * 255), int(g * 255), int(b * 255), a


def make_palette(hero: str, style: str, tweak: float = 0.0) -> Palette:
    d = hashlib.md5(hero.encode()).digest()
    h, h2 = d[0] / 255, d[0] / 255 + 0.35 + d[1] / 255 * 0.3
    sat, val = {"realistic": (0.45, 0.7), "ink": (0.06, 0.4), "anime": (0.6, 0.95), "chibi": (0.55, 0.98)}.get(style, (0.7, 0.85))
    skins = [(255, 218, 178, 255), (243, 196, 152, 255), (226, 174, 130, 255), (196, 140, 100, 255)]
    ink = style == "ink"
    return Palette(
        skin=(236, 228, 214, 255) if ink else skins[d[2] % 4],
        hair=_hsv(d[3] / 255, 0 if ink else 0.6, 0.15 if ink else 0.3 + d[4] / 255 * 0.55),
        main=_hsv(h, sat, val),
        main_dark=_hsv(h, sat, val * 0.68),
        second=_hsv(h2, sat * 0.8, val * 0.88),
        accent=(200, 40, 40, 255) if ink else _hsv(h + 0.5 + tweak, 0.85, 1.0),
        pants=_hsv(h2, 0.3 if not ink else 0, 0.32),
        boots=(40, 40, 40, 255) if ink else (78, 54, 42, 255),
        outline=(20, 20, 20, 255) if ink else (34, 26, 40, 255),
        metal=(170, 170, 170, 255) if ink else (205, 212, 222, 255),
        wood=(90, 80, 70, 255) if ink else (139, 94, 52, 255),
        glow=(120, 120, 120, 255) if ink else _hsv(h + 0.5 + tweak, 0.45, 1.0),
    )


# ---------------- 角色绘制 ----------------

def _rect(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, c: tuple) -> None:
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=c)


def _weapon(d: ImageDraw.ImageDraw, kind: str, hand: tuple, direction: tuple, pal: Palette, frame: int) -> None:
    hx, hy = hand
    if kind in ("sword", "dagger"):
        length = 9 if kind == "sword" else 4
        dx, dy = direction
        n = math.hypot(dx, dy) or 1
        ux, uy = dx / n, dy / n
        tip = (round(hx + ux * length), round(hy + uy * length))
        base = (round(hx + ux * 2), round(hy + uy * 2))
        d.line([base, tip], fill=pal.metal, width=1)
        d.point((round(hx + ux * 3), round(hy + uy * 3)), fill=(255, 255, 255, 255))
        px, py = round(-uy), round(ux)   # 护手垂直于剑身
        d.line([(base[0] - px, base[1] - py), (base[0] + px, base[1] + py)], fill=pal.accent, width=1)
    elif kind == "staff":
        d.line([(hx, hy - 11), (hx, hy + 5)], fill=pal.wood, width=1)
        r = 2 if frame % 2 else 1
        d.ellipse([hx - r, hy - 13 - r, hx + r, hy - 13 + r], fill=pal.glow)
        d.point((hx, hy - 13), fill=(255, 255, 255, 255))
    elif kind == "bow":
        pts = [(hx, hy - 6), (hx + 1, hy - 5), (hx + 2, hy - 3), (hx + 2, hy + 3), (hx + 1, hy + 5), (hx, hy + 6)]
        d.line(pts, fill=pal.wood, width=1)
    elif kind == "shield":
        _rect(d, hx, hy - 4, 5, 8, pal.metal)
        _rect(d, hx + 1, hy - 3, 3, 6, pal.main_dark)
        d.line([(hx + 2, hy - 2), (hx + 2, hy + 1)], fill=pal.accent)
        d.line([(hx + 1, hy - 1), (hx + 3, hy - 1)], fill=pal.accent)
    else:  # 徒手：拳套
        _rect(d, hx - 1, hy - 1, 2, 2, pal.accent)


def _pose(pose: str, t: int, n: int) -> dict:
    """返回各部位位置。角色面朝右，后手在左、前手在右。"""
    c = t % 4
    ph = t / (n - 1) if n > 1 else 0.5
    p = dict(bob=0, lift=0, lean=0, legs=(0, 0), back=(11, 22), front=(21, 21), wdir=(1, 2), fx=[], tint=0.0, fall=0.0)
    if pose == "idle":
        p["bob"] = [0, 0, 1, 1][c]
    elif pose == "walk":
        sw = [-1, 0, 1, 0][c]
        p.update(bob=[0, 1, 0, 1][c], legs=(sw, -sw), back=(11 - sw, 21), front=(21 + sw, 21))
    elif pose == "attack":
        if ph < 0.34:
            p.update(front=(17, 11), wdir=(-1, -2))
        elif ph < 0.67:
            p.update(front=(24, 15), wdir=(3, -1), lean=1, fx=["slash"])
        else:
            p.update(front=(23, 21), wdir=(1, 2), lean=1)
    elif pose == "cast":
        hy = 21 - round(7 * ph)
        p.update(back=(12, hy + 1), front=(20, hy), fx=["particles"] + (["orb"] if t == n - 1 else []))
    elif pose == "shoot":
        p.update(front=(25, 17), back=(22 - round(5 * ph), 17), fx=["arrow_fly" if t == n - 1 and n > 1 else "arrow"])
    elif pose == "jump":
        p.update(lift=[0, 3, 5, 2][c], legs=(1, -1), back=(10, 17), front=(22, 16))
    elif pose == "hurt":
        p.update(lean=-1, back=(9, 15), front=(22, 13), fx=["impact"], tint=0.55 if t % 2 == 0 else 0.0)
    elif pose == "death":
        p.update(lean=-1, back=(9, 15), front=(22, 14), fall=max(0.0, (ph - 0.4) / 0.6))
    elif pose == "block":
        p.update(front=(22, 17), wdir=(0, -3), fx=["spark"] if c in (1, 2) else [])
    return p


def draw_character(info: SpriteInfo, pal: Palette, rng: random.Random, pose_name: str | None = None,
                   frame: int | None = None, frames: int | None = None) -> tuple[Image.Image, Image.Image, int]:
    """返回 (角色层, 特效层, 腾空高度)。角色层已加描边。"""
    weapon = _pick(info.role, ROLE_WEAPON, "sword" if "战" in info.role or "剑" in info.role else "fist")
    pose_name = pose_name or _pick(info.action, ACTION_POSE, "idle")
    if pose_name == "shoot" and weapon != "bow":
        pose_name = "cast" if weapon == "staff" else "attack"
    t, n = (info.frame if frame is None else frame), (info.frames if frames is None else frames)
    p = _pose(pose_name, t, n)
    chibi = info.style == "chibi"

    body = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    b, lx = p["bob"], p["lean"]

    # 腿和靴子（不随呼吸起伏）
    leg_top, leg_h = (22, 5) if chibi else (23, 5)
    for i, (x0, off) in enumerate(((13, p["legs"][0]), (17, p["legs"][1]))):
        x = x0 + off
        tuck = 1 if p["lift"] and i == 1 else 0
        _rect(d, x, leg_top, 3, leg_h - tuck, pal.pants if i else _shade(pal.pants, 0.8))
        _rect(d, x + (1 if i else 0), leg_top + leg_h - tuck, 3, 2, pal.boots)

    # 后手
    sh_back, sh_front = (12 + lx, 16 + b), (19 + lx, 16 + b)
    back = (p["back"][0], p["back"][1] + b)
    d.line([sh_back, back], fill=_shade(pal.second, 0.8), width=2)
    _rect(d, back[0] - 1, back[1], 2, 2, pal.skin)

    # 躯干：主色 + 腰带 + 高光
    torso_y, torso_h = (15, 7) if chibi else (15, 8)
    _rect(d, 12 + lx, torso_y + b, 8, torso_h, pal.main)
    _rect(d, 18 + lx, torso_y + b, 2, torso_h, pal.main_dark)
    _rect(d, 12 + lx, torso_y + torso_h - 2 + b, 8, 1, pal.accent)
    _rect(d, 15 + lx, torso_y + b, 2, 3, pal.second)

    # 头和头发
    hx, hy, hw, hh = (10, 3, 12, 12) if chibi else (11, 5, 10, 10)
    hx += lx
    hy += b
    _rect(d, hx, hy, hw, hh, pal.skin)
    _rect(d, hx - 1, hy - 1, hw + 2, 4, pal.hair)
    _rect(d, hx - 1, hy, 3, hh - 2, pal.hair)
    _rect(d, hx + hw - 3, hy + 2, 2, 1, pal.hair)
    ey = hy + (6 if chibi else 5)
    for ex in ((hx + hw - 6, hx + hw - 3) if chibi else (hx + hw - 5, hx + hw - 2)):
        _rect(d, ex, ey, 2 if chibi else 1, 2, pal.outline)
        if chibi:
            d.point((ex, ey), fill=(255, 255, 255, 255))
    if info.style in ("anime", "chibi"):
        d.point((hx + hw - 2, ey + 2), fill=(255, 150, 160, 255))

    # 前手 + 武器
    front = (p["front"][0], p["front"][1] + b)
    d.line([sh_front, front], fill=pal.second, width=2)
    if pose_name == "shoot":
        bow_hand = front
        _weapon(d, "bow", bow_hand, (0, 0), pal, t)
        draw_pt = (p["back"][0], p["back"][1] + b)
        d.line([(bow_hand[0], bow_hand[1] - 6), draw_pt, (bow_hand[0], bow_hand[1] + 6)], fill=(235, 235, 235, 255))
    else:
        _weapon(d, weapon, front, p["wdir"], pal, t)
    _rect(d, front[0] - 1, front[1], 2, 2, pal.skin)

    if p["tint"]:
        white = Image.new("RGBA", body.size, (255, 255, 255, 255))
        white.putalpha(body.getchannel("A"))
        body = Image.blend(body, white, p["tint"])

    body = _outline(body, pal.outline)
    if p["fall"]:
        angle = 90 * min(1.0, p["fall"])
        body = body.rotate(angle, resample=Image.NEAREST, center=(16, 29))
        if p["fall"] >= 1:
            body.putalpha(body.getchannel("A").point(lambda a: int(a * 0.7)))

    fx = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    _effects(ImageDraw.Draw(fx), p["fx"], pal, rng, front, (p["back"][0], p["back"][1] + b), t, n)
    return body, fx, p["lift"]


def _shade(c: tuple, k: float) -> tuple:
    return int(c[0] * k), int(c[1] * k), int(c[2] * k), c[3]


def _outline(layer: Image.Image, color: tuple) -> Image.Image:
    alpha = layer.getchannel("A").point(lambda a: 255 if a > 0 else 0)
    ring = ImageChops.subtract(alpha.filter(ImageFilter.MaxFilter(3)), alpha)
    out = Image.new("RGBA", layer.size, color)
    out.putalpha(ring)
    out.alpha_composite(layer)
    return out


def _effects(d: ImageDraw.ImageDraw, fx: list, pal: Palette, rng: random.Random, front: tuple, back: tuple, t: int, n: int) -> None:
    if "slash" in fx:
        for i in range(12):
            a = math.radians(-70 + i * 12)
            for r in (10, 11):
                d.point((round(16 + r * math.cos(a)), round(16 + r * math.sin(a))), fill=(255, 255, 255, 230) if r == 10 else pal.glow)
    if "particles" in fx:
        for _ in range(4 + int(10 * (t + 1) / max(n, 1))):
            a, r = rng.uniform(0, 2 * math.pi), rng.uniform(5, 10)
            d.point((round(16 + r * math.cos(a)), round(9 + r * math.sin(a) * 0.7)), fill=rng.choice([pal.glow, pal.accent, (255, 255, 255, 255)]))
    if "orb" in fx:
        d.ellipse([13, 0, 19, 6], fill=pal.glow)
        d.ellipse([15, 2, 17, 4], fill=(255, 255, 255, 255))
    if "arrow" in fx:
        d.line([back, (front[0] + 3, front[1])], fill=pal.metal)
    if "arrow_fly" in fx:
        d.line([(27, 17), (31, 17)], fill=pal.metal)
        for y in (15, 19):
            d.line([(20, y), (24, y)], fill=(255, 255, 255, 160))
    if "impact" in fx:
        for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (2, 2), (-2, -2), (2, -2), (-2, 2)):
            d.point((25 + dx, 10 + dy), fill=(255, 230, 90, 255) if abs(dx) < 2 else (255, 255, 255, 255))
    if "spark" in fx:
        for dx, dy in ((0, 0), (1, -1), (1, 1), (2, 0)):
            d.point((front[0] + 6 + dx, front[1] - 2 + dy), fill=(255, 240, 150, 255))


# ---------------- 背景 ----------------

def background(style: str, pal: Palette, size: int, rng: random.Random, ground: int) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    hue = colorsys.rgb_to_hsv(*(c / 255 for c in pal.main[:3]))[0]
    if style == "ink":
        _gradient(d, size, (240, 233, 216), (228, 219, 198))
        for _ in range(size // 6):
            x, y, r = rng.randrange(size), rng.randrange(size), rng.randint(1, 3)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(200, 192, 176, 255))
        _rect(d, size - 5, size - 5, 3, 3, (190, 40, 40, 255))
    elif style == "realistic":
        _gradient(d, size, (46, 52, 70), (16, 18, 26))
        d.ellipse([size // 2 - 9, ground - 2, size // 2 + 9, ground + 2], fill=_hsv(hue + 0.5, 0.4, 0.45))
    elif style == "anime":
        _gradient(d, size, _hsv(hue + 0.5, 0.18, 1.0)[:3], _hsv(hue + 0.55, 0.32, 0.95)[:3])
        for _ in range(size // 5):
            d.point((rng.randrange(size), rng.randrange(size // 2)), fill=(255, 255, 255, 255))
    elif style == "chibi":
        _gradient(d, size, _hsv(hue + 0.15, 0.15, 1.0)[:3], _hsv(hue + 0.15, 0.22, 1.0)[:3])
        for y in range(2, size, 6):
            for x in range(2 + (y // 6 % 2) * 3, size, 6):
                d.point((x, y), fill=_hsv(hue + 0.15, 0.3, 0.98))
    else:  # pixel
        _rect(d, 0, 0, size, size, _hsv(hue + 0.5, 0.2, 0.96))
        _rect(d, 0, ground + 1, size, size - ground - 1, _hsv(hue + 0.4, 0.35, 0.75))
        _rect(d, 0, ground + 1, size, 1, _hsv(hue + 0.4, 0.45, 0.6))
        for x in range(1, size, 4):
            d.point((x, ground + 3 + (x // 4) % 2), fill=_hsv(hue + 0.4, 0.45, 0.65))
    return img


def _gradient(d: ImageDraw.ImageDraw, size: int, top: tuple, bottom: tuple) -> None:
    for y in range(size):
        k = y / max(size - 1, 1)
        d.line([(0, y), (size, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3)) + (255,))


def _shadow(img: Image.Image, cx: int, ground: int, lift: int) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    w = max(3, 7 - lift)
    ImageDraw.Draw(layer).ellipse([cx - w, ground - 1, cx + w, ground + 1], fill=(0, 0, 0, 70))
    img.alpha_composite(layer)


def _place(canvas: Image.Image, info: SpriteInfo, pal: Palette, rng: random.Random, x: int, y: int, **pose) -> None:
    body, fx, lift = draw_character(info, pal, rng, **pose)
    _shadow(canvas, x + 16, y + 29, lift)
    canvas.alpha_composite(body, (x, y - lift))
    canvas.alpha_composite(fx, (x, y - lift))


# ---------------- 入口 ----------------

def render(prompt: str, seed: int, label: str, size: tuple[int, int]) -> bytes:
    info = parse(prompt, label)
    rng = random.Random(f"{prompt}|{seed}")
    pal = make_palette(info.hero, info.style, tweak=(seed % 7) * 0.02)
    if info.is_master:
        canvas = _master_sheet(info, pal, rng)
    else:
        canvas = background(info.style, pal, GRID, rng, ground=29)
        _place(canvas, info, pal, rng, 0, 0)
    buf = io.BytesIO()
    canvas.convert("RGB").resize(size, Image.NEAREST).save(buf, format="PNG")
    return buf.getvalue()


def _master_sheet(info: SpriteInfo, pal: Palette, rng: random.Random) -> Image.Image:
    """风格样张：上方是待机展示，下方是同一角色的攻击和施法/射击姿态，右上角是调色板。"""
    size = GRID * 2
    canvas = background(info.style, pal, size, rng, ground=size - 3)
    weapon = _pick(info.role, ROLE_WEAPON, "sword")
    second_pose = "shoot" if weapon == "bow" else "cast"
    _place(canvas, info, pal, rng, 14, 2, pose_name="idle", frame=0, frames=4)
    _place(canvas, info, pal, rng, 0, 31, pose_name="attack", frame=1, frames=3)
    _place(canvas, info, pal, rng, 32, 31, pose_name=second_pose, frame=2, frames=3)
    d = ImageDraw.Draw(canvas)
    for i, c in enumerate((pal.main, pal.second, pal.accent, pal.hair, pal.skin)):
        _rect(d, 52, 4 + i * 5, 5, 4, pal.outline)
        _rect(d, 53, 5 + i * 5, 3, 2, c)
    return canvas
