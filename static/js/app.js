/* 背诵助手 —— 前端逻辑（风格与「作文批改」一致：零依赖、零 CDN、相对路径请求）
   相对路径（api/… static/…）→ 将来部署到 /beishu/ 这类子路径也能用 */
/* 匿名身份：首次进来自动生成一个 16 位随机 id，存 localStorage，永不过期、无需起名/登录。
   ⚠️ 不用 crypto.randomUUID() —— 它只在安全上下文（https/localhost）可用，
   而 NAS 上是 http://192.168.1.100:8090，会是 undefined；getValues/getRandomValues 才到处能用。 */
function newUid() {
  var b = new Uint8Array(8);
  crypto.getRandomValues(b);
  var h = '';
  for (var i = 0; i < b.length; i++) h += ('0' + b[i].toString(16)).slice(-2);
  return h;
}
function getUid() {
  var id = localStorage.getItem('recite_uid') || '';
  if (!id) { id = newUid(); localStorage.setItem('recite_uid', id); }
  return id;
}
var Q = { list: [], i: 0, right: 0, wrong: 0, wrongs: [], answered: false, timer: null, poem: '' };
var PWCB = null;         // 口令解锁后要重试的动作

function $(s) { return document.querySelector(s); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function api(path, opts) {
  return fetch(path, opts).then(function (r) {
    return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
  });
}
function toast(msg, ms) {
  var t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._h);
  t._h = setTimeout(function () { t.classList.add('hidden'); }, ms || 1800);
}
function show(view) {
  ['practice', 'deck', 'teacher'].forEach(function (v) {
    var el = $('#view-' + v);
    if (el) el.classList.toggle('hidden', v !== view);
  });
  window.scrollTo(0, 0);
}

/* ---------------- 口令门（与作文批改同语义） ---------------- */
function openPw(cb) {
  PWCB = cb || null;
  $('#setPwOverlay').classList.remove('hidden');
  $('#setPwErr').classList.add('hidden');
  $('#setPwInput').value = '';
  setTimeout(function () { $('#setPwInput').focus(); }, 60);
}
function closePw(back) {
  $('#setPwOverlay').classList.add('hidden');
  PWCB = null;
}
function tryPw() {
  var btn = $('#btnSetPwOk');
  btn.disabled = true;
  fetch('api/settings/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: $('#setPwInput').value })
  }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
    .then(function (x) {
      btn.disabled = false;
      if (x.ok) {
        var cb = PWCB;
        closePw();
        if (cb) cb();
      } else {
        var e = $('#setPwErr');
        e.textContent = x.j.error || '口令不正确';
        e.classList.remove('hidden');
        $('#setPwInput').select();
      }
    }).catch(function () {
      btn.disabled = false;
      $('#setPwErr').textContent = '网络错误，稍后再试';
      $('#setPwErr').classList.remove('hidden');
    });
}
/* 撞到 401 settings_auth → 弹口令遮罩 → 解锁后自动重试原请求 */
function gatedFetch(url, opts) {
  return fetch(url, opts).then(function (r) {
    if (r.status !== 401) return r;
    return r.clone().json().catch(function () { return {}; }).then(function (j) {
      if (j.error !== 'settings_auth') return r;
      return new Promise(function (resolve, reject) {
        var cb = function () { resolve(fetch(url, opts)); };
        cb.cancel = function () { var e = new Error('已取消'); e.code = 'pw_cancel'; reject(e); };
        openPw(cb);
      });
    });
  });
}

/* ---------------- 练习 ---------------- */
function loadBatch(onlyWrong) {
  $('#doneCard').classList.add('hidden');
  $('#fbCard').classList.add('hidden');
  $('#progCard').classList.add('hidden');
  $('#qText').textContent = '正在出题…';
  show('practice');
  var url = 'api/cards?name=' + encodeURIComponent(getUid()) + '&limit=20'
    + (Q.poem ? '&poem=' + encodeURIComponent(Q.poem) : '')
    + (onlyWrong ? '&only_wrong=1' : '');
  api(url).then(function (r) {
    if (!r.ok) { toast('出题失败：' + (r.j.error || r.status)); return; }
    Q.list = r.j.cards || [];
    Q.i = 0; Q.right = 0; Q.wrong = 0; Q.wrongs = [];
    if (!Q.list.length) { finish(true); return; }
    renderCard();
  }).catch(function () { toast('网络错误'); });
}

