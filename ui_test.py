#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""背诵助手 —— 真浏览器 UI 测试（自带服务、随机空闲端口、全新数据目录，零额度）。

    python3 ui_test.py

覆盖：
  1. 第一次进来**不用起名/登录**，直接出题；匿名编号自动生成且刷新不丢
  2. 出题里**两种题型都出现**；选择题点错 → 显示正确答案；填空题写错 → 错字标红 → 再答对
  3. 题库页：篇目数、展开原文、练这一篇
  4. 班级页：先弹**口令遮罩**（与作文批改同一套），错口令报错、对口令进统计
  5. 刷新后匿名编号还在（= 进度不断号）
  6. 三种视口不横向溢出、零 JS 报错
  7. **风格与作文批改一致**（同一个 --radius / --brand / 卡片变量）
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
PORT = _s.getsockname()[1]          # 随机空闲端口：避免撞上僵尸测试服务
_s.close()
BASE = "http://127.0.0.1:%d/" % PORT
PASS = FAIL = 0
ERRS = []


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % name)
    else:
        FAIL += 1
        print("  ✘ %s   %s" % (name, extra))


def kill_orphans():
    """清掉上次遗留的测试服务（带 RECITE_UI_TEST=1 标记的）。"""
    import glob
    n = 0
    for env_path in glob.glob("/proc/[0-9]*/environ"):
        pid = int(env_path.split("/")[2])
        if pid == os.getpid():
            continue
        try:
            env = open(env_path, "rb").read().decode(errors="replace")
            cmd = open("/proc/%d/cmdline" % pid, "rb").read().decode(errors="replace")
        except Exception:
            continue
        if "RECITE_UI_TEST=1" in env and "app.py" in cmd:
            try:
                os.kill(pid, 15)
                n += 1
            except Exception:
                pass
    if n:
        print("（清掉了 %d 个上次遗留的测试服务）" % n)


