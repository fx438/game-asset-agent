"""构建 GitHub Pages 静态演示站。

站点结构：
  index.html                       app/static/index.html，注入浏览器内模拟后端
  static/mock-api.js               deploy/pages/mock-api.js
  static/examples/frames/*.png     示例图
  static/examples/demo_requirement.xlsx

只用标准库，CI 里不需要安装依赖。用法：python deploy/pages/build.py [输出目录，默认 _site]
本地预览：python -m http.server -d _site 8080
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MARK = "<!-- STATIC_DEMO:"


def build(out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    examples = out / "static" / "examples"
    examples.mkdir(parents=True)

    lines = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.lstrip().startswith(MARK)]
    if len(hits) != 1:
        raise SystemExit(f"index.html 里应该恰好有一处 {MARK} 标记，实际 {len(hits)} 处")
    lines[hits[0]] = '<script src="static/mock-api.js"></script>\n'
    (out / "index.html").write_text("".join(lines), encoding="utf-8")

    shutil.copy(HERE / "mock-api.js", out / "static" / "mock-api.js")
    shutil.copytree(ROOT / "app" / "static" / "examples" / "frames", examples / "frames")
    shutil.copy(ROOT / "app" / "static" / "examples" / "demo_requirement.xlsx", examples)
    (out / ".nojekyll").touch()
    print(f"已生成 {out}（{sum(1 for p in out.rglob('*') if p.is_file())} 个文件）")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site")