function renderCard() {
  window.__renders = (window.__renders || 0) + 1;   // 供 UI 测试等"这张卡真的渲染完了"
  var c = Q.list[Q.i];
  Q.answered = false;
  clearTimeout(Q.timer);
  $('#fbCard').classList.add('hidden');
  $('#doneCard').classList.add('hidden');
  $('#qProgress').textContent = '第 ' + (Q.i + 1) + ' / ' + Q.list.length + ' 题';
  $('#qPoem').textContent = c.poem_title + (c.author ? ' · ' + c.author : '');
  $('#qMode').textContent = c.mode === 'choice' ? '选择题' : '填空题';
  $('#qText').textContent = c.prompt;
  $('#qHint').textContent = '';
  var box = $('#choiceBox');
  if (c.mode === 'choice') {
    $('#fillBox').classList.add('hidden');
    box.classList.remove('hidden');
    box.innerHTML = '';
    (c.options || []).forEach(function (op, idx) {
      var b = document.createElement('button');
      b.className = 'opt';
      b.textContent = op;
      b.addEventListener('click', function () { submitChoice(b, op, c); });
      box.appendChild(b);
    });
  } else {
    box.classList.add('hidden');
    $('#fillBox').classList.remove('hidden');
    $('#fillInput').value = '';
  }
}

function submitChoice(btn, value, c) {
  if (Q.answered) return;
  Q.answered = true;
  var right = value === c.answer;
  Array.prototype.forEach.call($('#choiceBox').children, function (b) {
    if (b.textContent === c.answer) b.classList.add('ok');
    else if (b === btn && !right) b.classList.add('bad');
    b.disabled = true;
  });
  api('api/answer', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: getUid(), id: c.id, mode: 'choice', answer: value })
  }).then(function (r) { feedback(r.j, value); })
    .catch(function () { feedback({ ok: right, answer: c.answer, note: '（网络错误，按本地判定）' }, value); });
}

function submitFill() {
  var c = Q.list[Q.i];
  if (Q.answered) return;
  var v = $('#fillInput').value;
  if (!v.trim()) { toast('还没填呢'); return; }
  Q.answered = true;
  $('#btnSubmit').disabled = true;
  api('api/answer', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: getUid(), id: c.id, mode: 'fill', answer: v })
  }).then(function (r) {
    $('#btnSubmit').disabled = false;
    feedback(r.j, v);
  }).catch(function () {
    $('#btnSubmit').disabled = false;
    Q.answered = false;
    toast('网络错误，再试一次');
  });
}

/* 错字高亮：标出与正确答案不同的那一段 */
function markDiff(yours, answer) {
  var a = String(yours || ''), b = String(answer || '');
  var i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  var j = 0;
  while (j < a.length - i && j < b.length - i
         && a[a.length - 1 - j] === b[b.length - 1 - j]) j++;
  return { head: esc(a.slice(0, i)), badA: esc(a.slice(i, a.length - j)),
           badB: esc(b.slice(i, b.length - j)), tail: esc(a.slice(a.length - j)) };
}