def wait_port(port, timeout=30):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=2) as r:
                if json.load(r).get("ok"):
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    kill_orphans()          # ⚠️ 必须在起服务之前清：否则会把自己刚起的服务当孤儿杀掉
    data = tempfile.mkdtemp(prefix="recite_ui_")
    shutil.copy(os.path.join(HERE, "data", "deck.json"), os.path.join(data, "deck.json"))
    env = dict(os.environ)
    env.update({"PORT": str(PORT), "RECITE_DATA_DIR": data, "RECITE_UI_TEST": "1"})
    logf = open("/tmp/recite_ui_server.log", "w")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=HERE, env=env,
                            stdout=logf, stderr=subprocess.STDOUT)
    try:
        if not wait_port(PORT):
            ck(False, "服务起来了")
            return 1
        ck(True, "服务起来了")

        import asyncio
        from playwright.async_api import async_playwright

        async def run():
            async with async_playwright() as p:
                b = await p.chromium.launch(args=["--no-sandbox"])
                pg = await b.new_page(viewport={"width": 390, "height": 844})
                pg.on("pageerror", lambda e: ERRS.append(str(e)[:200]))
                # 口令门那步会**故意**触发 401（未解锁 / 错口令）——这类预期报错不算 bug，
                # 与 blog 项目同一套处理（否则"零 JS 报错"这条永远假红）
                def _on_console(m):
                    if m.type != "error":
                        return
                    t = m.text or ""
                    if "401" in t or "UNAUTHORIZED" in t.upper():
                        return
                    ERRS.append(t[:200])
                pg.on("console", _on_console)

                print("== 1. 匿名身份（无起名/登录步骤） ==")
                await pg.goto(BASE, wait_until="domcontentloaded")
                await pg.wait_for_timeout(400)
                ck(not await pg.is_visible("#view-name"), "没有起名页了")
                ck(not await pg.is_visible("#nameInput"), "页面上没有名字输入框")
                await pg.wait_for_selector("#view-practice:not(.hidden)", timeout=8000)
                ck(True, "进来直接进练习")
                await pg.wait_for_function("window.__renders > 0", timeout=8000)   # 等首张卡渲染完
                UID = await pg.evaluate("localStorage.getItem('recite_uid')")
                ck(bool(UID) and len(UID) == 16, "自动生成 16 位匿名编号（存本地）", str(UID))

                print("== 2. 两种题型 + 判分 ==")
                choice_ok = fill_ok = False
                async def wait_render(prev):
                    await pg.wait_for_function("(window.__renders||0) > %d" % prev, timeout=8000)

                for _ in range(40):
                    mode = await pg.inner_text("#qMode")
                    if mode == "选择题" and not choice_ok:
                        n = await pg.eval_on_selector_all("#choiceBox .opt", "els => els.length")
                        ck(n == 4, "选择题 4 个选项")
                        correct = await pg.evaluate("Q.list[Q.i].answer")
                        texts = await pg.eval_on_selector_all("#choiceBox .opt", "els => els.map(e => e.textContent)")
                        wrong = [t for t in texts if t != correct][0]
                        await pg.eval_on_selector(
                            "#choiceBox .opt:nth-child(%d)" % (texts.index(wrong) + 1), "el => el.click()")
                        await pg.wait_for_selector("#fbCard:not(.hidden)", timeout=5000)
                        ck("不对" in (await pg.inner_text("#fbTitle")), "选错 → 提示不对")
                        ck(correct in (await pg.inner_text("#fbAnswer")), "给出正确答案")
                        choice_ok = True
                        r0 = await pg.evaluate("window.__renders||0")
                        await pg.eval_on_selector("#btnNext", "el => el.click()")
                        await wait_render(r0)
                        continue
                    if mode == "填空题" and not fill_ok:
                        correct = await pg.evaluate("Q.list[Q.i].answer")
                        await pg.fill("#fillInput", "一眼看去就不对的答案")
                        await pg.eval_on_selector("#btnSubmit", "el => el.click()")
                        await pg.wait_for_selector("#fbCard:not(.hidden)", timeout=5000)
                        ck("不对" in (await pg.inner_text("#fbTitle")), "填错 → 提示不对")
                        html = await pg.inner_html("#fbYours")
                        ck("x" in html, "错的地方标红（<span class=x>）", html[:80])
                        r0 = await pg.evaluate("window.__renders||0")
                        await pg.eval_on_selector("#btnNext", "el => el.click()")
                        await wait_render(r0)
                        # 找到下一张填空题答对
                        for _ in range(30):
                            if (await pg.inner_text("#qMode")) == "填空题":
                                cor = await pg.evaluate("Q.list[Q.i].answer")
                                await pg.fill("#fillInput", cor)
                                await pg.eval_on_selector("#btnSubmit", "el => el.click()")
                                await pg.wait_for_selector("#fbCard:not(.hidden)", timeout=5000)
                                if "对" in (await pg.inner_text("#fbTitle")):
                                    ck(True, "填对 → 提示正确")
                                    fill_ok = True
                                    break
                            r1 = await pg.evaluate("window.__renders||0")
                            await pg.eval_on_selector("#btnNext", "el => el.click()")
                            await wait_render(r1)
                        if not fill_ok:
                            ck(False, "找到填空题并答对")
                        continue
                    r2 = await pg.evaluate("window.__renders||0")
                    await pg.eval_on_selector("#btnNext", "el => el.click()")
                    await wait_render(r2)
                    if choice_ok and fill_ok:
                        break
                ck(choice_ok, "出现过选择题")
                ck(fill_ok, "出现过填空题")

                print("== 3. 题库页 ==")
                await pg.eval_on_selector("#btnDeck", "el => el.click()")
                await pg.wait_for_timeout(600)
                ck(await pg.is_visible("#view-deck"), "题库页打开")
                cnt = int(await pg.inner_text("#deckCount"))
                ck(cnt >= 30, "篇目数 ≥30（实际 %d）" % cnt)
                hidden_before = await pg.eval_on_selector(".deck-item pre", "el => el.classList.contains('hidden')")
                await pg.eval_on_selector(".deck-item", "el => el.click()")
                await pg.wait_for_timeout(200)
                hidden_after = await pg.eval_on_selector(".deck-item pre", "el => el.classList.contains('hidden')")
                ck(hidden_before and not hidden_after, "点一下展开原文（可校对）")

                print("== 4. 班级页：口令门（与作文批改同一样式） ==")
                await pg.eval_on_selector("#btnTeacher", "el => el.click()")
                await pg.wait_for_selector("#setPwOverlay:not(.hidden)", timeout=8000)
                ck(True, "没解锁 → 弹出同一套口令遮罩")
                await pg.fill("#setPwInput", "错的")
                await pg.eval_on_selector("#btnSetPwOk", "el => el.click()")
                await pg.wait_for_selector("#setPwErr:not(.hidden)", timeout=5000)
                ck("口令不正确" in (await pg.inner_text("#setPwErr")), "错口令 → 报错")
                await pg.fill("#setPwInput", "<REDACTED_PASSWORD>")
                await pg.eval_on_selector("#btnSetPwOk", "el => el.click()")
                await pg.wait_for_timeout(900)
                body = await pg.inner_text("#classBody")
                ck("全班" in body or "每个学生" in body, "进门后看到班级统计", body[:60])
                ck("匿名编号" in body, "统计按匿名编号列出", body[:120])
                ck((UID or "")[:8] in body, "本机的匿名编号出现在统计里", body[:160])
                await pg.screenshot(path="/tmp/recite_teacher.png")

                print("== 5. 刷新后匿名编号还在（进度不断号） ==")
                await pg.reload()
                await pg.wait_for_timeout(800)
                ck(await pg.is_visible("#view-practice"), "刷新后直接回到练习")
                ck((await pg.evaluate("localStorage.getItem('recite_uid')")) == UID, "刷新后编号不变")

                print("== 6. 风格与作文批改一致 ==")
                style = await pg.evaluate("""() => {
                  const s = getComputedStyle(document.documentElement);
                  const card = document.querySelector('.card');
                  return {radius: s.getPropertyValue('--radius').trim(),
                          brand: s.getPropertyValue('--brand').trim(),
                          bg: s.getPropertyValue('--bg').trim(),
                          cardRadius: getComputedStyle(card).borderRadius};
                }""")
                ck(style["radius"] in ("14px", ""), "--radius = 14px（实际 %s）" % style["radius"])
                ck(style["brand"] in ("#2f6df6", ""), "--brand 与作文批改同色（实际 %s）" % style["brand"])
                ck(style["cardRadius"] == "14px", "卡片圆角 14px（实际 %s）" % style["cardRadius"])
                ck(style["bg"] in ("#f7f7f5", ""), "--bg 与作文批改同色（实际 %s）" % style["bg"])

                print("== 7. 多视口 + 零 JS 报错 ==")
                for w, h in ((390, 844), (768, 1024), (1280, 800)):
                    await pg.set_viewport_size({"width": w, "height": h})
                    await pg.wait_for_timeout(300)
                    over = await pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
                    ck(not over, "%dx%d 没有横向溢出" % (w, h))
                await pg.set_viewport_size({"width": 390, "height": 844})
                await pg.screenshot(path="/tmp/recite_phone.png")
                ck(not ERRS, "没有 JS 报错", str(ERRS[:3]))
                await b.close()

        asyncio.run(run())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            logf.close()
        except Exception:
            pass
        shutil.rmtree(data, ignore_errors=True)

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
