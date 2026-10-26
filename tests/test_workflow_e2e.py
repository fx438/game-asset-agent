"""端到端：上传 -> 拆解 -> 确认清单 -> 母版(驳回一次) -> 批量生图 + 自动审查 -> 人工驳回回流 -> 全部通过 -> 入库。"""
import json

from app.storage import get_storage
from app.workflow.graph import pending_interrupt, run_workflow
from tests.conftest import make_xlsx

DECIDE_LATER = {"WAITING_HUMAN", "AUTO_FAILED", "ERROR"}


def _project(client, pid):
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def _assets(client, pid):
    return client.get(f"/api/projects/{pid}/assets").json()


def test_full_workflow(client):
    xlsx = make_xlsx([["英雄", "角色", "动作", "数量"], ["亚瑟", "战士", "攻击", 3], ["妲己", "法师", "待机", 2]])
    r = client.post("/api/projects", data={"name": "第一期 角色"}, files={"file": ("清单.xlsx", xlsx)})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # 1. 需求拆解完成，等待确认清单
    p = _project(client, pid)
    assert p["status"] == "PLAN_CONFIRMING"
    assert p["total_images"] == 5
    assert pending_interrupt(pid) == "confirm_plan"

    # 状态不对的操作被拒绝
    assert client.post(f"/api/projects/{pid}/master", json={"approved": True}).status_code == 409

    # 2. 确认清单（顺手把亚瑟攻击改成 4 张）并选择像素风
    tasks = [{k: t[k] for k in ("hero", "role", "action", "count", "description", "view")} for t in p["tasks"]]
    tasks[0]["count"] = 4
    r = client.post(f"/api/projects/{pid}/plan", json={"style_key": "pixel", "style_notes": "冷色调", "tasks": tasks})
    assert r.status_code == 202, r.text
    p = _project(client, pid)
    assert p["status"] == "MASTER_CONFIRMING"
    assert set(p["character_cards"]) == {"亚瑟|战士", "妲己|法师"}

    # 3. 驳回母版必须带意见；带意见驳回后生成第 2 版
    assert client.post(f"/api/projects/{pid}/master", json={"approved": False}).status_code == 400
    client.post(f"/api/projects/{pid}/master", json={"approved": False, "feedback": "颜色再暗一些"})
    masters = client.get(f"/api/projects/{pid}/masters").json()
    assert [m["version"] for m in masters] == [2, 1]
    assert masters[1]["status"] == "REJECTED" and "颜色再暗一些" in masters[0]["prompt"]
    assert client.get(masters[0]["image_url"]).headers["content-type"] == "image/png"

    # 过期 / 重复的唤醒（类型不匹配）会被忽略，流程仍停在母版确认
    run_workflow(pid, {"type": "confirm_plan", "style_key": "anime"})
    # 针对旧版母版 v1 的确认（token 过期）也会被忽略
    run_workflow(pid, {"type": "confirm_master", "token": 1, "approved": True})
    assert _project(client, pid)["status"] == "MASTER_CONFIRMING"
    assert pending_interrupt(pid) == "confirm_master"

    # 4. 确认母版 -> 批量生图 + 自动审查 -> 生成屏障唤醒 -> 等待人工审核
    client.post(f"/api/projects/{pid}/master", json={"approved": True})
    p = _project(client, pid)
    assert p["status"] == "HUMAN_REVIEWING", p
    assert pending_interrupt(pid) == "human_review"
    assets = _assets(client, pid)
    assert len(assets) == 6
    assert all(a["status"] in DECIDE_LATER for a in assets)
    assert all(a["prompt"].startswith("16-bit 像素艺术") for a in assets)
    detail = client.get(f"/api/assets/{assets[0]['id']}").json()
    assert detail["reviews"] and detail["reviews"][0]["kind"] == "auto"

    # 未审完不能提交
    assert client.post(f"/api/projects/{pid}/review/submit").status_code == 409

    # 5. 驳回第一张（带批注），其余通过
    target = assets[0]
    assert client.post(f"/api/assets/{target['id']}/review", json={"approved": False}).status_code == 400
    client.post(f"/api/assets/{target['id']}/review", json={"approved": False, "comment": "剑太短", "reviewer": "qa"})
    others = [a["id"] for a in assets[1:]]
    ok = [a["id"] for a in assets[1:] if a["status"] != "ERROR"]
    if ok:
        assert client.post(f"/api/projects/{pid}/review/batch", json={"asset_ids": ok, "approved": True}).status_code == 200
    for aid in set(others) - set(ok):
        client.post(f"/api/assets/{aid}/review", json={"approved": False, "comment": "重新生成"})
    client.post(f"/api/projects/{pid}/review/submit")

    p = _project(client, pid)
    assert p["status"] == "HUMAN_REVIEWING" and p["review_round"] == 1
    regenerated = client.get(f"/api/assets/{target['id']}").json()
    assert regenerated["version"] > target["version"]
    assert "剑太短" in regenerated["prompt"]
    assert regenerated["status"] in DECIDE_LATER

    # 上一轮（round=0）的重复提交晚到：必须被丢弃，不能把本轮未审核的图直接归档
    run_workflow(pid, {"type": "human_review", "token": 0})
    assert _project(client, pid)["status"] == "HUMAN_REVIEWING"
    assert client.get(f"/api/assets/{target['id']}").json()["status"] in DECIDE_LATER

    # 6. 第二轮全部通过 -> 归档入库
    pending = [a["id"] for a in _assets(client, pid) if a["status"] in DECIDE_LATER]
    for aid in pending:
        a = client.get(f"/api/assets/{aid}").json()
        client.post(f"/api/assets/{aid}/review", json={"approved": a["status"] != "ERROR", "comment": None if a["status"] != "ERROR" else "重试"})
    client.post(f"/api/projects/{pid}/review/submit")
    p = _project(client, pid)
    if p["status"] == "HUMAN_REVIEWING":   # 极少数情况下有生成失败的图被驳回，再审一轮
        for a in _assets(client, pid):
            if a["status"] in DECIDE_LATER:
                client.post(f"/api/assets/{a['id']}/review", json={"approved": True})
        client.post(f"/api/projects/{pid}/review/submit")
        p = _project(client, pid)
    assert p["status"] == "ARCHIVED", p

    library = client.get(f"/api/projects/{pid}/library").json()
    assert len(library) == 6
    keys = sorted(a["archive_key"] for a in library)
    assert keys[0] == f"library/{pid}_第一期_角色/亚瑟/战士/攻击/亚瑟_战士_攻击_01.png"
    manifest = json.loads(get_storage().get_bytes(f"library/{pid}_第一期_角色/manifest.json"))
    assert len(manifest) == 6 and all(m["style"] == "pixel" and m["master_version"] == 2 for m in manifest)


def test_rejects_bad_upload(client):
    assert client.post("/api/projects", data={"name": "x"}, files={"file": ("a.exe", b"1")}).status_code == 400
    assert client.post("/api/projects", data={"name": "x"}, files={"file": ("a.txt", b"")}).status_code == 400


def test_unrecognizable_document_marks_failed(client):
    r = client.post("/api/projects", data={"name": "坏文档"}, files={"file": ("a.txt", "随便写点什么".encode())})
    p = _project(client, r.json()["id"])
    assert p["status"] == "FAILED"
    assert "没有从文档中识别出任何出图任务" in p["error"]


def test_file_endpoint_blocks_path_traversal(client):
    assert client.get("/api/files/../.env").status_code == 404
    assert client.get("/api/files/other/x.png").status_code == 404