function feedback(res, yours) {
  var c = Q.list[Q.i];
  var ok = !!(res && res.ok);
  if (ok) Q.right++;
  else { Q.wrong++; Q.wrongs.push({ q: c.prompt, a: (res && res.answer) || c.answer }); }
  var t = $('#fbTitle');
  t.textContent = ok ? '✅ 对！' : '❌ 不对';
  t.className = 'fb-title ' + (ok ? 'ok' : 'bad');
  $('#fbNote').textContent = (res && res.note) || '';
  var ans = (res && res.answer) || c.answer;
  if (c.mode === 'fill') {
    var d = markDiff(yours, ans);
    $('#fbYoursWrap').classList.remove('hidden');
    $('#fbYours').innerHTML = d.head + (d.badA ? '<span class="x">' + d.badA + '</span>' : '') + d.tail;
    $('#fbAnswer').innerHTML = d.head + (d.badB ? '<span class="y">' + d.badB + '</span>' : '') + d.tail;
  } else {
    $('#fbYoursWrap').classList.add('hidden');
    $('#fbAnswer').textContent = ans;
  }
  $('#fbCard').classList.remove('hidden');
  if (ok) Q.timer = setTimeout(nextCard, 1100);      // 答对自动过；答错等你看完点「下一题」
}

function nextCard() {
  clearTimeout(Q.timer);
  if (Q.i + 1 >= Q.list.length) { finish(false); return; }
  Q.i++;
  renderCard();
}

function finish(empty) {
  var html = '<div class="lbl">这一轮</div><div>答对 <b>' + Q.right + '</b> 题，答错 <b>' + Q.wrong + '</b> 题</div>';
  if (empty) html = '<div>这一轮没有要背的啦 🎉 明天再来！</div>';
  if (Q.wrongs.length) {
    html += '<div class="lbl mt">错过的句子</div><div class="small">';
    Q.wrongs.forEach(function (w) {
      html += '<div class="mt">' + esc(w.q) + '<br><b>' + esc(w.a) + '</b></div>';
    });
    html += '</div>';
  }
  html += '<div class="muted small mt">答错的题一分钟后会再出现；答对的按 1 / 3 / 7 / 14 天再见。</div>';
  $('#doneText').innerHTML = html;
  $('#doneCard').classList.remove('hidden');
}

/* ---------------- 我的进度 ---------------- */
function loadMyProgress() {
  api('api/progress?name=' + encodeURIComponent(getUid())).then(function (r) {
    if (!r.ok) { toast('读取失败'); return; }
    var j = r.j;
    var html = '<div>练过 <b>' + j.cards_touched + '</b> / ' + j.cards_total
      + ' 张卡 · 对 <b>' + j.right + '</b> · 错 <b>' + j.wrong + '</b></div>';
    if (j.by_poem && j.by_poem.length) {
      html += '<table class="tbl mt"><tr><th>篇目</th><th>练过</th><th>对</th><th>错</th></tr>';
      j.by_poem.forEach(function (b) {
        html += '<tr><td>' + esc(b.poem_title) + '</td><td class="num">' + b.seen
          + '</td><td class="num">' + b.right + '</td><td class="num">' + b.wrong + '</td></tr>';
      });
      html += '</table>';
    }
    $('#progBody').innerHTML = html;
    $('#progCard').classList.remove('hidden');
  });
}

/* ---------------- 题库 ---------------- */
function loadDeck() {
  api('api/deck').then(function (r) {
    if (!r.ok) { toast('题库读取失败'); return; }
    $('#deckCount').textContent = r.j.total_poems;
    var box = $('#deckList');
    box.innerHTML = '';
    r.j.poems.forEach(function (p) {
      var d = document.createElement('div');
      d.className = 'deck-item';
      d.innerHTML = '<div class="t">《' + esc(p.title) + '》</div><div class="m">'
        + esc(p.author) + ' · ' + esc(p.dynasty) + ' · ' + p.lines + ' 句 · ' + p.cards + ' 张卡</div>';
      var pre = document.createElement('pre');
      pre.textContent = (p.text || []).join('\n');
      pre.classList.add('hidden');
      var b = document.createElement('button');
      b.className = 'btn ghost tiny mt';
      b.textContent = '▶ 练这一篇';
      b.addEventListener('click', function (ev) { ev.stopPropagation(); Q.poem = p.id; loadBatch(); });
      d.appendChild(b);
      d.appendChild(pre);
      d.addEventListener('click', function () { pre.classList.toggle('hidden'); });
      box.appendChild(d);
    });
  });
}

