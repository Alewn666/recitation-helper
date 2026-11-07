"""线上验收：背诵助手（127.0.0.1:8090）——健康、页面、两种题型、口令门、截图。"""
import asyncio
import json
import sys
import urllib.request

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8090/"
PASS = FAIL = 0


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s   %s" % (name, extra))


def get(path):
    with urllib.request.urlopen(BASE.rstrip("/") + path, timeout=10) as r:
        return r.status, r.read()


async def main():
    code, body = get("/api/health")
    j = json.loads(body)
    ck(code == 200 and j.get("ok") and j.get("poems") >= 50,
       "健康检查 ok，题库 %s 篇" % j.get("poems"))
    code, page = get("/")
    ck(code == 200 and b"login-overlay" in page and b"view-name" not in page
       and b"nameInput" not in page,
       "首页 200，带口令遮罩，**已无起名页**")

    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 390, "height": 844})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
        # 线上是生产数据：验收固定用一个匿名编号，反复跑只覆盖同一条，不越攒越多
        await pg.add_init_script("localStorage.setItem('recite_uid','verify0000000000')")
        await pg.goto(BASE, wait_until="domcontentloaded")
        await pg.wait_for_timeout(600)
        ck(not await pg.is_visible("#view-name") and not await pg.is_visible("#nameInput"),
           "没有起名/登录步骤")
        await pg.wait_for_function("window.__renders > 0", timeout=8000)
        uid = await pg.evaluate("localStorage.getItem('recite_uid')")
        ck(uid == "verify0000000000", "匿名编号按预设生效（16 位）", str(uid))
        ck(True, "进入练习并渲染出第一张卡")
        modes = set()
        for _ in range(8):
            modes.add(await pg.inner_text("#qMode"))
            if len(modes) == 2:
                break
            r0 = await pg.evaluate("window.__renders||0")
            await pg.eval_on_selector("#btnNext", "el => el.click()")
            await pg.wait_for_timeout(250)
            await pg.evaluate("window.__renders = window.__renders || 0")
            if (await pg.evaluate("window.__renders||0")) == r0:
                break
        ck(modes == {"选择题", "填空题"} or len(modes) == 2, "两种题型都出现过", str(modes))
        await pg.screenshot(path="/tmp/recite_live_card.png")

        # 先答一题（把进度记上，再进老师页核对）
        await pg.eval_on_selector("#btnNext", "el => el.click()")
        await pg.wait_for_timeout(400)
        if (await pg.inner_text("#qMode")) == "选择题":
            await pg.eval_on_selector("#choiceBox .opt", "el => el.click()")
        else:
            await pg.fill("#fillInput", "随便写")
            await pg.eval_on_selector("#btnSubmit", "el => el.click()")
        await pg.wait_for_selector("#fbCard:not(.hidden)", timeout=6000)

        # 口令门
        await pg.eval_on_selector("#btnTeacher", "el => el.click()")
        await pg.wait_for_selector("#setPwOverlay:not(.hidden)", timeout=8000)
        ck(True, "班级页弹口令遮罩")
        await pg.fill("#setPwInput", "<REDACTED_PASSWORD>")
        await pg.eval_on_selector("#btnSetPwOk", "el => el.click()")
        await pg.wait_for_timeout(900)
        body = await pg.inner_text("#classBody")
        ck("匿名编号" in body and "verify00" in body,
           "解锁后统计按匿名编号列出且含本机编号", body[:80])
        await pg.screenshot(path="/tmp/recite_live_class.png")
        ck(not errs, "零 JS 报错", str(errs[:3]))
        await b.close()

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
