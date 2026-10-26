"""在线演示模式：即使配置了 Key 也绝不调用付费接口。"""
import httpx
import pytest

from app.agents import requirement_agent
from app.agents.requirement_agent import TaskItem
from app.ai import dashscope_llm, wanx_client
from app.config import Settings, get_settings


def test_demo_mode_ignores_api_key():
    s = Settings(demo_mode=True, dashscope_api_key="sk-fake-key-for-test", ai_mock=False)
    assert s.mock_ai is True
    assert s.dashscope_api_key == ""
    assert s.images_per_task_limit == 8
    assert s.total_images_limit == 32
    assert s.upload_limit_bytes == 2 * 1024 * 1024


def test_normal_mode_uses_key():
    s = Settings(demo_mode=False, dashscope_api_key="sk-fake-key-for-test", ai_mock=None)
    assert s.mock_ai is False
    assert s.total_images_limit is None


@pytest.fixture
def demo_settings(monkeypatch):
    """把全局配置换成“演示模式 + 假 Key”，并让任何外部 HTTP 请求直接失败。"""
    fake = Settings(demo_mode=True, dashscope_api_key="sk-fake-key-for-test", image_size="128*128")
    for mod in (wanx_client, dashscope_llm, requirement_agent):
        monkeypatch.setattr(mod, "get_settings", lambda: fake, raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("演示模式下不应发出任何网络请求")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    return fake


def test_demo_mode_never_calls_network(demo_settings):
    image = wanx_client.generate("像素风 战士 攻击", "", seed=42, label="亚瑟 1 v1")
    assert image.task_id.startswith("mock-")
    assert image.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_demo_mode_caps_total_images(demo_settings):
    tasks = [TaskItem(hero=f"英雄{i}", role="战士", action="攻击", count=8) for i in range(6)]
    issues: list[str] = []
    capped = requirement_agent.normalize(tasks, issues)
    assert sum(t.count for t in capped) == 32
    assert any("最多 32 张" in i for i in issues)


def test_global_settings_not_demo():
    # 其余测试跑在普通 mock 模式下，确认没有被演示模式污染
    assert get_settings().demo_mode is False


def test_example_requirement_fits_demo_limit(demo_settings):
    from pathlib import Path

    from app.parsers.document_parser import parse_document

    path = Path(__file__).parent.parent / "app" / "static" / "examples" / "demo_requirement.xlsx"
    result = requirement_agent.run(parse_document(path.name, path.read_bytes()))
    assert result.method == "table"
    assert len(result.tasks) == 6
    assert result.total_images <= demo_settings.total_images_limit


def test_pixel_sprites_parse_and_render():
    from io import BytesIO

    from PIL import Image

    from app.ai import pixel_sprites

    prompt = "16-bit 像素艺术游戏角色精灵图，角色：梅林（法师），蓝袍，动作：施法 第3/4帧，视角：侧面"
    info = pixel_sprites.parse(prompt, "梅林 1 v1")
    assert (info.hero, info.role, info.action, info.frame, info.frames, info.style) == ("梅林", "法师", "施法", 2, 4, "pixel")
    frame = Image.open(BytesIO(pixel_sprites.render(prompt, 1, "梅林 1 v1", (256, 256))))
    master = Image.open(BytesIO(pixel_sprites.render("国风水墨，梅林（法师），站立展示姿态", 1, "MASTER v1 ink", (256, 256))))
    assert frame.size == master.size == (256, 256)
    # 不同帧画面不同
    other = pixel_sprites.render(prompt.replace("第3/4帧", "第1/4帧"), 1, "梅林 1 v1", (256, 256))
    assert other != pixel_sprites.render(prompt, 1, "梅林 1 v1", (256, 256))


def test_example_images_for_demo_requirement():
    from io import BytesIO

    from PIL import Image

    from app.ai import example_images
    from app.agents.styles import STYLES

    pixel, ink = STYLES["pixel"].prefix, STYLES["ink"].prefix
    frame = f"{pixel}，角色：梅林（法师），蓝色长袍，动作：施法 第4/4帧，视角：侧面"
    v1 = example_images.lookup(frame, 1, "梅林 7 v1", (256, 256))
    v2 = example_images.lookup(frame + "，修正：光球更亮", 2, "梅林 7 v2", (256, 256))
    assert Image.open(BytesIO(v1)).size == (256, 256)
    assert v1 != v2   # 画面相同，但各版本文件内容不同
    assert example_images.lookup(f"{pixel}，亚瑟（战士），银甲，站立展示姿态", 1, "MASTER v3 pixel", (256, 256))
    # 换画风、换英雄都不命中，回落到代码绘制
    assert example_images.lookup(frame.replace(pixel, ink), 1, "梅林 7 v1", (256, 256)) is None
    assert example_images.lookup(frame.replace("梅林", "路人"), 1, "路人 7 v1", (256, 256)) is None
