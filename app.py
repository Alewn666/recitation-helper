#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""背诵助手 —— 中考古诗词背诵练习（整个班级共用，无账号）。

设计与约定（与「作文批改」保持一致的风格与工程约定）：
  · 题库 = data/deck.json，可逐字校对；页面里能看原文
  · 出题引擎从原文自动生成卡片：上句填下句 / 下句填上句 / 整句默写 / 作者朝代 / 字词释义 / 理解性默写
  · **每张卡由系统随机决定「选择题」还是「填空题」**（字词释义固定用选择题）
  · 判分：忽略空白与标点；答错时指出第几个字不对
  · 进度按**匿名编号**记（前端首次进来自动生成 16 位随机 id 存 localStorage，**无起名/登录步骤**；
    老师统计页按编号列人，`students` 的键就是这个编号）；记忆节奏用简化间隔重复
  · 老师统计页 = /api/stats，走**设置口令门**（默认 <REDACTED_PASSWORD> / 环境变量覆盖 / 空串关闭），
    未解锁返回 401 {"error":"settings_auth"} —— 与作文批改同一套语义
"""
import hmac
import json
import os
import random
import threading
import time

from flask import Flask, Response, jsonify, render_template, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("RECITE_DATA_DIR", os.path.join(BASE_DIR, "data"))
DECK_PATH = os.path.join(DATA_DIR, "deck.json")
PROG_PATH = os.path.join(DATA_DIR, "progress.json")

# —— 设置口令门：默认写死在代码里（部署到哪台都一样），环境变量可覆盖，空串=关闭
SETTINGS_PW_DEFAULT = "<REDACTED_PASSWORD>"
SETTINGS_GATED = {"/api/stats"}
_PW_FAILS, _PW_LOCK = {}, threading.Lock()
PW_MAX_FAILS, PW_LOCK_SECS = 5, 30

app = Flask(__name__)
app.secret_key = os.environ.get("RECITE_SECRET") or os.urandom(32)

_lock = threading.Lock()
_deck_cache = {"mtime": 0, "data": None}

PUNCT = "，。？！；：、（）【】《》〈〉「」『』“”‘’,.!?;:()[]{}<> \t\r\n\u3000"
STEPS = [0, 1 * 86400, 3 * 86400, 7 * 86400, 14 * 86400]   # 答对后的复习间隔
REQUEUE_SECS = 60                                          # 答错：1 分钟后重新出现


# ------------------------------------------------------------------ 题库
def load_deck():
    mt = os.path.getmtime(DECK_PATH)
    with _lock:
        if _deck_cache["data"] is None or _deck_cache["mtime"] != mt:
            with open(DECK_PATH, encoding="utf-8") as f:
                _deck_cache["data"] = json.load(f)
            _deck_cache["mtime"] = mt
        return _deck_cache["data"]


def split_half(line):
    """整句拆上下两半：只在「刚好一个逗号、且没有问号感叹号分号冒号」时拆。"""
    if line.count("，") != 1:
        return None
    if any(c in line for c in "？！；："):
        return None
    a, b = line.split("，")
    if not a.strip() or not b.strip():
        return None
    return a.strip(), b.strip()


def build_cards(poem):
    """把一首诗拆成卡片列表（纯函数，便于测试）。"""
    pid, title = poem["id"], poem["title"]
    meta = {"poem_id": pid, "poem_title": title,
            "author": poem.get("author", ""), "dynasty": poem.get("dynasty", "")}
    cards, lines = [], poem.get("text", [])
    for i, line in enumerate(lines):
        half = split_half(line)
        if half:
            a, b = half
            b_full = b if b.endswith(("。", "？", "！")) else b + "。"   # 别补重复的句号
            cards.append(dict(meta, id="%s:n:%d" % (pid, i), kind="next", idx=i,
                              prompt="%s，______" % a, answer=b_full,
                              hint="《%s》· 接下一句" % title))
            cards.append(dict(meta, id="%s:p:%d" % (pid, i), kind="prev", idx=i,
                              prompt="______，%s" % b_full, answer=a,
                              hint="《%s》· 上一句" % title))
        else:
            prevline = lines[i - 1] if i > 0 else ""
            cards.append(dict(meta, id="%s:w:%d" % (pid, i), kind="whole", idx=i,
                              prompt=("《%s》中紧接「%s」的是？" % (title, prevline))
                              if prevline else ("《%s》的开篇句是？" % title),
                              answer=line, hint="《%s》· 第 %d 句" % (title, i + 1)))
    # 名句卡：把 famous 里的名句也做成默写卡（能拆上下句的才做）
    for j, f in enumerate(poem.get("famous", [])):
        half = split_half(f if f.endswith(("。", "！", "？")) else f + "。")
        if not half:
            continue
        fa, fb = half
        fb_full = fb if fb.endswith(("。", "？", "！")) else fb + "。"
        cards.append(dict(meta, id="%s:f:%d" % (pid, j), kind="next", idx=j,
                          prompt="《%s》名句：%s，______" % (title, fa), answer=fb_full,
                          hint="名句默写"))
    cards.append(dict(meta, id="%s:a:0" % pid, kind="author", idx=0,
                      prompt="《%s》的作者和朝代是？" % title,
                      answer="%s·%s" % (poem.get("author", "?"), poem.get("dynasty", "?")),
                      hint="作者与朝代"))
    for j, u in enumerate(poem.get("understood", [])):
        cards.append(dict(meta, id="%s:u:%d" % (pid, j), kind="understood", idx=j,
                          prompt=u["q"], answer=u["a"], hint="理解性默写"))
    for j, w in enumerate(poem.get("words", [])):
        cards.append(dict(meta, id="%s:d:%d" % (pid, j), kind="word", idx=j,
                          prompt="「%s」是什么意思？" % w["w"], answer=w["a"],
                          hint="字词解释"))
    return cards


def all_cards(poem_id=None):
    out = []
    for p in load_deck()["poems"]:
        if poem_id and p["id"] != poem_id:
            continue
        out.extend(build_cards(p))
    return out


def pick_options(card, pool, n=4):
    """从同类型卡片里抽干扰项（保证 4 个不重复、必含正确答案）。"""
    ans = card["answer"]
    cands = [c["answer"] for c in pool
             if c["kind"] == card["kind"] and c["id"] != card["id"] and c["answer"] != ans]
    uniq = []
    for c in cands:
        if c not in uniq:
            uniq.append(c)
    random.shuffle(uniq)
    opts = uniq[:n - 1]
    if len(opts) < n - 1:
        others = [c["answer"] for c in pool
                  if c["id"] != card["id"] and c["answer"] != ans and c["answer"] not in opts]
        random.shuffle(others)
        opts += others[:n - 1 - len(opts)]
    opts.append(ans)
    random.shuffle(opts)
    return opts


# ------------------------------------------------------------------ 判分
def norm(s):
    s = s or ""
    out = []
    for ch in s:
        if ch in PUNCT:
            continue
        if "０" <= ch <= "９":
            ch = chr(ord(ch) - 0xFEE0)
        elif "Ａ" <= ch <= "Ｚ" or "ａ" <= ch <= "ｚ":
            ch = chr(ord(ch) - 0xFEE0)
        out.append(ch)
    return "".join(out)


def grade(user, answer):
    """返回 (对错, 说明)。只差标点算对（但提醒一声）。"""
    nu, na = norm(user), norm(answer)
    if not nu:
        return False, "还没填呢"
    if nu == na:
        if (user or "").strip() != (answer or "").strip():
            return True, "对！标点也照原文写就更好"
        return True, "完全正确"
    i = 0
    while i < min(len(nu), len(na)) and nu[i] == na[i]:
        i += 1
    if i < min(len(nu), len(na)):
        return False, "第 %d 个字不对：「%s」应为「%s」" % (i + 1, nu[i], na[i])
    if len(nu) < len(na):
        return False, "少写了：" + na[len(nu):][:6]
    return False, "多写了：" + nu[len(na):][:6]


# ------------------------------------------------------------------ 进度
def load_prog():
    try:
        with open(PROG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"students": {}, "events": []}


def save_prog(p):
    """原子写：临时文件 + fsync + os.replace（写到一半被打断也不会留半截 JSON）。"""
    tmp = PROG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PROG_PATH)


def student_of(prog, name):
    return prog["students"].setdefault(name, {"cards": {}, "updated": 0})


# ------------------------------------------------------------------ 设置口令门
def settings_pw():
    if "RECITE_SETTINGS_PW" in os.environ:
        return os.environ["RECITE_SETTINGS_PW"].strip()
    return SETTINGS_PW_DEFAULT


def _rate_ok(ip):
    with _PW_LOCK:
        rec = _PW_FAILS.get(ip)
        return not (rec and time.time() < rec.get("until", 0))


def _rate_fail(ip):
    with _PW_LOCK:
        rec = _PW_FAILS.setdefault(ip, {"n": 0, "until": 0})
        rec["n"] += 1
        if rec["n"] >= PW_MAX_FAILS:
            rec["until"] = time.time() + PW_LOCK_SECS
            rec["n"] = 0


@app.before_request
def _gate():
    if request.path in SETTINGS_GATED and settings_pw() and not session.get("recite_teacher"):
        return Response(json.dumps({"error": "settings_auth"}, ensure_ascii=False),
                        status=401, mimetype="application/json")
    return None


@app.route("/api/settings/login", methods=["POST"])
def api_settings_login():
    ip = request.remote_addr or "?"
    if not _rate_ok(ip):
        return jsonify({"ok": False, "error": "尝试次数过多，请 %d 秒后再试" % PW_LOCK_SECS}), 429
    pw = settings_pw()
    if not pw:
        session["recite_teacher"] = True
        return jsonify({"ok": True})
    data = request.get_json(force=True, silent=True) or {}
    if hmac.compare_digest((data.get("password") or "").encode("utf-8"), pw.encode("utf-8")):
        with _PW_LOCK:
            _PW_FAILS.pop(ip, None)
        session["recite_teacher"] = True
        session.permanent = True
        return jsonify({"ok": True})
    _rate_fail(ip)
    return jsonify({"ok": False, "error": "口令不正确"}), 401


# ------------------------------------------------------------------ 页面与接口
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "poems": len(load_deck()["poems"])})


@app.route("/api/deck")
def api_deck():
    poems = []
    for p in load_deck()["poems"]:
        poems.append({"id": p["id"], "title": p["title"], "author": p.get("author", ""),
                      "dynasty": p.get("dynasty", ""), "kind": p.get("kind", "诗"),
                      "lines": len(p.get("text", [])), "cards": len(build_cards(p)),
                      "text": p.get("text", [])})
    return jsonify({"poems": poems, "total_poems": len(poems),
                    "total_cards": sum(x["cards"] for x in poems)})


@app.route("/api/cards")
def api_cards():
    name = (request.args.get("name") or "").strip()[:20]
    poem_id = request.args.get("poem") or None
    limit = min(int(request.args.get("limit") or 20), 60)
    only_wrong = request.args.get("only_wrong") == "1"
    if not name:
        return jsonify({"error": "need_name"}), 400
    prog = load_prog()
    fresh_student = name not in prog["students"]
    st = student_of(prog, name)["cards"]
    now = time.time()
    if fresh_student:                     # 第一次进来练就登记（老师页能看到"今天谁练了"）
        student_of(prog, name)["updated"] = now
        with _lock:
            save_prog(prog)
    cards = all_cards(poem_id)
    due = [c for c in cards if (st.get(c["id"], {}).get("seen") or 0) > 0
           and (st.get(c["id"], {}).get("due") or 0) <= now]
    fresh = [c for c in cards if (st.get(c["id"], {}).get("seen") or 0) == 0]
    rest = [c for c in cards if c not in due and c not in fresh]
    random.shuffle(fresh)
    if only_wrong:
        pool = [c for c in due + fresh + rest if (st.get(c["id"], {}).get("wrong") or 0) > 0]
        due, fresh, rest = pool, [], []
    batch = (due + fresh + rest)[:limit]
    if not batch:
        return jsonify({"cards": [], "done": True, "name": name})
    pool = cards
    out, modes = [], []
    for c in batch:
        # 系统随机决定题型：字词释义固定选择题（背释义不用手打长句），其余五五开
        mode = "choice" if c["kind"] == "word" else random.choice(["choice", "fill"])
        modes.append(mode)
        item = {"id": c["id"], "kind": c["kind"], "mode": mode, "prompt": c["prompt"],
                "hint": c["hint"], "poem_id": c["poem_id"], "poem_title": c["poem_title"],
                "author": c["author"], "dynasty": c["dynasty"], "answer": c["answer"]}
        if mode == "choice":
            item["options"] = pick_options(c, pool)
        out.append(item)
    if len(out) >= 4 and len(set(modes)) == 1:        # 一批里两种题型至少各出现一次
        flip = random.randrange(len(out))
        cur = out[flip]["mode"]
        out[flip]["mode"] = "fill" if cur == "choice" else "choice"
        c = next(x for x in batch if x["id"] == out[flip]["id"])
        if out[flip]["mode"] == "choice":
            out[flip]["options"] = pick_options(c, pool)
        else:
            out[flip].pop("options", None)
    return jsonify({"cards": out, "name": name, "done": False,
                    "stats": {"due": len(due), "fresh": len(fresh), "seen": len(st)}})


@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()[:20]
    cid = data.get("id") or ""
    if not name or not cid:
        return jsonify({"error": "bad_request"}), 400
    card = next((c for c in all_cards() if c["id"] == cid), None)
    if not card:
        return jsonify({"error": "unknown_card"}), 404
    mode = data.get("mode") or "fill"
    yours = (data.get("answer") or "").strip()
    if mode == "choice":
        ok = norm(yours) == norm(card["answer"])
        note = "选对了" if ok else "选错了，看正确答案"
    else:
        ok, note = grade(yours, card["answer"])
    now = time.time()
    prog = load_prog()
    st = student_of(prog, name)["cards"].setdefault(
        cid, {"seen": 0, "right": 0, "wrong": 0, "streak": 0, "due": 0})
    st["seen"] += 1
    if ok:
        st["right"] += 1
        st["streak"] = min(st.get("streak", 0) + 1, len(STEPS) - 1)
        st["due"] = now + STEPS[st["streak"]]
    else:
        st["wrong"] += 1
        st["streak"] = 0
        st["due"] = now + REQUEUE_SECS
    student_of(prog, name)["updated"] = now
    prog.setdefault("events", []).append(
        {"ts": now, "name": name, "id": cid, "ok": bool(ok), "mode": mode})
    if len(prog["events"]) > 20000:
        prog["events"] = prog["events"][-10000:]
    with _lock:
        save_prog(prog)
    return jsonify({"ok": bool(ok), "note": note, "answer": card["answer"],
                    "poem_title": card["poem_title"], "author": card["author"],
                    "dynasty": card["dynasty"], "streak": st["streak"],
                    "next_in": int(max(0, st["due"] - now))})


@app.route("/api/progress")
def api_progress():
    name = (request.args.get("name") or "").strip()[:20]
    if not name:
        return jsonify({"error": "need_name"}), 400
    prog = load_prog()
    st = (prog["students"].get(name) or {}).get("cards", {})
    cards = all_cards()
    by_poem = {}
    for c in cards:
        s = st.get(c["id"]) or {}
        if not s.get("seen"):
            continue
        b = by_poem.setdefault(c["poem_id"], {"poem_title": c["poem_title"], "seen": 0,
                                              "right": 0, "wrong": 0, "cards": 0})
        b["seen"] += s.get("seen", 0)
        b["right"] += s.get("right", 0)
        b["wrong"] += s.get("wrong", 0)
        b["cards"] += 1
    touched = len([1 for c in cards if (st.get(c["id"]) or {}).get("seen")])
    return jsonify({"name": name, "cards_total": len(cards), "cards_touched": touched,
                    "right": sum(s.get("right", 0) for s in st.values()),
                    "wrong": sum(s.get("wrong", 0) for s in st.values()),
                    "by_poem": sorted(by_poem.values(), key=lambda x: -x["seen"])[:20]})


@app.route("/api/wrong")
def api_wrong():
    name = (request.args.get("name") or "").strip()[:20]
    prog = load_prog()
    st = (prog["students"].get(name) or {}).get("cards", {})
    out = []
    for c in all_cards():
        s = st.get(c["id"]) or {}
        if s.get("wrong"):
            out.append({"id": c["id"], "prompt": c["prompt"], "answer": c["answer"],
                        "poem_title": c["poem_title"], "wrong": s["wrong"],
                        "right": s.get("right", 0), "streak": s.get("streak", 0)})
    out.sort(key=lambda x: (-x["wrong"], x["answer"]))
    return jsonify({"wrong": out[:120]})


@app.route("/api/stats")
def api_stats():
    """老师页（只读）—— 走设置口令门。"""
    prog = load_prog()
    now = time.time()
    students = []
    for nm, s in prog["students"].items():
        cs = s.get("cards", {})
        right = sum(c.get("right", 0) for c in cs.values())
        wrong = sum(c.get("wrong", 0) for c in cs.values())
        today = len([e for e in prog.get("events", [])
                     if e["name"] == nm and now - e["ts"] < 86400])
        students.append({"name": nm, "cards_touched": len(cs), "right": right,
                         "wrong": wrong, "today": today, "last": int(s.get("updated", 0)),
                         "accuracy": round(right * 100.0 / max(right + wrong, 1))})
    students.sort(key=lambda x: (-x["today"], -x["last"]))
    wrong_counter = {}
    for s in prog["students"].values():
        for cid, c in s.get("cards", {}).items():
            if c.get("wrong"):
                wrong_counter[cid] = wrong_counter.get(cid, 0) + c["wrong"]
    cards = {c["id"]: c for c in all_cards()}
    top_wrong = []
    for cid, n in sorted(wrong_counter.items(), key=lambda x: -x[1])[:15]:
        c = cards.get(cid) or {}
        top_wrong.append({"prompt": c.get("prompt", cid), "answer": c.get("answer", ""),
                          "poem": c.get("poem_title", ""), "wrong": n})
    return jsonify({"students": students, "top_wrong": top_wrong,
                    "class": {"students": len(students), "cards": len(cards),
                              "today_answers": len([e for e in prog.get("events", [])
                                                    if now - e["ts"] < 86400])}})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), debug=False)