/* ---------------- 班级（老师） ---------------- */
function loadClass() {
  var box = $('#classBody');
  box.innerHTML = '<div class="card muted">正在读取…</div>';
  gatedFetch('api/stats').then(function (r) {
    if (r.status === 401) throw new Error('need_pw');
    return r.json();
  }).then(function (j) {
    var c = j.class || {};
    var html = '<div class="card"><div class="lbl">📊 全班</div><div>参与学生 <b>' + (c.students || 0)
      + '</b> 人 · 今日答题 <b>' + (c.today_answers || 0) + '</b> 次 · 题库 <b>' + (c.cards || 0) + '</b> 张卡</div></div>';
    html += '<div class="card"><div class="lbl">👥 每个学生（匿名编号）</div><table class="tbl"><tr><th>编号</th><th>今日</th>'
      + '<th>练过</th><th>对/错</th><th>正确率</th></tr>';
    (j.students || []).forEach(function (s) {
      var uid = String(s.name || '');
      html += '<tr><td title="' + esc(uid) + '">' + esc(uid.slice(0, 8))
        + (uid.length > 8 ? '…' : '') + '</td><td class="num">' + s.today + '</td><td class="num">'
        + s.cards_touched + '</td><td class="num">' + s.right + '/' + s.wrong + '</td><td class="num">'
        + s.accuracy + '%</td></tr>';
    });
    html += '</table></div>';
    html += '<div class="card"><div class="lbl">🔥 最容易错的（全班累计）</div><table class="tbl">';
    (j.top_wrong || []).forEach(function (w) {
      html += '<tr><td>' + esc(w.prompt) + '<br><b>' + esc(w.answer) + '</b></td><td>'
        + esc(w.poem) + '</td><td class="num">' + w.wrong + ' 次</td></tr>';
    });
    html += '</table></div>';
    box.innerHTML = html;
  }).catch(function (e) {
    box.innerHTML = '<div class="card muted">' + (e && e.code === 'pw_cancel' ? '已取消' : '读取失败') + '</div>';
  });
}

/* ---------------- 绑定 ---------------- */
document.addEventListener('DOMContentLoaded', function () {
  $('#btnDeck').addEventListener('click', function () { show('deck'); loadDeck(); });
  $('#btnTeacher').addEventListener('click', function () { show('teacher'); loadClass(); });
  $('#btnBackFromDeck').addEventListener('click', function () { show('practice'); });
  $('#btnBackFromTeacher').addEventListener('click', function () { show('practice'); });
  $('#btnClassRefresh').addEventListener('click', loadClass);
  $('#btnSubmit').addEventListener('click', submitFill);
  $('#fillInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') submitFill(); });
  $('#btnNext').addEventListener('click', nextCard);
  $('#btnAgain').addEventListener('click', function () { Q.poem = ''; loadBatch(); });
  $('#btnOnlyWrong').addEventListener('click', function () { loadBatch(true); });
  $('#btnMyProgress').addEventListener('click', loadMyProgress);
  $('#btnCloseProg').addEventListener('click', function () { $('#progCard').classList.add('hidden'); });
  $('#btnHint').addEventListener('click', function () {
    var a = String(Q.list[Q.i].answer || '');
    $('#qHint').textContent = '提示：开头是「' + a.slice(0, 2) + '…」，共 '
      + a.replace(/[，。？！；：、]/g, '').length + ' 个字';
  });
  $('#btnGiveUp').addEventListener('click', function () {
    var c = Q.list[Q.i];
    if (Q.answered) return;
    Q.answered = true;
    api('api/answer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: getUid(), id: c.id, mode: 'fill', answer: '' })
    }).then(function () {
      feedback({ ok: false, answer: c.answer, note: '看了答案 —— 这张一会儿还会再出现' }, '');
    });
  });
  $('#btnSetPwOk').addEventListener('click', tryPw);
  $('#btnSetPwCancel').addEventListener('click', function () { closePw(); });
  $('#setPwInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') tryPw(); });

  loadBatch();          // 匿名身份，直接开练（无起名/登录步骤）
});
