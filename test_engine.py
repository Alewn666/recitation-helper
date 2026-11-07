#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""背诵助手 —— 后端自测（题库 / 出题 / 判分 / 进度 / 老师口令门）。零依赖、零额度。

    python3 test_engine.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="recite_test_")
shutil.copy(os.path.join(HERE, "data", "deck.json"), os.path.join(TMP, "deck.json"))
os.environ["RECITE_DATA_DIR"] = TMP
os.environ.pop("RECITE_SETTINGS_PW", None)      # 默认口令（<REDACTED_PASSWORD>）

sys.path.insert(0, HERE)
import app as srv                                 # noqa: E402

PASS = FAIL = 0


def ok(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s   %s" % (name, extra))


def main():
    c = srv.app.test_client()

    print("== 1. 题库 ==")
    poems = srv.load_deck()["poems"]
    ok(len(poems) >= 30, "篇目数 ≥30（实际 %d）" % len(poems))
    ok(len({p["id"] for p in poems}) == len(poems), "篇目 id 不重复")
    ok(all(p.get("text") and p.get("author") and p.get("dynasty") for p in poems),
       "每篇都有原文/作者/朝代")

    print("== 2. 拆句规则 ==")
    ok(srv.split_half("大漠孤烟直，长河落日圆。") == ("大漠孤烟直", "长河落日圆。"), "一个逗号 → 拆")
    ok(srv.split_half("枯藤老树昏鸦，小桥流水人家，古道西风瘦马。") is None, "多逗号 → 不拆")
    ok(srv.split_half("明月几时有？把酒问青天。") is None, "含问号 → 不拆")

    print("== 3. 卡片生成 ==")
    poem = next(p for p in poems if p["id"] == "shizhisaishang")
    cards = srv.build_cards(poem)
    kinds = {}
    for x in cards:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    ok(len({x["id"] for x in cards}) == len(cards), "卡片 id 不重复")
    ok(bool(kinds.get("next")) and bool(kinds.get("prev")), "有上句填下句 / 下句填上句", str(kinds))
    ok(kinds.get("author") == 1, "有作者朝代卡")
    ok(kinds.get("understood", 0) >= 1 and kinds.get("word", 0) >= 1, "有理解性默写 / 字词卡")
    card = next(x for x in cards if x["kind"] == "next" and x["idx"] == 2)
    ok(card["answer"] == "长河落日圆。" and "大漠孤烟直" in card["prompt"],
       "「大漠孤烟直，______」→ 长河落日圆。（句号不重复）", card["answer"])
    ok(card["poem_title"] == "使至塞上" and card["author"] == "王维", "卡片带出处/作者")

    print("== 4. 选择题干扰项 ==")
    pool = srv.all_cards()
    bad = 0
    for i in range(30):
        c0 = pool[i % len(pool)]
        o = srv.pick_options(c0, pool)
        if len(o) != 4 or len(set(o)) != 4 or c0["answer"] not in o:
            bad += 1
    ok(bad == 0, "30 次抽样：4 个选项、不重复、必含正确答案")

    print("== 5. 判分 ==")
    ok(srv.grade("长河落日圆。", "长河落日圆。")[0] is True, "完全一致 → 对")
    ok(srv.grade("长河落日圆", "长河落日圆。")[0] is True, "只差标点 → 算对")
    ok("标点" in srv.grade("长河落日圆", "长河落日圆。")[1], "并提醒标点")
    ok(srv.grade("长河落日元。", "长河落日圆。")[0] is False, "错字 → 错")
    ok("第 5 个字" in srv.grade("长河落日元。", "长河落日圆。")[1], "指出第几个字错")
    ok(srv.grade("长河落日", "长河落日圆。")[0] is False, "少写 → 错")
    ok(srv.grade("", "长河落日圆。")[0] is False, "空答案 → 错")

    print("== 6. 出题接口（系统随机两种题型） ==")
    r = c.get("/api/cards?name=小明&limit=20")
    j = r.get_json()
    ok(r.status_code == 200 and len(j["cards"]) == 20, "取到 20 张卡")
    ok({x["mode"] for x in j["cards"]} == {"choice", "fill"}, "一批里两种题型都有",
       str({x["mode"] for x in j["cards"]}))
    for x in j["cards"]:
        if x["mode"] == "choice":
            ok(len(x["options"]) == 4 and x["answer"] in x["options"], "选择题 4 选项含答案")
            break
    ok(all(x["answer"] for x in j["cards"]), "都带答案（前端展示用）")
    ok(c.get("/api/cards").status_code == 400, "没名字 → 400")
    ok(len(c.get("/api/cards?name=小明&limit=5").get_json()["cards"]) == 5, "limit 生效")

    print("== 7. 答题与进度（按名字，无账号） ==")
    first = j["cards"][0]
    r = c.post("/api/answer", json={"name": "小明", "id": first["id"], "mode": "fill",
                                    "answer": first["answer"]})
    ans = r.get_json()
    ok(ans["ok"] is True and ans["streak"] == 1 and ans["next_in"] >= 86400, "答对 → 推到 1 天后")
    r = c.post("/api/answer", json={"name": "小明", "id": first["id"], "mode": "fill", "answer": "乱写"})
    ans = r.get_json()
    ok(ans["ok"] is False and ans["streak"] == 0 and ans["next_in"] <= 61, "答错 → 1 分钟后再出现")
    ok(c.post("/api/answer", json={"name": "小明", "id": "不存在:1:1"}).status_code == 404, "未知卡 → 404")
    ok(c.post("/api/answer", json={"id": first["id"]}).status_code == 400, "缺名字 → 400")
    saved = json.load(open(srv.PROG_PATH, encoding="utf-8"))
    ok("小明" in saved["students"], "进度落盘（按名字）")
    ok(not os.path.exists(srv.PROG_PATH + ".tmp"), "原子写不留临时文件")
    ok(len(saved["events"]) >= 2, "答题事件记下来了")

    print("== 8. 只练错过的 / 个人进度 / 错题本 ==")
    got = c.get("/api/cards?name=小明&only_wrong=1&limit=20").get_json()["cards"]
    ok(got and all(x["id"] == first["id"] for x in got), "只返回错过的卡")
    ok(c.get("/api/cards?name=小红&only_wrong=1").get_json()["cards"] == [], "没答错过 → 空")
    pj = c.get("/api/progress?name=小明").get_json()
    ok(pj["cards_touched"] >= 1 and pj["by_poem"], "个人进度有数据")
    ok(c.get("/api/progress").status_code == 400, "缺名字 → 400")
    ok(len(c.get("/api/wrong?name=小明").get_json()["wrong"]) >= 1, "错题本有内容")

    print("== 9. 老师页口令门（与作文批改同语义） ==")
    r = c.get("/api/stats")
    ok(r.status_code == 401 and r.get_json().get("error") == "settings_auth",
       "未解锁 → 401 settings_auth", "%d %s" % (r.status_code, r.get_json()))
    ok(c.post("/api/settings/login", json={"password": "wrong"}).status_code == 401, "错口令 → 401")
    ok(c.post("/api/settings/login", json={"password": srv.SETTINGS_PW_DEFAULT}).status_code == 200,
       "对口令 → 200")
    j = c.get("/api/stats").get_json()
    ok(j["class"]["students"] >= 1 and any(s["name"] == "小明" for s in j["students"]),
       "统计里有小明")
    ok(j["top_wrong"] and j["top_wrong"][0]["wrong"] >= 1, "最容易错的句子有数据")
    ok(srv.app.test_client().get("/api/stats").status_code == 401, "新会话仍要口令")

    print("== 10. 环境变量覆盖 / 关闭口令门 ==")
    os.environ["RECITE_SETTINGS_PW"] = "other_pw"
    c2 = srv.app.test_client()
    ok(c2.post("/api/settings/login", json={"password": srv.SETTINGS_PW_DEFAULT}).status_code == 401,
       "老口令失效")
    ok(c2.post("/api/settings/login", json={"password": "other_pw"}).status_code == 200, "新口令生效")
    os.environ["RECITE_SETTINGS_PW"] = ""
    ok(srv.app.test_client().get("/api/stats").status_code == 200, "空串 → 门关着")
    del os.environ["RECITE_SETTINGS_PW"]
    ok(srv.app.test_client().get("/api/stats").status_code == 401, "删掉变量 → 门回来")

    print("== 11. 其它 ==")
    dj = c.get("/api/deck").get_json()
    ok(dj["total_poems"] >= 30 and dj["poems"][0]["text"], "题库接口带原文（供校对）")
    ok(c.get("/api/health").get_json()["ok"] is True, "健康检查")
    ok(b"login-overlay" in c.get("/").data, "页面用的是与作文批改同一套口令遮罩样式")

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
