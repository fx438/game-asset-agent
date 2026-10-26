"""风格母版模板库。prefix 会固定拼在每一张图的提示词最前面，是风格锁定的第一道保证。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class StyleTemplate:
    key: str
    name: str
    description: str
    prefix: str
    negative: str


_COMMON_NEGATIVE = "低质量，模糊，畸形，多余的手指，多余的肢体，肢体残缺，文字，水印，签名，裁切不完整"

STYLES: dict[str, StyleTemplate] = {
    s.key: s
    for s in [
        StyleTemplate(
            "pixel", "像素风", "16-bit 复古像素游戏角色，色块清晰，适合横版动作游戏",
            "16-bit 像素艺术游戏角色精灵图，清晰像素边缘，有限调色板，纯色背景，全身，居中",
            _COMMON_NEGATIVE + "，写实，3D渲染，渐变模糊，抗锯齿过度",
        ),
        StyleTemplate(
            "anime", "二次元", "日系动漫赛璐璐上色，线条干净",
            "日系二次元游戏角色立绘，赛璐璐上色，干净线稿，高饱和配色，纯色背景，全身，居中",
            _COMMON_NEGATIVE + "，写实照片，3D渲染",
        ),
        StyleTemplate(
            "chibi", "Q版", "大头小身 2-3 头身，可爱圆润",
            "Q版卡通游戏角色，2.5头身，大眼睛，圆润造型，描边清晰，纯色背景，全身，居中",
            _COMMON_NEGATIVE + "，写实比例，恐怖",
        ),
        StyleTemplate(
            "realistic", "写实", "写实奇幻概念设计，质感丰富",
            "写实奇幻游戏角色概念设计，电影级光影，精细材质，纯色背景，全身，居中",
            _COMMON_NEGATIVE + "，卡通，动漫，像素",
        ),
        StyleTemplate(
            "ink", "国风水墨", "中国水墨晕染，留白构图",
            "国风水墨游戏角色，水墨晕染笔触，淡彩，宣纸质感，留白背景，全身，居中",
            _COMMON_NEGATIVE + "，赛博朋克，霓虹，3D渲染",
        ),
    ]
}


def get_style(key: str) -> StyleTemplate:
    try:
        return STYLES[key]
    except KeyError as exc:
        raise ValueError(f"未知风格: {key}，可选：{', '.join(STYLES)}") from exc
