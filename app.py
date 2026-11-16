import os, re, time, json, hashlib, secrets, sqlite3, subprocess, threading, shutil, sys, signal, zipfile, io, requests
from urllib.parse import urlsplit
import shlex, mimetypes, functools
try:
    import resource
except ImportError:
    resource = None
from contextlib import contextmanager
from datetime import timedelta
from flask import Flask, request, jsonify, redirect, url_for, render_template, Response, send_from_directory, session, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import DictLoader

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('DB_PATH', os.path.join(BASE, 'codesync.db'))
SANDBOX = os.path.join(BASE, 'sandbox')
os.makedirs(SANDBOX, exist_ok=True)

active_runs = {}
INVITE_CODE = os.environ.get('INVITE_CODE', '').strip()

# ============================================================
# HTML SHABLONLAR (PWA qo'shilgan)
# ============================================================
TEMPLATES = {
'base.html': r'''<!DOCTYPE html>
<html lang="uz"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}CodeSync{% endblock %}</title>

<!-- PWA va Ikonkalar -->
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon.png">
<meta name="theme-color" content="#1e1e1e">
<link rel="icon" href="/icon.png" type="image/png">

<link rel="stylesheet" href="/app.css">
{% block head %}{% endblock %}
</head><body>
{% block body %}{% endblock %}
{% block scripts %}{% endblock %}

<!-- Service Worker ro'yxatdan o'tkazish -->
<script>
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function() {
      navigator.serviceWorker.register('/service-worker.js').then(function(reg) {
        console.log('ServiceWorker registered', reg.scope);
      }).catch(function(err) {
        console.log('ServiceWorker registration failed', err);
      });
    });
  }
</script>
</body></html>''',

'landing.html': r'''{% extends "base.html" %}
{% block title %}CodeSync{% endblock %}
{% block body %}
<div class="center"><div class="card" style="text-align:center">
  <h1 class="brand">⚡ CodeSync</h1>
  <p class="muted" style="margin:8px 0 26px">Jamoa bilan real vaqtda birga kod yozing</p>
  <div style="display:flex;gap:10px">
    <a href="/login" class="btn btn-sec" style="flex:1">Kirish</a>
    <a href="/register" class="btn btn-pri" style="flex:1">Ro'yxatdan o'tish</a>
  </div>
</div></div>
{% endblock %}''',

'auth.html': r'''{% extends "base.html" %}
{% block title %}CodeSync — {{ 'Kirish' if mode == 'login' else "Ro'yxatdan o'tish" }}{% endblock %}
{% block body %}
<div class="center"><div class="card">
  <h2 style="text-align:center;color:#fff;margin-bottom:18px">{{ 'Tizimga kirish' if mode == 'login' else "Ro'yxatdan o'tish" }}</h2>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST" style="display:flex;flex-direction:column;gap:12px">
    <input class="input" type="text" name="username" placeholder="Foydalanuvchi nomi" required autocomplete="username" autocapitalize="none">
    <input class="input" type="password" name="password" placeholder="Parol" required>
    {% if mode == 'register' and invite_required %}<input class="input" type="text" name="invite" placeholder="Taklif kodi" required autocomplete="off" autocapitalize="none">{% endif %}
    <button class="btn btn-pri" type="submit" style="padding:11px;margin-top:6px">{{ 'Kirish' if mode == 'login' else 'Yaratish' }}</button>
  </form>
  <p class="muted" style="text-align:center;margin-top:18px;font-size:13px">
    {% if mode == 'login' %}Akkauntingiz yo'qmi? <a class="lnk" href="/register">Ro'yxatdan o'tish</a>
    {% else %}Akkauntingiz bormi? <a class="lnk" href="/login">Kirish</a>{% endif %}
  </p>
</div></div>
{% endblock %}''',

'dashboard.html': r'''{% extends "base.html" %}
{% block title %}CodeSync — Loyihalar{% endblock %}
{% block body %}
<div class="page">
  <header class="bar">
    <div class="bar-l"><b class="logo">⚡ CodeSync</b></div>
    <div class="bar-r"><span class="muted">{{ username }}</span><a href="/logout" class="btn btn-sec">Chiqish</a></div>
  </header>
  <div class="wrap">
    <div class="row">
      <h2 style="font-weight:500;color:#fff">Loyihalarim</h2>
      <button class="btn btn-pri" id="open-modal">+ Yangi loyiha</button>
    </div>
    <form class="joinbox" id="join-form">
      <input class="input" id="join-code" placeholder="Jamoa kodi bilan qo'shilish" autocomplete="off" autocapitalize="none">
      <button class="btn btn-sec" type="submit">Qo'shilish</button>
    </form>
    <div id="join-msg" class="err hidden"></div>
    <div id="projects" class="grid"></div>
  </div>
</div>
<div id="modal" class="modal hidden"><div class="modal-box">
  <h3 style="color:#fff;margin-bottom:14px">Yangi loyiha</h3>
  <input class="input" id="proj-name" placeholder="Loyiha nomi" maxlength="60" style="margin-bottom:14px">
  <div class="muted" style="font-size:12px;margin-bottom:8px">Loyiha turi:</div>
  <div class="types">
    {% for key, t in templates.items() %}
    <button type="button" class="type-card" data-type="{{ key }}"><span style="font-size:22px">{{ t.icon }}</span><br>{{ t.name }}</button>
    {% endfor %}
  </div>
  <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:18px">
    <button class="btn btn-sec" id="cancel-modal">Bekor qilish</button>
    <button class="btn btn-pri" id="create-btn" disabled>Yaratish</button>
  </div>
</div></div>
{% endblock %}
{% block scripts %}<script src="/dashboard.js"></script>{% endblock %}''',

'editor.html': r'''{% extends "base.html" %}
{% block title %}Muharrir — CodeSync{% endblock %}
{% block head %}
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/material-darker.min.css">
{% endblock %}
{% block body %}
<div class="ide" id="ide" data-project="{{ project_id }}" data-user="{{ username }}" data-uid="{{ uid }}">
  <header class="bar">
    <div class="bar-l">
      <a class="btn btn-sec" href="/dashboard">←</a>
      <b class="logo">⚡ CodeSync</b>
      <span class="room" id="room-code" title="Kodni nusxalash">#...</span>
    </div>
    <div class="bar-r">
      <span id="online" class="muted"></span>
      <span id="save-state" class="muted"></span>
      <span id="status" class="muted">🟡</span>
      <a href="/project/{{ project_id }}/terminal" class="btn btn-sec" target="_blank">📟 Terminal</a>
      <a href="/project/{{ project_id }}/preview" class="btn btn-sec" target="_blank">🌐 Preview</a>
      <a href="/project/{{ project_id }}/static" class="btn btn-sec" target="cs_static_{{ project_id }}">📄 Static</a>
      <a href="/project/{{ project_id }}/messages" class="btn btn-sec" target="_blank">💬 Chat</a>
      <a href="/api/projects/{{ project_id }}/download" class="btn btn-sec" style="color:#00ff00; border-color:#00ff00" target="_blank">⬇ ZIP</a>
      <button class="btn btn-pri" id="run-btn">▶ Run</button>
      <button class="btn btn-sec" id="panel-btn" title="Ovozli chat">🎤</button>
    </div>
  </header>
  <div class="body">
    <aside class="side">
      <div class="side-h"><span>FAYLLAR</span><button class="btn btn-sec" id="new-file" style="padding:2px 9px">+</button></div>
      <div class="tree" id="file-tree"></div>
    </aside>
    <main class="main">
      <div class="tabs" id="tabs"></div>
      <div id="editor-container"></div>
      <section class="term" id="bottom-term">
        <div class="term-h">
          <span>TERMINAL</span>
          <div>
            <button class="btn btn-sec" id="clear-term" style="padding:1px 8px">Tozalash</button>
            <button class="btn btn-danger" id="stop-term" style="padding:1px 8px;display:none;">To'xtatish</button>
          </div>
        </div>
        <div id="term-out"></div>
        <form id="term-form" class="term-in"><span id="term-prompt">$</span><input id="term-input" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="buyruq yozing (help)"></form>
      </section>
    </main>
    <aside class="right hidden" id="right">
      <div class="rt">
        <button class="tab active" id="voice-tab">🎤 Ovozli chat</button>
        <button class="tab" id="close-right" style="flex:0 0 40px">✕</button>
      </div>
      <div class="pane" id="pane-voice">
        <div class="voice" style="padding:20px;display:flex;flex-direction:column;gap:10px;height:100%">
          <div id="voice-status" class="muted" style="text-align:center"></div>
          <div id="voice-peers" style="flex:1;overflow:auto;display:flex;flex-direction:column;gap:6px;background:#1e1e1e;padding:10px;border-radius:6px"></div>
          <div id="voice-audio" style="display:none"></div>
          <div style="display:flex;gap:10px">
            <button class="btn btn-pri" id="voice-join" style="flex:1">🎤 Qo'shilish</button>
            <button class="btn btn-sec hidden" id="voice-mute" style="flex:1">🔇 O'chirish</button>
            <button class="btn btn-danger hidden" id="voice-leave" style="flex:1">📞 Chiqish</button>
          </div>
          <div class="muted" style="font-size:11px;text-align:center">Ovoz fonda ham ishlashi uchun bu oynani yopmang. Terminal, Preview va Chat yangi oynada ochiladi.</div>
        </div>
      </div>
    </aside>
  </div>
</div>
<div id="loading" class="loading">Muharrir yuklanmoqda...</div>
{% endblock %}
{% block scripts %}
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/xml/xml.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/javascript/javascript.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/css/css.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/htmlmixed/htmlmixed.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/python/python.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/markdown/markdown.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/closebrackets.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/matchbrackets.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/edit/closetag.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/addon/selection/active-line.min.js"></script>
<script src="/icons.js"></script>
<script src="/editor.js"></script>
{% endblock %}''',

'terminal.html': r'''{% extends "base.html" %}
{% block title %}Terminal — {{ project_name }}{% endblock %}
{% block body %}
<div class="ide" style="background:#000;">
  <header class="bar">
    <div class="bar-l">
      <a class="btn btn-sec" href="/project/{{ project_id }}">← Orqaga</a>
      <b class="logo" style="color:#00ff00">📟 Terminal — {{ project_name }}</b>
    </div>
    <div class="bar-r">
      <button class="btn btn-sec" id="clear-full-term">Tozalash</button>
      <button class="btn btn-danger" id="stop-full-term" style="display:none;">To'xtatish</button>
    </div>
  </header>
  <div style="flex:1;padding:10px;overflow:auto;" id="full-term-out"></div>
  <form id="full-form" class="term-in" style="background:#000;border-top:1px solid #222"><span id="full-prompt" style="color:#00ff00"></span><input id="full-input" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="buyruq yozing (help)"></form>
</div>
<script>
  var PID = "{{ project_id }}";
  var PNAME = {{ project_name|tojson }};
  var term = document.getElementById('full-term-out');
  var stopBtn = document.getElementById('stop-full-term');
  var since = 0, rid = 0, polling = false, cwd = '', hist = [], hi = 0;

  function append(text, kind) {
    var d = document.createElement('pre');
    d.style.margin = '0'; d.style.whiteSpace = 'pre-wrap'; d.style.fontFamily = 'monospace'; d.style.wordBreak = 'break-word';
    d.style.color = kind === 'err' ? '#ff6b6b' : kind === 'sys' ? '#8b9bb4' : '#00ff00';
    d.textContent = text; term.appendChild(d); term.scrollTop = term.scrollHeight;
  }
  function promptText() { return '$ /' + PNAME + (cwd ? '/' + cwd : ''); }
  function setPrompt() { document.getElementById('full-prompt').textContent = promptText(); }

  async function poll() {
    if (polling) return; polling = true;
    try {
      var r = await fetch('/api/projects/' + PID + '/terminal/poll', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({since: since, rid: rid}) });
      var d = await r.json();
      if (d.lines && d.lines.length) d.lines.forEach(function(l) { append(l.text.replace(/\n$/, ''), l.err ? 'err' : 'out'); });
      if (d.since !== undefined) since = d.since;
      if (d.rid !== undefined) rid = d.rid;
      stopBtn.style.display = d.running ? 'inline-block' : 'none';
    } catch (e) {} finally { polling = false; }
  }

  document.getElementById('clear-full-term').onclick = function() { term.innerHTML = ''; };
  stopBtn.onclick = function() {
    fetch('/api/projects/' + PID + '/terminal/stop', {method: 'POST'}).then(function() { stopBtn.style.display = 'none'; });
  };
  document.getElementById('full-form').onsubmit = async function(e) {
    e.preventDefault();
    var inp = document.getElementById('full-input'), cmd = inp.value.trim();
    if (!cmd) return;
    inp.value = ''; hist.push(cmd); hi = hist.length;
    append(promptText() + ' ' + cmd, 'sys');
    try {
      var r = await fetch('/api/projects/' + PID + '/terminal/exec', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: cmd, cwd: cwd}) });
      var d = await r.json();
      if (!r.ok) { append(d.error || ('Xato ' + r.status), 'err'); return; }
      if (d.clear) term.innerHTML = '';
      if (d.out) append(d.out.replace(/\n$/, ''), d.err ? 'err' : 'out');
      cwd = d.cwd || ''; setPrompt();
      poll();
    } catch (err) { append(String(err.message || err), 'err'); }
  };
  document.getElementById('full-input').onkeydown = function(e) {
    if (e.key === 'ArrowUp' && hist.length) { e.preventDefault(); hi = Math.max(0, hi - 1); this.value = hist[hi] || ''; }
    else if (e.key === 'ArrowDown' && hist.length) { e.preventDefault(); hi = Math.min(hist.length, hi + 1); this.value = hist[hi] || ''; }
  };
  setPrompt();
  append('Terminal tayyor. "help" yozing.', 'sys');
  setInterval(poll, 500);
  poll();
</script>
{% endblock %}''',

'messages.html': r'''{% extends "base.html" %}
{% block title %}Chat — {{ project_name }}{% endblock %}
{% block body %}
<div class="ide" id="chat-root" data-project="{{ project_id }}" data-uid="{{ uid }}">
  <header class="bar">
    <div class="bar-l">
      <a class="btn btn-sec" href="/project/{{ project_id }}">← Orqaga</a>
      <b class="logo">💬 {{ project_name }}</b>
    </div>
    <div class="bar-r">
      <span class="muted" id="mem-count"></span>
      <span id="conn" class="muted">🟡</span>
      <button class="btn btn-sec" id="mem-toggle">👥 A'zolar</button>
    </div>
  </header>
  <div class="chat-wrap">
    <div class="chat-main">
      <div id="msg-list"></div>
      <div class="composer">
        <div id="rbar" class="rbar hidden"><span id="rbar-text"></span><button type="button" class="btn btn-sec" id="rbar-x" style="padding:0 8px">✕</button></div>
        <div id="fchip" class="rbar hidden"><span id="fchip-text"></span><button type="button" class="btn btn-sec" id="fchip-x" style="padding:0 8px">✕</button></div>
        <form id="send-form" style="display:flex;gap:6px">
          <button type="button" class="btn btn-sec" id="file-btn" title="Fayl, rasm yoki video yuborish">📎</button>
          <input type="file" id="file-input" class="hidden">
          <input class="input" id="msg-input" placeholder="Xabar yozing..." autocomplete="off" maxlength="2000">
          <button class="btn btn-pri" id="send-btn" type="submit">➤</button>
        </form>
      </div>
    </div>
    <aside class="members" id="members">
      <div class="muted" style="margin-bottom:8px">A'zolar</div>
      <div id="mem-list"></div>
      <div id="ban-list" style="margin-top:14px"></div>
    </aside>
  </div>
</div>
<div id="lightbox" class="lightbox hidden"></div>
{% endblock %}
{% block scripts %}
<script src="/icons.js"></script>
<script src="/messages.js"></script>
{% endblock %}''',

'static.html': r'''{% extends "base.html" %}
{% block title %}Static — {{ project_name }}{% endblock %}
{% block body %}
<div class="ide" style="background:#111;">
  <header class="bar">
    <div class="bar-l">
      <a class="btn btn-sec" href="/project/{{ project_id }}">← Orqaga</a>
      <b class="logo">📄 Static ko'rish — {{ project_name }}</b>
    </div>
    <div class="bar-r">
      <label class="muted" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="auto" checked> Avto-yangilash</label>
      <button class="btn btn-sec" id="reload">⟳ Yangilash</button>
      <a class="btn btn-sec" id="open-new" href="/s/{{ token }}/" target="_blank">↗ Alohida oynada</a>
    </div>
  </header>
  <div style="flex:1;background:#fff;position:relative">
    <iframe id="frame" src="/s/{{ token }}/" sandbox="allow-scripts allow-forms allow-modals allow-popups" style="position:absolute;inset:0;width:100%;height:100%;border:0"></iframe>
  </div>
</div>
<script>
  var PID = "{{ project_id }}";
  var frame = document.getElementById('frame');
  var last = null;
  function reload() { frame.src = '/s/{{ token }}/?r=' + Date.now(); }
  document.getElementById('reload').onclick = reload;
  async function check() {
    try {
      var r = await fetch('/api/projects/' + PID + '/versions', { credentials: 'same-origin' });
      if (!r.ok) return;
      var d = await r.json();
      if (last !== null && d.v !== last && document.getElementById('auto').checked) reload();
      last = d.v;
    } catch (e) {}
  }
  setInterval(check, 2000);
  check();
</script>
{% endblock %}''',

'stopped.html': r'''<!DOCTYPE html>
<html lang="uz"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Texnik ishlar — CodeSync</title>
<link rel="stylesheet" href="/app.css">
</head><body>
<div class="center"><div class="card" style="text-align:center;max-width:460px">
  <div style="font-size:54px">🛠️</div>
  <h2 style="color:#fff;margin:10px 0">Texnik ishlar olib borilmoqda</h2>
  <p class="muted" style="font-size:14px">{{ message or "Sayt vaqtincha to'xtatilgan. Iltimos, birozdan keyin qayta urinib ko'ring." }}</p>
  <p class="muted" style="margin-top:14px;font-size:12px">Sahifa har 30 soniyada avtomatik yangilanadi.</p>
</div></div>
</body></html>''',

'admin_login.html': r'''{% extends "base.html" %}
{% block title %}Admin — CodeSync{% endblock %}
{% block body %}
<div class="center"><div class="card">
  <h2 style="text-align:center;color:#fff;margin-bottom:18px">🔐 Admin panel</h2>
  {% if disabled %}<div class="err">Admin paroli sozlanmagan. Serverda ADMIN_PASS muhit o'zgaruvchisini o'rnating.</div>{% endif %}
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST" style="display:flex;flex-direction:column;gap:12px">
    <input class="input" type="text" name="username" placeholder="Admin login" required autocomplete="username" autocapitalize="none">
    <input class="input" type="password" name="password" placeholder="Parol" required autocomplete="current-password">
    <button class="btn btn-pri" type="submit" style="padding:11px;margin-top:6px">Kirish</button>
  </form>
</div></div>
{% endblock %}''',

'admin.html': r'''{% extends "base.html" %}
{% block title %}Admin — CodeSync{% endblock %}
{% block body %}
<div class="page">
  <header class="bar">
    <div class="bar-l"><b class="logo">🔐 CodeSync Admin</b></div>
    <div class="bar-r"><a href="/dashboard" class="btn btn-sec">Sayt</a><a href="/admin/logout" class="btn btn-sec">Chiqish</a></div>
  </header>
  <div class="wrap" style="max-width:1100px">
    <h3 style="color:#fff;margin-bottom:10px">Server holati</h3>
    <div id="stats" class="stats"></div>

    <h3 style="color:#fff;margin:22px 0 10px">Texnik ishlar</h3>
    <div class="panel">
      <div id="maint-state" class="muted" style="margin-bottom:8px"></div>
      <input class="input" id="maint-msg" placeholder="Foydalanuvchilarga ko'rsatiladigan xabar (ixtiyoriy)" maxlength="300" style="margin-bottom:10px">
      <button class="btn btn-danger" id="maint-btn" data-on="0">Texnik ishlar rejimini yoqish</button>
    </div>

    <h3 style="color:#fff;margin:22px 0 10px">Foydalanuvchilar</h3>
    <input class="input" id="user-filter" placeholder="Qidirish..." style="margin-bottom:10px">
    <div style="overflow-x:auto">
      <table class="tbl">
        <thead><tr><th>ID</th><th>Login</th><th>Loyiha (o'z / a'zo)</th><th>Oxirgi faollik</th><th>Amallar</th></tr></thead>
        <tbody id="users"></tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
{% block scripts %}<script src="/admin.js"></script>{% endblock %}''',

'preview.html': r'''{% extends "base.html" %}
{% block title %}Preview — {{ project_name }}{% endblock %}
{% block body %}
<div class="ide" style="background:#111;">
  <header class="bar">
    <div class="bar-l">
      <a class="btn btn-sec" href="/project/{{ project_id }}">← Orqaga</a>
      <b class="logo" style="color:#00ff00">🌐 Veb-Server Preview</b>
    </div>
    <div class="bar-r">
      <input type="text" id="url-input" class="input" value="{{ port }}/" style="width:220px; padding:4px 8px;" placeholder="{{ port }}/">
      <button class="btn btn-pri" onclick="loadPreview()">Ochish</button>
      <button class="btn btn-sec" onclick="document.getElementById('preview-iframe').src = document.getElementById('preview-iframe').src;">⟳ Yangilash</button>
      <button class="btn btn-danger" onclick="stopServer()">⏹ Serverni To'xtatish</button>
    </div>
  </header>
  <div style="flex:1; background:#fff; display:flex; flex-direction:column; position:relative;">
    <iframe id="preview-iframe" sandbox="allow-scripts allow-forms allow-modals allow-popups" style="flex:1; width:100%; border:none; display:none;"></iframe>
    <div id="empty-msg" style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); color:#666; font-family:sans-serif; text-align:center; padding:20px;">
      <h2 style="margin-bottom:10px;">Veb-server Preview (Real Server)</h2>
      <p>Serveringiz <code>PORT</code> muhit o'zgaruvchisidagi portda ishlashi kerak:</p>
      <p><code>app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5001)))</code></p>
      <p>Bu loyihaning porti: <b>{{ port }}</b>. Run bosing, so'ng "Ochish".</p>
    </div>
  </div>
</div>
<script>
  function loadPreview() {
    var url = document.getElementById('url-input').value.trim();
    var iframe = document.getElementById('preview-iframe');
    var emptyMsg = document.getElementById('empty-msg');
    
    if (url) {
      iframe.src = '/px/{{ token }}/' + url.replace(/^\/+/, '');
      iframe.style.display = 'block';
      emptyMsg.style.display = 'none';
    }
  }
  
  async function stopServer() {
    if (!confirm("Server jarayonini to'xtatishni xohlaysizmi?")) return;
    try {
      var r = await fetch('/api/projects/{{ project_id }}/terminal/stop', { method: 'POST' });
      if (r.ok) {
        alert("Server to'xtatildi!");
        document.getElementById('preview-iframe').src = 'about:blank';
        document.getElementById('preview-iframe').style.display = 'none';
        document.getElementById('empty-msg').style.display = 'block';
      }
    } catch (e) { alert("Xato: " + e.message); }
  }

  window.onload = function() { setTimeout(loadPreview, 500); };
</script>
{% endblock %}''',
}

# ============================================================
# CSS
# ============================================================
APP_CSS = r'''
:root{--bg:#1e1e1e;--side:#252526;--bar:#323233;--hover:#2a2d2e;--active:#37373d;--border:#3c3c3c;--text:#cccccc;--muted:#8b9bb4;--pri:#0e7fd0;--pri-h:#1b90e6;--danger:#f14c4c;--ok:#4ec9b0}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
button,input{font:inherit;color:inherit}
button{cursor:pointer}
a{color:inherit;text-decoration:none}
.hidden{display:none!important}
.muted{color:var(--muted);font-size:13px}
.lnk{color:var(--pri-h)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:7px 12px;border:1px solid transparent;border-radius:6px;font-size:13px;white-space:nowrap}
.btn-pri{background:var(--pri);color:#fff}.btn-pri:hover{background:var(--pri-h)}
.btn-sec{background:#3a3d41;color:var(--text)}.btn-sec:hover{background:#4a4e53}
.btn-danger{background:transparent;color:var(--danger);border-color:var(--danger)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.input{width:100%;padding:10px 12px;background:#3c3c3c;border:1px solid var(--border);border-radius:6px;color:#fff;outline:none}
.input:focus{border-color:var(--pri)}
.err{background:rgba(241,76,76,.12);border:1px solid var(--danger);color:#ff8a8a;padding:9px 12px;border-radius:6px;margin-bottom:14px;font-size:13px;text-align:center}
.center{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;background:radial-gradient(circle at center,#252526,#111)}
.card{background:var(--side);border:1px solid var(--border);border-radius:12px;padding:32px;width:100%;max-width:380px}
.brand{color:var(--pri-h);font-size:32px}

.bar{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 10px;background:var(--bar);border-bottom:1px solid #000;flex:none;overflow-x:auto}
.bar-l,.bar-r{display:flex;align-items:center;gap:8px;flex:none}
.logo{color:#fff}
.room{background:#1e1e1e;padding:3px 9px;border-radius:10px;font-size:12px;cursor:pointer}

.page{min-height:100vh;display:flex;flex-direction:column}
.wrap{max-width:900px;width:100%;margin:0 auto;padding:24px 16px}
.row{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.joinbox{display:flex;gap:8px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.proj{background:var(--side);border:1px solid var(--border);border-radius:10px;padding:16px;cursor:pointer;position:relative}
.proj:hover{border-color:var(--pri)}
.proj h3{color:#fff;font-size:15px;margin:10px 0 8px;word-break:break-word}
.proj .meta{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);gap:6px}
.badge{background:var(--bg);padding:2px 7px;border-radius:4px}
.proj .del{position:absolute;top:10px;right:10px;background:none;border:0;color:var(--muted);font-size:16px}
.proj .del:hover{color:var(--danger)}
.empty{grid-column:1/-1;text-align:center;padding:40px;color:var(--muted)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:100;display:flex;align-items:center;justify-content:center;padding:16px}
.modal-box{background:var(--side);border:1px solid var(--border);border-radius:10px;padding:22px;width:100%;max-width:420px}
.types{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.type-card{padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;text-align:center;font-size:13px}
.type-card.sel{border-color:var(--pri)}

.ide{display:flex;flex-direction:column;height:100vh;height:100dvh;overflow:hidden}
.body{flex:1;display:flex;min-height:0}
.side{width:210px;flex:none;background:var(--side);border-right:1px solid #000;display:flex;flex-direction:column;min-height:0}
.side-h{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;font-size:11px;letter-spacing:.5px;color:var(--muted)}
.tree{flex:1;overflow:auto}
.fi{display:flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;font-size:13px;white-space:nowrap}
.fi:hover{background:var(--hover)}
.fi.active{background:var(--active);color:#fff}
.fi .nm{flex:1;overflow:hidden;text-overflow:ellipsis}
.fi .x{opacity:.4;padding:0 4px}
.fi .x:hover{opacity:1;color:var(--danger)}
.fold{padding:6px 10px;font-size:12px;color:var(--muted)}
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.tabname{padding:6px 12px;background:var(--side);font-size:13px;border-bottom:1px solid #000;min-height:30px;color:#fff}
#editor-container{flex:1;min-height:0;position:relative;background:#212121}
#editor-container .CodeMirror{position:absolute;top:0;left:0;right:0;bottom:0;height:100%;font:14px/1.5 Consolas,Menlo,monospace}
.fallback-ta{position:absolute;top:0;left:0;width:100%;height:100%;background:#212121;color:#eee;border:0;outline:0;padding:10px;font:14px/1.5 Consolas,Menlo,monospace;resize:none;tab-size:2}
.term{height:200px;flex:none;border-top:1px solid #000;background:#1b1b1b;display:flex;flex-direction:column}
.term-h{display:flex;justify-content:space-between;align-items:center;padding:4px 10px;font-size:11px;letter-spacing:.5px;color:var(--muted);background:var(--side)}
#term-out{flex:1;overflow:auto;padding:6px 10px;font:13px/1.45 Consolas,Menlo,monospace;white-space:pre-wrap;word-break:break-word}
.right{width:340px;flex:none;background:var(--side);border-left:1px solid #000;display:flex;flex-direction:column;min-height:0}
.rt{display:flex;border-bottom:1px solid #000}
.tab{flex:1;padding:10px 4px;background:none;border:0;border-bottom:2px solid transparent;color:var(--muted);font-size:13px}
.tab.active{color:#fff;border-bottom-color:var(--pri)}
.pane{flex:1;min-height:0;display:flex;flex-direction:column}
#chat-box{flex:1;overflow:auto;padding:10px;display:flex;flex-direction:column;gap:7px}
.msg{font-size:13px;word-break:break-word;white-space:pre-wrap}
.msg b{color:var(--ok)}
.msg.me b{color:#6cb6ff}
#chat-form{display:flex;gap:6px;padding:8px;border-top:1px solid #000}
#preview-frame{flex:1;width:100%;border:0;background:#fff}
.voice{padding:30px 20px;text-align:center}
.vrow{display:flex;justify-content:space-between;padding:6px 10px;background:#2d2d2d;border-radius:4px;font-size:13px}
.loading{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:200;color:var(--muted)}
@media(max-width:800px){
  .side{width:140px}
  .right{position:fixed;top:48px;right:0;bottom:0;width:100%;z-index:20}
  .term{height:130px}
}

.tabs{display:flex;overflow-x:auto;background:var(--side);border-bottom:1px solid #000;min-height:34px;flex:none}
.tab-i{display:flex;align-items:center;gap:6px;padding:6px 10px;border-right:1px solid #000;font-size:13px;cursor:pointer;white-space:nowrap;color:var(--muted)}
.tab-i:hover{background:var(--hover)}
.tab-i.active{background:#212121;color:#fff;box-shadow:inset 0 -2px 0 var(--pri)}
.tab-i .x{opacity:.5;padding:0 3px;font-size:16px;line-height:1}
.tab-i .x:hover{opacity:1;color:var(--danger)}
.ic{display:inline-flex;vertical-align:middle}
.ic svg{width:16px;height:16px}
.ic-py{color:#4b8bbe}.ic-js{color:#f0db4f}.ic-html{color:#e44d26}.ic-css{color:#42a5f5}.ic-scss{color:#ec6cb9}
.ic-txt{color:#9aa4b2}.ic-folder{color:#e8b13c}.ic-code{color:#8bc34a}.ic-easel{color:#ff9800}.ic-file{color:#9aa4b2}
.fold{display:flex;align-items:center;gap:4px}
.term-in{display:flex;align-items:center;gap:8px;padding:5px 10px;border-top:1px solid #000;background:#151515;flex:none}
.term-in span{color:#4ec9b0;font:13px Consolas,Menlo,monospace;white-space:nowrap;max-width:50%;overflow:hidden;text-overflow:ellipsis}
.term-in input{flex:1;min-width:0;background:transparent;border:0;outline:0;color:#e6e6e6;font:13px Consolas,Menlo,monospace}

.chat-wrap{flex:1;display:flex;min-height:0}
.chat-main{flex:1;display:flex;flex-direction:column;min-width:0}
#msg-list{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.m{max-width:min(560px,88%);align-self:flex-start;background:#2d2d30;border-radius:10px;padding:8px 10px}
.m.me{align-self:flex-end;background:#1c4a73}
.m.flash{outline:2px solid var(--pri)}
.m .who{font-size:12px;color:var(--ok);font-weight:600;margin-bottom:2px}
.m.me .who{color:#9fd0ff}
.m .txt{white-space:pre-wrap;word-break:break-word;font-size:14px}
.m .meta{font-size:11px;color:var(--muted);margin-top:4px;display:flex;gap:12px;justify-content:flex-end}
.m .rep{border-left:3px solid var(--pri);background:rgba(255,255,255,.07);padding:4px 8px;margin-bottom:6px;border-radius:4px;font-size:12px;cursor:pointer;word-break:break-word}
.m img.att,.m video.att{max-width:100%;max-height:260px;border-radius:8px;display:block;margin-top:4px}
.m img.att{cursor:zoom-in}
.fcard{display:flex;align-items:center;gap:8px;background:rgba(0,0,0,.25);padding:8px;border-radius:8px;margin-top:4px}
.composer{border-top:1px solid #000;padding:8px;background:var(--side);flex:none}
.rbar{display:flex;justify-content:space-between;align-items:center;gap:8px;background:#1e1e1e;border-left:3px solid var(--pri);padding:4px 8px;margin-bottom:6px;border-radius:4px;font-size:12px}
.members{width:250px;flex:none;border-left:1px solid #000;background:var(--side);overflow:auto;padding:10px}
.mem{display:flex;align-items:center;gap:8px;padding:6px 2px;font-size:13px}
.dot{width:9px;height:9px;border-radius:50%;background:#555;flex:none}
.dot.on{background:#3fb950}
.lightbox{position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:300;display:flex;align-items:center;justify-content:center}
.lb-media{max-width:96vw;max-height:92vh;border-radius:6px}
.lb-x{position:absolute;top:12px;right:14px;background:#333;border:0;color:#fff;font-size:18px;border-radius:50%;width:38px;height:38px}

.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.stat{background:var(--side);border:1px solid var(--border);border-radius:10px;padding:12px}
.stat .val{font-size:20px;color:#fff;margin:4px 0;word-break:break-word}
.stat.busy{border-color:#feca57}.stat.overload{border-color:var(--danger)}.stat.ok{border-color:#3fb950}
.panel{background:var(--side);border:1px solid var(--border);border-radius:10px;padding:14px}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th,.tbl td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
.tbl th{color:var(--muted);font-weight:500}
@media(max-width:800px){
  .members{position:fixed;top:48px;right:0;bottom:0;width:80%;max-width:290px;z-index:20;display:none}
  .members.open{display:block}
}
'''

# ============================================================
# DASHBOARD JS
# ============================================================
DASH_JS = r'''
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var selectedType = null;

  async function api(url, method, body) {
    var r = await fetch(url, {
      method: method || 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin'
    });
    if (r.status === 401) { location.href = '/login'; throw new Error('Kirish kerak'); }
    var data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error(data.error || ('Xato ' + r.status));
    return data;
  }

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  var ICONS = { web: '🌐', python: '🐍', flask: '🧪' };

  async function load() {
    var box = $('projects');
    try {
      var list = await api('/api/projects');
      box.innerHTML = '';
      if (!list.length) {
        box.appendChild(h('div', 'empty', "Hali loyihalar yo'q. Yangi loyiha yarating yoki kod bilan qo'shiling."));
        return;
      }
      list.forEach(function (p) {
        var card = h('div', 'proj');
        var ic = h('div', '', ICONS[p.project_type] || '📄');
        ic.style.fontSize = '24px';
        card.appendChild(ic);
        card.appendChild(h('h3', '', p.name));
        var meta = h('div', 'meta');
        meta.appendChild(h('span', 'badge', p.project_type + (p.is_owner ? '' : " · a'zo")));
        meta.appendChild(h('span', '', '#' + p.room_code));
        card.appendChild(meta);
        if (p.is_owner) {
          var del = h('button', 'del', '✕');
          del.title = "O'chirish";
          del.onclick = async function (e) {
            e.stopPropagation();
            if (!confirm('"' + p.name + '" loyihasi butunlay o\'chirilsinmi?')) return;
            try { await api('/api/projects/' + p.id, 'DELETE'); load(); } catch (err) { alert(err.message); }
          };
          card.appendChild(del);
        }
        card.onclick = function () { location.href = '/project/' + p.id; };
        box.appendChild(card);
      });
    } catch (e) {
      box.innerHTML = '';
      box.appendChild(h('div', 'empty', 'Yuklashda xato: ' + e.message));
    }
  }

  $('open-modal').onclick = function () { $('modal').classList.remove('hidden'); $('proj-name').focus(); };
  $('cancel-modal').onclick = function () { $('modal').classList.add('hidden'); };
  document.querySelectorAll('.type-card').forEach(function (c) {
    c.onclick = function () {
      document.querySelectorAll('.type-card').forEach(function (x) { x.classList.remove('sel'); });
      c.classList.add('sel');
      selectedType = c.dataset.type;
      $('create-btn').disabled = false;
    };
  });

  $('create-btn').onclick = async function () {
    var name = $('proj-name').value.trim();
    if (!name) { $('proj-name').focus(); return; }
    if (!selectedType) return;
    $('create-btn').disabled = true;
    try {
      var d = await api('/api/projects', 'POST', { name: name, project_type: selectedType });
      location.href = '/project/' + d.id;
    } catch (e) { alert(e.message); $('create-btn').disabled = false; }
  };

  $('join-form').onsubmit = async function (e) {
    e.preventDefault();
    var code = $('join-code').value.trim();
    var msg = $('join-msg');
    msg.classList.add('hidden');
    if (!code) return;
    try {
      var d = await api('/api/join', 'POST', { code: code });
      location.href = '/project/' + d.id;
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.remove('hidden');
    }
  };

  load();
})();
'''

# ============================================================
# EDITOR JS
# ============================================================
EDITOR_JS = r'''
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var csIcon = window.csIcon || function () { return '📄 '; };
  var ide = $('ide');
  var PID = ide.dataset.project;
  var ME = ide.dataset.user;
  var UID = parseInt(ide.dataset.uid, 10);
  var state = { files: [], tabs: [], active: null, project: null };
  var ed = null, applying = false, syncing = false, running = false;
  var termSince = 0, termRid = 0, termPollTimer = null, termCwd = '', termHist = [], termHi = 0;

  async function api(url, method, body) {
    var r = await fetch(url, {
      method: method || 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin'
    });
    if (r.status === 401) { location.href = '/login'; throw new Error('Kirish kerak'); }
    var data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) { var er = new Error(data.error || ('Xato ' + r.status)); er.status = r.status; throw er; }
    return data;
  }
  function byId(id) { return state.files.find(function (f) { return f.id === id; }); }
  function extOf(n) { var i = n.lastIndexOf('.'); return i < 0 ? '' : n.slice(i + 1).toLowerCase(); }
  function modeFor(n) {
    var e = extOf(n);
    if (e === 'html' || e === 'htm') return 'htmlmixed';
    if (e === 'css') return 'css';
    if (e === 'js') return 'javascript';
    if (e === 'json') return { name: 'javascript', json: true };
    if (e === 'py') return 'python';
    if (e === 'md') return 'markdown';
    return 'text/plain';
  }
  var COLORS = { log: '#d4d4d4', info: '#6cb6ff', warn: '#feca57', error: '#ff6b6b', sys: '#8b9bb4', ok: '#4ec9b0' };
  function term(text, kind) {
    var box = $('term-out');
    var d = document.createElement('div');
    d.style.color = COLORS[kind || 'log'] || kind;
    d.textContent = text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }
  $('clear-term').onclick = function () { $('term-out').innerHTML = ''; };

  // ---------- muharrir ----------
  function makeEditor(parent) {
    var handlers = [];
    if (window.CodeMirror) {
      var cm = CodeMirror(parent, {
        value: '', lineNumbers: true, theme: 'material-darker', indentUnit: 2, tabSize: 2,
        styleActiveLine: true, matchBrackets: true, autoCloseBrackets: true, autoCloseTags: true,
        extraKeys: { 'Ctrl-S': function () { flushSave(); }, 'Ctrl-Enter': function () { runCode(); } }
      });
      cm.on('change', function () { if (!applying) handlers.forEach(function (fn) { fn(); }); });
      return {
        get: function () { return cm.getValue(); },
        load: function (text, mode) { applying = true; cm.setValue(text); cm.clearHistory(); cm.setOption('mode', mode); applying = false; cm.refresh(); },
        patch: function (text) {
          if (cm.getValue() === text) return;
          var cur = cm.getCursor(), sc = cm.getScrollInfo(), last = cm.lastLine();
          applying = true;
          cm.replaceRange(text, { line: 0, ch: 0 }, { line: last, ch: cm.getLine(last).length });
          cm.setCursor(cur); cm.scrollTo(sc.left, sc.top);
          applying = false;
        },
        onChange: function (fn) { handlers.push(fn); }
      };
    }
    var ta = document.createElement('textarea');
    ta.className = 'fallback-ta'; ta.spellcheck = false;
    parent.appendChild(ta);
    ta.addEventListener('input', function () { handlers.forEach(function (fn) { fn(); }); });
    return {
      get: function () { return ta.value; },
      load: function (t) { ta.value = t; },
      patch: function (t) { if (ta.value === t) return; var s = ta.selectionStart; ta.value = t; ta.setSelectionRange(s, s); },
      onChange: function (fn) { handlers.push(fn); }
    };
  }

  // ---------- fayllar daraxti va tablar (ikonkalar bilan) ----------
  function renderTree() {
    var box = $('file-tree'); box.innerHTML = '';
    var sorted = state.files.slice().sort(function (a, b) { return a.name.localeCompare(b.name); });
    var lastFolder = '';
    sorted.forEach(function (f) {
      var parts = f.name.split('/');
      var base = parts.pop();
      var folder = parts.join('/');
      if (folder !== lastFolder) {
        lastFolder = folder;
        if (folder) {
          var fo = document.createElement('div'); fo.className = 'fold';
          fo.innerHTML = csIcon('', true);
          var ft = document.createElement('span'); ft.textContent = ' ' + folder; fo.appendChild(ft);
          box.appendChild(fo);
        }
      }
      var el = document.createElement('div');
      el.className = 'fi' + (state.active === f.id ? ' active' : '');
      if (folder) el.style.paddingLeft = '24px';
      var ic = document.createElement('span'); ic.innerHTML = csIcon(base);
      var nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = base;
      var x = document.createElement('span'); x.className = 'x'; x.textContent = '✕'; x.title = "O'chirish";
      el.appendChild(ic); el.appendChild(nm); el.appendChild(x);
      el.onclick = function (e) {
        if (e.target === x) { if (confirm('"' + f.name + '" o\'chirilsinmi?')) deleteFile(f.id); return; }
        openFile(f.id);
      };
      box.appendChild(el);
    });
  }
  function renderTabs() {
    var box = $('tabs'); box.innerHTML = '';
    state.tabs.forEach(function (id) {
      var f = byId(id); if (!f) return;
      var el = document.createElement('div');
      el.className = 'tab-i' + (id === state.active ? ' active' : '');
      var ic = document.createElement('span'); ic.innerHTML = csIcon(f.name);
      var nm = document.createElement('span'); nm.textContent = f.name.split('/').pop(); nm.title = f.name;
      var x = document.createElement('span'); x.className = 'x'; x.textContent = '×'; x.title = 'Yopish';
      el.appendChild(ic); el.appendChild(nm); el.appendChild(x);
      el.onclick = function (e) { if (e.target === x) closeTab(id); else openFile(id); };
      box.appendChild(el);
    });
  }
  function openFile(id) {
    var f = byId(id); if (!f) return;
    if (state.tabs.indexOf(id) < 0) state.tabs.push(id);
    state.active = id;
    ed.load(f.content, modeFor(f.name));
    renderTabs(); renderTree();
  }
  function closeEditor() { state.active = null; ed.load('', 'text/plain'); renderTabs(); renderTree(); }
  function closeTab(id) {
    var i = state.tabs.indexOf(id); if (i < 0) return;
    var f = byId(id); if (f && f.dirty) saveFile(f);
    state.tabs.splice(i, 1);
    if (state.active === id) {
      var next = state.tabs[i] || state.tabs[i - 1];
      if (next) openFile(next); else closeEditor();
    } else renderTabs();
  }
  function pickFirst() {
    var pref = ['index.html', 'app.py', 'main.py', 'main.js'];
    var f = null;
    for (var i = 0; i < pref.length && !f; i++) f = state.files.find(function (x) { return x.name === pref[i]; });
    if (!f) f = state.files.slice().sort(function (a, b) { return a.name.localeCompare(b.name); })[0];
    if (f) openFile(f.id); else closeEditor();
  }

  function setSave(t) { $('save-state').textContent = t; }
  function scheduleSave(f) { clearTimeout(f.timer); setSave('✏'); f.timer = setTimeout(function () { saveFile(f); }, 600); }
  async function saveFile(f) {
    clearTimeout(f.timer); if (!f.dirty) return;
    var text = f.content;
    try {
      var r = await api('/api/files/' + f.id, 'PUT', { content: text });
      f.version = r.version;
      if (f.content === text) { f.dirty = false; setSave('✓ saqlandi'); }
    } catch (e) {
      if (e.status === 404) { dropFile(f.id); return; }
      setSave('⚠ saqlanmadi');
      f.timer = setTimeout(function () { saveFile(f); }, 3000);
    }
  }
  function flushSave() { var f = byId(state.active); if (f) saveFile(f); }

  function dropFile(id) {
    state.files = state.files.filter(function (f) { return f.id !== id; });
    state.tabs = state.tabs.filter(function (t) { return t !== id; });
    if (state.active === id) pickFirst();
    renderTabs(); renderTree();
  }
  async function deleteFile(id) {
    try { await api('/api/files/' + id, 'DELETE'); dropFile(id); } catch (e) { alert(e.message); }
  }
  $('new-file').onclick = async function () {
    var name = prompt('Fayl nomi (papka bilan: templates/index.html):');
    if (!name) return;
    name = name.trim();
    try {
      var d = await api('/api/projects/' + PID + '/files', 'POST', { name: name });
      state.files.push({ id: d.id, name: d.name, content: '', version: d.version, dirty: false, timer: null });
      renderTree(); openFile(d.id);
    } catch (e) { alert(e.message); }
  };

  // ---------- jamoa bilan sinxronlash ----------
  async function sync() {
    if (syncing) return; syncing = true;
    try {
      var versions = {};
      state.files.forEach(function (f) { versions[f.id] = f.version; });
      var r = await api('/api/projects/' + PID + '/sync', 'POST', { versions: versions });
      var changed = false;
      var ids = new Set(r.files.map(function (x) { return x.id; }));
      var before = state.files.length;
      state.files = state.files.filter(function (f) { return ids.has(f.id); });
      if (state.files.length !== before) {
        changed = true;
        state.tabs = state.tabs.filter(function (t) { return !!byId(t); });
        if (!byId(state.active)) pickFirst();
      }
      r.files.forEach(function (s) {
        var f = byId(s.id);
        if (!f) {
          if (s.content === undefined) return;
          state.files.push({ id: s.id, name: s.name, content: s.content, version: s.version, dirty: false, timer: null });
          changed = true; return;
        }
        if (f.name !== s.name) { f.name = s.name; changed = true; }
        if (s.content !== undefined && s.version > f.version && !f.dirty) {
          f.version = s.version; f.content = s.content;
          if (f.id === state.active) ed.patch(s.content);
        }
      });
      if (changed) { renderTree(); renderTabs(); }
      var names = r.online.filter(function (n) { return n !== ME; });
      $('online').textContent = names.length ? '👥 ' + names.join(', ') : '';
      $('status').textContent = '🟢';
    } catch (e) {
      if (e.status === 404) { location.href = '/dashboard'; return; }
      $('status').textContent = '🔴 Oflayn';
    } finally { syncing = false; }
  }

  // ---------- ovozli chat (WebRTC) + fonda ishlash ----------
  var voice = { on: false, stream: null, pcs: {}, since: 0, timer: null, muted: false, ice: [], peers: [], polling: false };
  var KEEP = { audio: null, lock: null, url: null };
  function vstatus(t) { $('voice-status').textContent = t; }
  function sendSignal(to, kind, data) { api('/api/projects/' + PID + '/voice/signal', 'POST', { to: to, kind: kind, data: JSON.stringify(data) }).catch(function () {}); }
  function closePeer(pid) {
    var pc = voice.pcs[pid];
    if (pc) { clearTimeout(pc._dt); pc.onconnectionstatechange = null; pc.onicecandidate = null; try { pc.close(); } catch (e) {} }
    delete voice.pcs[pid];
    var a = $('aud-' + pid); if (a) a.remove();
  }
  function newPc(pid) {
    var pc = new RTCPeerConnection({ iceServers: voice.ice });
    pc._q = []; voice.pcs[pid] = pc;
    voice.stream.getTracks().forEach(function (t) { pc.addTrack(t, voice.stream); });
    pc.onicecandidate = function (e) { if (e.candidate) sendSignal(pid, 'ice', e.candidate); };
    pc.ontrack = function (e) {
      var a = $('aud-' + pid);
      if (!a) { a = document.createElement('audio'); a.id = 'aud-' + pid; a.autoplay = true; a.setAttribute('playsinline', ''); $('voice-audio').appendChild(a); }
      a.srcObject = e.streams[0];
      var p = a.play(); if (p && p.catch) p.catch(function () {});
    };
    pc.onconnectionstatechange = function () {
      clearTimeout(pc._dt);
      if (pc.connectionState === 'failed' && voice.pcs[pid] === pc) closePeer(pid);
      else if (pc.connectionState === 'disconnected') {
        pc._dt = setTimeout(function () { if (voice.pcs[pid] === pc && pc.connectionState !== 'connected') closePeer(pid); }, 8000);
      }
      renderVoice();
    };
    return pc;
  }
  async function flushQ(pc) { while (pc._q.length) { try { await pc.addIceCandidate(pc._q.shift()); } catch (e) {} } }
  async function makeOffer(pid) {
    var pc = newPc(pid);
    try { var offer = await pc.createOffer(); await pc.setLocalDescription(offer); sendSignal(pid, 'offer', pc.localDescription); }
    catch (e) { closePeer(pid); }
  }
  async function handleSignal(s) {
    var from = s.from_user, data;
    try { data = JSON.parse(s.data); } catch (e) { return; }
    try {
      if (s.kind === 'offer') {
        closePeer(from);
        var pc = newPc(from);
        await pc.setRemoteDescription(data);
        await flushQ(pc);
        var ans = await pc.createAnswer();
        await pc.setLocalDescription(ans);
        sendSignal(from, 'answer', pc.localDescription);
      } else if (s.kind === 'answer') {
        var p1 = voice.pcs[from];
        if (p1 && p1.signalingState === 'have-local-offer') { await p1.setRemoteDescription(data); await flushQ(p1); }
      } else if (s.kind === 'ice') {
        var p2 = voice.pcs[from]; if (!p2) return;
        if (p2.remoteDescription) { try { await p2.addIceCandidate(data); } catch (e) {} } else p2._q.push(data);
      }
    } catch (e) { closePeer(from); }
  }
  async function voicePoll() {
    if (!voice.on || voice.polling) return; voice.polling = true;
    try {
      var r = await api('/api/projects/' + PID + '/voice/poll', 'POST', { since: voice.since });
      if (!voice.on) return;
      if (r.gone) { await api('/api/projects/' + PID + '/voice', 'POST', { action: 'join' }); return; }
      voice.ice = r.ice || voice.ice; voice.peers = r.peers;
      var ids = {};
      r.peers.forEach(function (p) { ids[p.id] = true; if (!voice.pcs[p.id] && UID < p.id) makeOffer(p.id); });
      Object.keys(voice.pcs).forEach(function (k) { if (!ids[k]) { var pcx = voice.pcs[k]; if (!pcx || pcx.connectionState !== 'connected') closePeer(+k); } });
      for (var i = 0; i < r.signals.length; i++) {
        var s = r.signals[i]; if (s.id > voice.since) voice.since = s.id;
        await handleSignal(s);
      }
      renderVoice();
    } catch (e) {
      if (e.status === 404) { leaveVoice(); vstatus('Siz loyihadan chiqarildingiz'); }
    } finally { voice.polling = false; }
  }
  function renderVoice() {
    var box = $('voice-peers'); box.innerHTML = '';
    function row(name, st) {
      var d = document.createElement('div'); d.className = 'vrow';
      var n = document.createElement('span'); n.textContent = name;
      var s = document.createElement('span'); s.className = 'muted'; s.textContent = st;
      d.appendChild(n); d.appendChild(s); box.appendChild(d);
    }
    if (voice.on) row(ME + ' (siz)', voice.muted ? '🔇 ovoz o\'chiq' : '🎤');
    voice.peers.forEach(function (p) {
      var pc = voice.pcs[p.id];
      row(p.username, pc && pc.connectionState === 'connected' ? '🟢 ulangan' : '⏳ ulanmoqda...');
    });
    $('voice-tab').textContent = '🎤 Ovozli chat' + (voice.on ? ' (' + (voice.peers.length + 1) + ')' : '');
    $('voice-join').classList.toggle('hidden', voice.on);
    $('voice-mute').classList.toggle('hidden', !voice.on);
    $('voice-leave').classList.toggle('hidden', !voice.on);
    $('voice-mute').textContent = voice.muted ? '🎤 Yoqish' : '🔇 O\'chirish';
    if (voice.on) vstatus(voice.peers.length ? 'Ovozli chat ishlayapti' : 'Boshqalar qo\'shilishini kutyapmiz...');
  }
  // Fonda (boshqa ilovaga o'tganda) uzilmasligi uchun: jim audio + MediaSession + ekran qulfi
  function silentWavUrl() {
    var rate = 8000, n = rate, buf = new ArrayBuffer(44 + n), v = new DataView(buf);
    function s(o, t) { for (var i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); }
    s(0, 'RIFF'); v.setUint32(4, 36 + n, true); s(8, 'WAVE'); s(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
    s(36, 'data'); v.setUint32(40, n, true);
    for (var i = 0; i < n; i++) v.setUint8(44 + i, i % 2 ? 129 : 127);
    return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
  }
  async function acquireLock() {
    try {
      if (navigator.wakeLock && !KEEP.lock) {
        KEEP.lock = await navigator.wakeLock.request('screen');
        KEEP.lock.addEventListener('release', function () { KEEP.lock = null; });
      }
    } catch (e) {}
  }
  function startKeepAlive() {
    try {
      if (!KEEP.url) KEEP.url = silentWavUrl();
      if (!KEEP.audio) { KEEP.audio = new Audio(KEEP.url); KEEP.audio.loop = true; }
      var p = KEEP.audio.play(); if (p && p.catch) p.catch(function () {});
    } catch (e) {}
    acquireLock();
    if ('mediaSession' in navigator) {
      try {
        navigator.mediaSession.metadata = new MediaMetadata({ title: 'Ovozli chat', artist: state.project ? state.project.name : 'CodeSync', album: 'CodeSync' });
        navigator.mediaSession.playbackState = 'playing';
        navigator.mediaSession.setActionHandler('stop', function () { leaveVoice(); });
        navigator.mediaSession.setActionHandler('pause', function () { setMute(true); });
        navigator.mediaSession.setActionHandler('play', function () { setMute(false); });
      } catch (e) {}
    }
  }
  function stopKeepAlive() {
    try { if (KEEP.audio) KEEP.audio.pause(); } catch (e) {}
    try { if (KEEP.lock) KEEP.lock.release(); } catch (e) {}
    KEEP.lock = null;
    if ('mediaSession' in navigator) { try { navigator.mediaSession.playbackState = 'none'; } catch (e) {} }
  }
  function setMute(m) {
    voice.muted = m;
    if (voice.stream) voice.stream.getAudioTracks().forEach(function (t) { t.enabled = !m; });
    renderVoice();
  }
  async function getMic() {
    return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
  }
  async function voiceRecover() {
    if (!voice.on) return;
    var tr = voice.stream && voice.stream.getAudioTracks()[0];
    if (!tr || tr.readyState === 'ended') {
      try {
        var ns = await getMic();
        var nt = ns.getAudioTracks()[0]; nt.enabled = !voice.muted;
        Object.keys(voice.pcs).forEach(function (k) {
          voice.pcs[k].getSenders().forEach(function (sn) { sn.replaceTrack(nt).catch(function () {}); });
        });
        voice.stream = ns;
      } catch (e) { vstatus('Mikrofon qayta ulanmadi, qayta qo\'shiling'); }
    }
    Object.keys(voice.pcs).forEach(function (k) {
      var st = voice.pcs[k].connectionState;
      if (st === 'failed' || st === 'closed' || st === 'disconnected') closePeer(+k);
    });
    document.querySelectorAll('#voice-audio audio').forEach(function (a) { var p = a.play(); if (p && p.catch) p.catch(function () {}); });
    if (KEEP.audio) { var p2 = KEEP.audio.play(); if (p2 && p2.catch) p2.catch(function () {}); }
    acquireLock();
    voicePoll();
  }
  async function joinVoice() {
    if (voice.on) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.RTCPeerConnection) {
      vstatus('Brauzer mikrofonni qo\'llamaydi (sayt HTTPS bo\'lishi kerak)'); return;
    }
    vstatus('Mikrofon so\'ralmoqda...');
    try { voice.stream = await getMic(); }
    catch (e) { vstatus('Mikrofonga ruxsat berilmadi'); return; }
    try { var d = await api('/api/projects/' + PID + '/voice', 'POST', { action: 'join' }); voice.ice = d.ice || []; }
    catch (e) { voice.stream.getTracks().forEach(function (t) { t.stop(); }); voice.stream = null; vstatus('Xato: ' + e.message); return; }
    voice.on = true; voice.since = 0; voice.muted = false; voice.peers = [];
    startKeepAlive();
    renderVoice(); voicePoll();
    voice.timer = setInterval(voicePoll, 800);
  }
  function leaveVoice() {
    if (!voice.on) return;
    voice.on = false; clearInterval(voice.timer);
    Object.keys(voice.pcs).forEach(function (k) { closePeer(+k); });
    if (voice.stream) voice.stream.getTracks().forEach(function (t) { t.stop(); });
    voice.stream = null; voice.peers = [];
    stopKeepAlive();
    api('/api/projects/' + PID + '/voice', 'POST', { action: 'leave' }).catch(function () {});
    renderVoice(); vstatus('Ovozli chatdan chiqdingiz');
  }
  $('voice-join').onclick = joinVoice;
  $('voice-leave').onclick = leaveVoice;
  $('voice-mute').onclick = function () { setMute(!voice.muted); };
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { sync(); voiceRecover(); }
  });
  window.addEventListener('pagehide', function () {
    if (voice.on && navigator.sendBeacon) {
      navigator.sendBeacon('/api/projects/' + PID + '/voice', new Blob([JSON.stringify({ action: 'leave' })], { type: 'application/json' }));
    }
  });
  window.addEventListener('beforeunload', function (e) {
    if (state.files.some(function (f) { return f.dirty; })) { e.preventDefault(); e.returnValue = ''; }
  });

  $('panel-btn').onclick = function () { $('right').classList.toggle('hidden'); };
  $('close-right').onclick = function () { $('right').classList.add('hidden'); };
  $('room-code').onclick = function () {
    var code = state.project ? state.project.room_code : '';
    var el = $('room-code');
    function ok() { el.textContent = 'Nusxalandi ✓'; setTimeout(function () { el.textContent = '#' + code; }, 1200); }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(code).then(ok, function () { prompt('Jamoa kodi:', code); });
    else prompt('Jamoa kodi:', code);
  };

  // ---------- terminal (jarayon chiqishi + buyruq qatori) ----------
  function stopTermPoll() {
    if (termPollTimer) { clearInterval(termPollTimer); termPollTimer = null; }
    $('stop-term').style.display = 'none';
  }
  function startTermPoll(keep) {
    stopTermPoll();
    $('stop-term').style.display = 'inline-block';
    termSince = 0; termRid = 0;
    if (!keep) $('term-out').innerHTML = '';
    termPollTimer = setInterval(async function () {
      try {
        var r = await api('/api/projects/' + PID + '/terminal/poll', 'POST', { since: termSince, rid: termRid });
        termRid = r.rid;
        if (r.lines && r.lines.length) {
          r.lines.forEach(function (l) { term(l.text.replace(/\n$/, ''), l.err ? 'error' : 'log'); });
        }
        termSince = r.since;
        if (!r.running) stopTermPoll();
      } catch (e) { term('Polling xatosi', 'error'); }
    }, 500);
  }
  $('stop-term').onclick = async function () {
    try { await api('/api/projects/' + PID + '/terminal/stop', 'POST'); term('[To\'xtatildi]', 'sys'); stopTermPoll(); }
    catch (e) { alert(e.message); }
  };
  function promptText() { return '$ /' + (state.project ? state.project.name : 'loyiha') + (termCwd ? '/' + termCwd : ''); }
  function updatePrompt() { $('term-prompt').textContent = promptText(); }
  $('term-form').onsubmit = async function (e) {
    e.preventDefault();
    var inp = $('term-input'), cmd = inp.value.trim();
    if (!cmd) return;
    inp.value = ''; termHist.push(cmd); termHi = termHist.length;
    term(promptText() + ' ' + cmd, 'sys');
    try {
      var r = await api('/api/projects/' + PID + '/terminal/exec', 'POST', { cmd: cmd, cwd: termCwd });
      if (r.clear) $('term-out').innerHTML = '';
      if (r.out) term(r.out.replace(/\n$/, ''), r.err ? 'error' : 'log');
      termCwd = r.cwd || ''; updatePrompt();
      if (r.started) startTermPoll(true);
    } catch (err) { term(err.message, 'error'); }
  };
  $('term-input').onkeydown = function (e) {
    if (e.key === 'ArrowUp' && termHist.length) { e.preventDefault(); termHi = Math.max(0, termHi - 1); this.value = termHist[termHi] || ''; }
    else if (e.key === 'ArrowDown' && termHist.length) { e.preventDefault(); termHi = Math.min(termHist.length, termHi + 1); this.value = termHist[termHi] || ''; }
  };

  // ---------- Run ----------
  async function runCode() {
    if (running) return;
    var f = byId(state.active);
    if (!f) { term('Avval faylni oching', 'error'); return; }
    var ext = extOf(f.name);
    var isWeb = state.project && state.project.project_type === 'web';
    if (ext === 'html' || ext === 'htm' || ext === 'css' || (ext === 'js' && isWeb)) {
      window.open('/project/' + PID + '/static', 'cs_static_' + PID);
      term('Static ko\'rish ochildi (o\'zgarishlar avtomatik yangilanadi).', 'ok');
      return;
    }
    if (ext !== 'py') { term('Bu faylni ishga tushirib bo\'lmaydi. Faqat .py (yoki HTML uchun Static).', 'warn'); return; }
    $('term-out').innerHTML = '';
    running = true;
    try {
      term('> python ' + f.name, 'sys');
      var r = await api('/api/projects/' + PID + '/terminal/run', 'POST', { filename: f.name });
      if (r.error) { term(r.error, 'error'); return; }
      if (state.project && state.project.project_type === 'flask') term('Server PORT=' + r.port + ' da ishlaydi. 🌐 Preview orqali oching.', 'info');
      startTermPoll(true);
    } catch (e) { term('Xato: ' + e.message, 'error'); }
    finally { setTimeout(function () { running = false; }, 1000); }
  }
  $('run-btn').onclick = runCode;

  ed = makeEditor($('editor-container'));
  ed.onChange(function () {
    var f = byId(state.active);
    if (!f) return;
    f.content = ed.get();
    f.dirty = true;
    scheduleSave(f);
  });

  (async function init() {
    try {
      var d = await api('/api/projects/' + PID);
      state.project = d.project;
      state.files = d.files.map(function (f) { return { id: f.id, name: f.name, content: f.content || '', version: f.version, dirty: false, timer: null }; });
      $('room-code').textContent = '#' + d.project.room_code;
      document.title = d.project.name + ' — CodeSync';
      renderTree(); pickFirst(); updatePrompt();
      term('Terminal tayyor. "help" yozing yoki ▶ Run bosing.', 'sys');
      $('loading').classList.add('hidden');
      await sync();
      setInterval(sync, 1500);
    } catch (e) {
      if (e.status === 404) { location.href = '/dashboard'; return; }
      $('loading').textContent = 'Xato: ' + e.message;
    }
  })();
})();
'''

ICONS_JS = r'''
(function () {
  function svg(paths, evenodd) {
    var b = paths.map(function (d) { return '<path' + (evenodd ? ' fill-rule="evenodd"' : '') + ' d="' + d + '"/>'; }).join('');
    return '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16">' + b + '</svg>';
  }
  var P = {
    folder: svg(['M.54 3.87.5 3a2 2 0 0 1 2-2h3.672a2 2 0 0 1 1.414.586l.828.828A2 2 0 0 0 9.828 3h3.982a2 2 0 0 1 1.992 2.181l-.637 7A2 2 0 0 1 13.174 14H2.826a2 2 0 0 1-1.991-1.819l-.637-7a2 2 0 0 1 .342-1.31zM2.19 4a1 1 0 0 0-.996 1.09l.637 7a1 1 0 0 0 .995.91h10.348a1 1 0 0 0 .995-.91l.637-7A1 1 0 0 0 13.81 4zm4.69-1.707A1 1 0 0 0 6.172 2H2.5a1 1 0 0 0-1 .981l.006.139q.323-.119.684-.12h5.396z']),
    code: svg(['M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5zm-3 0A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V4.5z', 'M8.646 6.646a.5.5 0 0 1 .708 0l2 2a.5.5 0 0 1 0 .708l-2 2a.5.5 0 0 1-.708-.708L10.293 9 8.646 7.354a.5.5 0 0 1 0-.708m-1.292 0a.5.5 0 0 0-.708 0l-2 2a.5.5 0 0 0 0 .708l2 2a.5.5 0 0 0 .708-.708L5.707 9l1.647-1.646a.5.5 0 0 0 0-.708']),
    easel: svg(['M8.5 6a.5.5 0 1 0-1 0h-2A1.5 1.5 0 0 0 4 7.5v2A1.5 1.5 0 0 0 5.5 11h.473l-.447 1.342a.5.5 0 1 0 .948.316L7.027 11H7.5v1a.5.5 0 0 0 1 0v-1h.473l.553 1.658a.5.5 0 1 0 .948-.316L10.027 11h.473A1.5 1.5 0 0 0 12 9.5v-2A1.5 1.5 0 0 0 10.5 6zM5 7.5a.5.5 0 0 1 .5-.5h5a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-.5.5h-5a.5.5 0 0 1-.5-.5z', 'M14 14V4.5L9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2M9.5 3A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h5.5z']),
    css: svg(['M14 4.5V14a2 2 0 0 1-2 2h-1v-1h1a1 1 0 0 0 1-1V4.5h-2A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v9H2V2a2 2 0 0 1 2-2h5.5zM3.397 14.841a1.13 1.13 0 0 0 .401.823q.195.162.478.252.284.091.665.091.507 0 .859-.158.354-.158.539-.44.187-.284.187-.656 0-.336-.134-.56a1 1 0 0 0-.375-.357 2 2 0 0 0-.566-.21l-.621-.144a1 1 0 0 1-.404-.176.37.37 0 0 1-.144-.299q0-.234.185-.384.188-.152.512-.152.214 0 .37.068a.6.6 0 0 1 .246.181.56.56 0 0 1 .12.258h.75a1.1 1.1 0 0 0-.2-.566 1.2 1.2 0 0 0-.5-.41 1.8 1.8 0 0 0-.78-.152q-.439 0-.776.15-.337.149-.527.421-.19.273-.19.639 0 .302.122.524.124.223.352.367.228.143.539.213l.618.144q.31.073.463.193a.39.39 0 0 1 .152.326.5.5 0 0 1-.085.29.56.56 0 0 1-.255.193q-.167.07-.413.07-.175 0-.32-.04a.8.8 0 0 1-.248-.115.58.58 0 0 1-.255-.384zM.806 13.693q0-.373.102-.633a.87.87 0 0 1 .302-.399.8.8 0 0 1 .475-.137q.225 0 .398.097a.7.7 0 0 1 .272.26.85.85 0 0 1 .12.381h.765v-.072a1.33 1.33 0 0 0-.466-.964 1.4 1.4 0 0 0-.489-.272 1.8 1.8 0 0 0-.606-.097q-.534 0-.911.223-.375.222-.572.632-.195.41-.196.979v.498q0 .568.193.976.197.407.572.626.375.217.914.217.439 0 .785-.164t.55-.454a1.27 1.27 0 0 0 .226-.674v-.076h-.764a.8.8 0 0 1-.118.363.7.7 0 0 1-.272.25.9.9 0 0 1-.401.087.85.85 0 0 1-.478-.132.83.83 0 0 1-.299-.392 1.7 1.7 0 0 1-.102-.627zM6.78 15.29a1.2 1.2 0 0 1-.111-.449h.764a.58.58 0 0 0 .255.384q.106.073.25.114.142.041.319.041.245 0 .413-.07a.56.56 0 0 0 .255-.193.5.5 0 0 0 .085-.29.39.39 0 0 0-.153-.326q-.152-.12-.463-.193l-.618-.143a1.7 1.7 0 0 1-.539-.214 1 1 0 0 1-.351-.367 1.1 1.1 0 0 1-.123-.524q0-.366.19-.639.19-.272.527-.422t.777-.149q.456 0 .779.152.326.153.5.41.18.255.2.566h-.75a.56.56 0 0 0-.12-.258.6.6 0 0 0-.246-.181.9.9 0 0 0-.37-.068q-.324 0-.512.152a.47.47 0 0 0-.184.384q0 .18.143.3a1 1 0 0 0 .404.175l.621.143q.326.075.566.211t.375.358.135.56q0 .37-.188.656a1.2 1.2 0 0 1-.539.439q-.351.158-.858.158-.381 0-.665-.09a1.4 1.4 0 0 1-.478-.252 1.1 1.1 0 0 1-.29-.375'], true),
    scss: svg(['M14 0a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2V0zM4.59 7.498q-.908 0-1.455.508-.547.507-.547 1.484v3.106q0 .986.527 1.484t1.406.498q.576 0 1.016-.224.45-.225.703-.674.255-.45.254-1.114v-.185h-1.22v.176q0 .449-.186.683t-.527.235q-.372-.01-.557-.264-.186-.255-.186-.752V9.686q0-.547.166-.811.177-.264.577-.264.321 0 .517.225.195.224.195.693v.205h1.23V9.52q0-.674-.243-1.124a1.55 1.55 0 0 0-.664-.673q-.42-.225-1.006-.225m4.214-.01q-.586 0-1.006.244a1.67 1.67 0 0 0-.635.674 2.1 2.1 0 0 0-.225.996q0 .753.293 1.182.304.42.967.732l.469.215q.44.186.625.43.186.244.186.635 0 .478-.166.703-.157.224-.528.224-.36 0-.547-.244-.185-.243-.205-.752H6.87q.02.996.498 1.524.479.527 1.387.527t1.416-.518.508-1.484q0-.81-.332-1.289-.333-.479-1.045-.79l-.45-.196q-.39-.166-.556-.381-.165-.214-.166-.576 0-.4.166-.596.175-.195.508-.195.36 0 .508.234.156.234.175.703h1.123q-.03-.976-.498-1.484-.468-.518-1.308-.518m4.057 0q-.585 0-1.006.244a1.67 1.67 0 0 0-.634.674 2.1 2.1 0 0 0-.225.996q0 .753.293 1.182.303.42.967.732l.469.215q.438.186.625.43.185.244.185.635 0 .478-.166.703-.156.224-.527.224-.361.001-.547-.244-.186-.243-.205-.752h-1.162q.02.996.498 1.524.479.527 1.386.527.909 0 1.417-.518.507-.517.507-1.484 0-.81-.332-1.289t-1.045-.79l-.449-.196q-.39-.166-.556-.381-.166-.214-.166-.576 0-.4.165-.596.177-.195.508-.195.361 0 .508.234.156.234.176.703h1.123q-.03-.976-.498-1.484-.47-.518-1.309-.518'], true),
    txt: svg(['M14 4.5V14a2 2 0 0 1-2 2h-2v-1h2a1 1 0 0 0 1-1V4.5h-2A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v9H2V2a2 2 0 0 1 2-2h5.5zM1.928 15.849v-3.337h1.136v-.662H0v.662h1.134v3.337zm4.689-3.999h-.894L4.9 13.289h-.035l-.832-1.439h-.932l1.228 1.983-1.24 2.016h.862l.853-1.415h.035l.85 1.415h.907l-1.253-1.992zm1.93.662v3.337h-.794v-3.337H6.619v-.662h3.064v.662H8.546Z'], true),
    js: svg(['M14 4.5V14a2 2 0 0 1-2 2H8v-1h4a1 1 0 0 0 1-1V4.5h-2A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v9H2V2a2 2 0 0 1 2-2h5.5zM3.186 15.29a1.2 1.2 0 0 1-.111-.449h.765a.58.58 0 0 0 .255.384q.105.073.249.114.143.041.319.041.246 0 .413-.07a.56.56 0 0 0 .255-.193.5.5 0 0 0 .085-.29.39.39 0 0 0-.153-.326q-.151-.12-.462-.193l-.619-.143a1.7 1.7 0 0 1-.539-.214 1 1 0 0 1-.351-.367 1.1 1.1 0 0 1-.123-.524q0-.366.19-.639.19-.272.528-.422.336-.15.776-.149.457 0 .78.152.324.153.5.41.18.255.2.566h-.75a.56.56 0 0 0-.12-.258.6.6 0 0 0-.247-.181.9.9 0 0 0-.369-.068q-.325 0-.513.152a.47.47 0 0 0-.184.384q0 .18.143.3a1 1 0 0 0 .405.175l.62.143q.327.075.566.211.24.136.375.358t.135.56q0 .37-.188.656a1.2 1.2 0 0 1-.539.439q-.351.158-.858.158-.381 0-.665-.09a1.4 1.4 0 0 1-.478-.252 1.1 1.1 0 0 1-.29-.375m-3.104-.033A1.3 1.3 0 0 1 0 14.791h.765a.6.6 0 0 0 .073.27.5.5 0 0 0 .454.246q.285 0 .422-.164.138-.165.138-.466v-2.745h.79v2.725q0 .66-.357 1.005-.354.345-.984.345a1.6 1.6 0 0 1-.569-.094 1.15 1.15 0 0 1-.407-.266 1.1 1.1 0 0 1-.243-.39'], true),
    py: svg(['M14 4.5V14a2 2 0 0 1-2 2H7v-1h5a1 1 0 0 0 1-1V4.5h-2A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v9H2V2a2 2 0 0 1 2-2h5.5zM0 11.85h1.6q.434 0 .732.179.302.175.46.477t.158.677-.16.677q-.158.299-.464.474a1.45 1.45 0 0 1-.732.173H.79v1.342H0zm2.06 1.714a.8.8 0 0 0 .085-.381q0-.34-.185-.521-.185-.182-.513-.182H.788v1.406h.66a.8.8 0 0 0 .374-.082.57.57 0 0 0 .238-.24m2.963.75v1.535H4.23v-1.52L2.89 11.85h.876l.853 1.696h.032l.856-1.696h.855z'], true),
    html: svg(['M14 4.5V11h-1V4.5h-2A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v9H2V2a2 2 0 0 1 2-2h5.5zm-9.736 7.35v3.999h-.791v-1.714H1.79v1.714H1V11.85h.791v1.626h1.682V11.85h.79Zm2.251.662v3.337h-.794v-3.337H4.588v-.662h3.064v.662zm2.176 3.337v-2.66h.038l.952 2.159h.516l.946-2.16h.038v2.661h.715V11.85h-.8l-1.14 2.596H9.93L8.79 11.85h-.805v3.999zm4.71-.674h1.696v.674H12.61V11.85h.79v3.325Z'], true),
    file: svg(['M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5zm-3 0A1.5 1.5 0 0 1 9.5 3V1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V4.5z'])
  };
  var MAP = {
    py: 'py', js: 'js', mjs: 'js', cjs: 'js', html: 'html', htm: 'html', css: 'css',
    scss: 'scss', sass: 'scss', less: 'scss',
    txt: 'txt', md: 'txt', log: 'txt', csv: 'txt',
    json: 'code', xml: 'code', yaml: 'code', yml: 'code', java: 'code', c: 'code', cpp: 'code', h: 'code', go: 'code',
    rs: 'code', php: 'code', ts: 'code', tsx: 'code', jsx: 'code', sh: 'code', sql: 'code', vue: 'code', rb: 'code',
    ppt: 'easel', pptx: 'easel', key: 'easel'
  };
  window.csIconKind = function (name, isFolder) {
    if (isFolder) return 'folder';
    var i = (name || '').lastIndexOf('.');
    var e = i < 0 ? '' : name.slice(i + 1).toLowerCase();
    return MAP[e] || 'file';
  };
  window.csIcon = function (name, isFolder) {
    var k = window.csIconKind(name, isFolder);
    return '<span class="ic ic-' + k + '">' + P[k] + '</span>';
  };
})();
'''

MESSAGES_JS = r'''
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var csIcon = window.csIcon || function () { return '📄 '; };
  var root = $('chat-root');
  var PID = root.dataset.project;
  var UID = parseInt(root.dataset.uid, 10);
  var seen = {}, since = 0, replyTo = null, pendingFile = null;
  var sending = false, polling = false, isOwner = false, first = true;
  var MAX_MB = 20;

  async function api(url, method, body) {
    var r = await fetch(url, {
      method: method || 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin'
    });
    if (r.status === 401) { location.href = '/login'; throw new Error('Kirish kerak'); }
    var data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) { var er = new Error(data.error || ('Xato ' + r.status)); er.status = r.status; throw er; }
    return data;
  }
  function fmtSize(n) {
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }
  function fmtTime(t) {
    try { return new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; }
  }
  function mk(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ---------- katta ko'rish (lightbox) ----------
  function openBox(kind, url) {
    var box = $('lightbox'); box.innerHTML = '';
    var el;
    if (kind === 'video') { el = document.createElement('video'); el.controls = true; el.autoplay = true; }
    else { el = document.createElement('img'); }
    el.src = url; el.className = 'lb-media';
    el.onclick = function (e) { e.stopPropagation(); };
    var x = mk('button', 'lb-x', '✕');
    x.onclick = closeBox;
    box.appendChild(el); box.appendChild(x);
    box.classList.remove('hidden');
  }
  function closeBox() { var b = $('lightbox'); b.classList.add('hidden'); b.innerHTML = ''; }
  $('lightbox').onclick = closeBox;
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeBox(); });

  // ---------- xabarlar ----------
  function attachmentEl(a) {
    var url = '/api/attachments/' + a.id;
    var wrap = mk('div', 'att-wrap');
    if (a.kind === 'image') {
      var im = document.createElement('img'); im.className = 'att'; im.src = url; im.loading = 'lazy'; im.alt = a.name;
      im.onclick = function () { openBox('image', url); };
      wrap.appendChild(im);
    } else if (a.kind === 'video') {
      var v = document.createElement('video'); v.className = 'att'; v.src = url; v.controls = true; v.preload = 'metadata';
      wrap.appendChild(v);
      var big = mk('a', 'lnk', '⤢ Katta ko\'rish'); big.href = '#'; big.style.fontSize = '12px';
      big.onclick = function (e) { e.preventDefault(); openBox('video', url); };
      wrap.appendChild(big);
    } else if (a.kind === 'audio') {
      var au = document.createElement('audio'); au.src = url; au.controls = true; au.preload = 'metadata'; au.style.maxWidth = '100%';
      wrap.appendChild(au);
    } else {
      var card = mk('div', 'fcard');
      var ic = mk('span'); ic.innerHTML = csIcon(a.name);
      var info = mk('div'); info.style.minWidth = '0';
      var nm = mk('a', 'lnk', a.name); nm.href = url + '?dl=1'; nm.style.wordBreak = 'break-all';
      var sz = mk('div', 'muted', fmtSize(a.size));
      info.appendChild(nm); info.appendChild(sz);
      card.appendChild(ic); card.appendChild(info);
      wrap.appendChild(card);
    }
    return wrap;
  }
  function renderMsg(m) {
    var el = mk('div', 'm' + (m.user_id === UID ? ' me' : ''));
    el.id = 'm' + m.id;
    el.appendChild(mk('div', 'who', m.username));
    if (m.reply) {
      var rp = mk('div', 'rep', '↩ ' + m.reply.username + ': ' + (m.reply.text || (m.reply.att ? '📎 fayl' : '')));
      rp.onclick = function () {
        var t = $('m' + m.reply.id);
        if (t) { t.scrollIntoView({ block: 'center', behavior: 'smooth' }); t.classList.add('flash'); setTimeout(function () { t.classList.remove('flash'); }, 1200); }
      };
      el.appendChild(rp);
    }
    if (m.text) el.appendChild(mk('div', 'txt', m.text));
    if (m.attachment) el.appendChild(attachmentEl(m.attachment));
    var meta = mk('div', 'meta');
    meta.appendChild(mk('span', '', fmtTime(m.created)));
    var rb = mk('a', 'lnk', '↩ Javob'); rb.href = '#';
    rb.onclick = function (e) { e.preventDefault(); setReply(m); };
    meta.appendChild(rb);
    el.appendChild(meta);
    return el;
  }
  function setReply(m) {
    replyTo = { id: m.id, username: m.username, text: m.text || (m.attachment ? '📎 ' + m.attachment.name : '') };
    $('rbar-text').textContent = '↩ ' + replyTo.username + ': ' + replyTo.text.slice(0, 80);
    $('rbar').classList.remove('hidden');
    $('msg-input').focus();
  }
  function clearReply() { replyTo = null; $('rbar').classList.add('hidden'); }
  $('rbar-x').onclick = clearReply;

  // ---------- a'zolar ----------
  function renderMembers(ms, banned) {
    var box = $('mem-list'); box.innerHTML = '';
    ms = ms.slice().sort(function (a, b) { return (b.online - a.online) || a.username.localeCompare(b.username); });
    var onl = 0;
    ms.forEach(function (m) {
      if (m.online) onl++;
      var row = mk('div', 'mem');
      row.appendChild(mk('span', 'dot' + (m.online ? ' on' : '')));
      var nm = mk('span', 'nm', m.username + (m.id === UID ? ' (siz)' : ''));
      nm.style.flex = '1'; nm.style.minWidth = '0'; nm.style.overflow = 'hidden'; nm.style.textOverflow = 'ellipsis';
      row.appendChild(nm);
      if (m.owner) row.appendChild(mk('span', '', '👑'));
      if (m.voice) row.appendChild(mk('span', '', '🎤'));
      if (isOwner && !m.owner && m.id !== UID) {
        var k = mk('button', 'btn btn-danger', 'Chiqarish');
        k.style.padding = '2px 8px'; k.style.fontSize = '11px';
        k.onclick = async function () {
          if (!confirm('"' + m.username + '" loyihadan chiqarilsinmi?')) return;
          try { await api('/api/projects/' + PID + '/members/' + m.id + '/kick', 'POST'); poll(); } catch (e) { alert(e.message); }
        };
        row.appendChild(k);
      }
      box.appendChild(row);
    });
    $('mem-count').textContent = onl + ' onlayn / ' + ms.length;
    var bb = $('ban-list'); bb.innerHTML = '';
    if (isOwner && banned.length) {
      bb.appendChild(mk('div', 'muted', 'Chiqarilganlar:'));
      banned.forEach(function (b) {
        var row = mk('div', 'mem');
        var nm = mk('span', 'nm', b.username); nm.style.flex = '1';
        row.appendChild(nm);
        var u = mk('button', 'btn btn-sec', 'Qaytarish');
        u.style.padding = '2px 8px'; u.style.fontSize = '11px';
        u.onclick = async function () {
          try { await api('/api/projects/' + PID + '/members/' + b.id + '/unban', 'POST'); poll(); } catch (e) { alert(e.message); }
        };
        row.appendChild(u);
        bb.appendChild(row);
      });
    }
  }

  async function poll() {
    if (polling) return; polling = true;
    try {
      var r = await api('/api/projects/' + PID + '/messages/poll', 'POST', { since: since });
      isOwner = !!r.is_owner;
      var list = $('msg-list');
      var near = list.scrollHeight - list.scrollTop - list.clientHeight < 140;
      var mine = false;
      r.messages.forEach(function (m) {
        if (seen[m.id]) return;
        seen[m.id] = 1;
        if (m.id > since) since = m.id;
        list.appendChild(renderMsg(m));
        if (m.user_id === UID) mine = true;
      });
      if (r.messages.length && (near || mine || first)) list.scrollTop = list.scrollHeight;
      first = false;
      renderMembers(r.members, r.banned || []);
      $('conn').textContent = '🟢';
    } catch (e) {
      if (e.status === 404) { location.href = '/dashboard'; return; }
      $('conn').textContent = '🔴';
    } finally { polling = false; }
  }

  // ---------- yuborish ----------
  function clearFile() { pendingFile = null; $('file-input').value = ''; $('fchip').classList.add('hidden'); }
  $('file-btn').onclick = function () { $('file-input').click(); };
  $('file-input').onchange = function () {
    var f = this.files && this.files[0];
    if (!f) return;
    if (f.size > MAX_MB * 1048576) { alert('Fayl ' + MAX_MB + ' MB dan katta'); clearFile(); return; }
    pendingFile = f;
    $('fchip-text').textContent = '📎 ' + f.name + ' (' + fmtSize(f.size) + ')';
    $('fchip').classList.remove('hidden');
  };
  $('fchip-x').onclick = clearFile;
  $('send-form').onsubmit = async function (e) {
    e.preventDefault();
    if (sending) return;
    var text = $('msg-input').value.trim();
    if (!text && !pendingFile) return;
    sending = true; $('send-btn').disabled = true;
    var fd = new FormData();
    fd.append('text', text);
    if (replyTo) fd.append('reply_to', replyTo.id);
    if (pendingFile) fd.append('file', pendingFile);
    try {
      var r = await fetch('/api/projects/' + PID + '/messages/send', { method: 'POST', body: fd, credentials: 'same-origin' });
      var d = {};
      try { d = await r.json(); } catch (x) {}
      if (r.status === 401) { location.href = '/login'; return; }
      if (!r.ok) throw new Error(d.error || ('Xato ' + r.status));
      $('msg-input').value = ''; clearReply(); clearFile();
      await poll();
    } catch (err) { alert(err.message); }
    finally { sending = false; $('send-btn').disabled = false; }
  };

  $('mem-toggle').onclick = function () { $('members').classList.toggle('open'); };
  poll();
  setInterval(poll, 1500);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
})();
'''

ADMIN_JS = r'''
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  async function api(url, method, body) {
    var r = await fetch(url, {
      method: method || 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin'
    });
    if (r.status === 401) { location.href = '/admin'; throw new Error('Kirish kerak'); }
    var d = {};
    try { d = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error(d.error || ('Xato ' + r.status));
    return d;
  }
  function fmtUp(s) {
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return (d ? d + 'k ' : '') + h + 's ' + m + 'd';
  }
  function card(box, title, value, sub, cls) {
    var c = mk('div', 'stat' + (cls ? ' ' + cls : ''));
    c.appendChild(mk('div', 'muted', title));
    c.appendChild(mk('div', 'val', value));
    if (sub) c.appendChild(mk('div', 'muted', sub));
    box.appendChild(c);
  }
  var maintInit = false;
  async function loadStats() {
    try {
      var s = await api('/admin/api/stats');
      var box = $('stats'); box.innerHTML = '';
      var st = s.server.status;
      card(box, 'Server holati', st === 'ok' ? '🟢 Normal' : st === 'busy' ? '🟡 Band' : '🔴 Og\'ir yuk', 'Ishlash vaqti: ' + fmtUp(s.server.uptime), st);
      card(box, 'Foydalanuvchilar', String(s.users), 'Bloklangan: ' + s.banned);
      card(box, 'Loyihalar', String(s.projects));
      card(box, 'Onlayn', String(s.online.length), s.online.join(', ') || '—');
      card(box, 'Ovozli chatda', String(s.voice.length), s.voice.join(', ') || '—');
      card(box, 'Ishlayotgan jarayonlar', String(s.server.runs));
      card(box, 'So\'rovlar / daqiqa', String(s.server.rpm), 'Oldingi daqiqa: ' + s.server.rpm_prev);
      card(box, 'Server yuki', s.server.load ? s.server.load.map(function (x) { return x.toFixed(2); }).join(' / ') : '—', 'CPU yadro: ' + s.server.cpus);
      var m = s.server.mem;
      card(box, 'Xotira', m ? m.used_mb + ' / ' + (m.total_mb || '?') + ' MB' : '—', m && m.pct !== null ? m.pct + '% ishlatilmoqda' : '');
      card(box, 'Disk', s.server.disk_free_gb + ' GB bo\'sh', 'Baza: ' + s.server.db_mb + ' MB, yuklamalar: ' + s.server.uploads_mb + ' MB');
      if (!maintInit) {
        maintInit = true;
        $('maint-msg').value = s.maintenance.message || '';
      }
      $('maint-state').textContent = s.maintenance.on ? '🔴 YOQILGAN (sayt foydalanuvchilarga yopiq)' : '🟢 O\'chiq (sayt ishlayapti)';
      $('maint-btn').textContent = s.maintenance.on ? 'Saytni qayta ochish' : 'Texnik ishlar rejimini yoqish';
      $('maint-btn').dataset.on = s.maintenance.on ? '1' : '0';
    } catch (e) {}
  }
  $('maint-btn').onclick = async function () {
    var on = this.dataset.on !== '1';
    if (on && !confirm('Sayt hamma foydalanuvchilar uchun yopiladi (stopped sahifa ko\'rsatiladi). Davom etasizmi?')) return;
    try { await api('/admin/api/maintenance', 'POST', { on: on, message: $('maint-msg').value }); loadStats(); } catch (e) { alert(e.message); }
  };

  var filter = '';
  $('user-filter').oninput = function () { filter = this.value.toLowerCase(); loadUsers(); };
  async function loadUsers() {
    try {
      var d = await api('/admin/api/users');
      var tb = $('users'); tb.innerHTML = '';
      d.users.filter(function (u) { return u.username.toLowerCase().indexOf(filter) >= 0; }).forEach(function (u) {
        var tr = mk('tr');
        tr.appendChild(mk('td', '', String(u.id)));
        tr.appendChild(mk('td', '', (u.online ? '🟢 ' : '⚪ ') + u.username + (u.banned ? ' 🚫' : '')));
        tr.appendChild(mk('td', '', u.owned + ' / ' + u.joined));
        tr.appendChild(mk('td', '', u.last_seen ? new Date(u.last_seen * 1000).toLocaleString() : '—'));
        var act = mk('td');
        var b1 = mk('button', 'btn ' + (u.banned ? 'btn-sec' : 'btn-danger'), u.banned ? 'Blokdan chiqarish' : 'Bloklash');
        b1.onclick = async function () {
          if (!u.banned && !confirm('"' + u.username + '" bloklansinmi?')) return;
          try { await api('/admin/api/users/' + u.id + '/ban', 'POST', { banned: !u.banned }); loadUsers(); loadStats(); } catch (e) { alert(e.message); }
        };
        var b2 = mk('button', 'btn btn-danger', 'O\'chirish');
        b2.style.marginLeft = '6px';
        b2.onclick = async function () {
          if (!confirm('"' + u.username + '" va uning loyihalari BUTUNLAY o\'chirilsinmi?')) return;
          try { await api('/admin/api/users/' + u.id + '/delete', 'POST'); loadUsers(); loadStats(); } catch (e) { alert(e.message); }
        };
        act.appendChild(b1); act.appendChild(b2);
        tr.appendChild(act);
        tb.appendChild(tr);
      });
    } catch (e) {}
  }
  loadStats(); loadUsers();
  setInterval(loadStats, 3000);
  setInterval(loadUsers, 8000);
})();
'''

PROJECT_TEMPLATES = {
    'web': {'name': 'Web sayt', 'icon': '🌐', 'lang': 'html', 'files': [
        ('index.html', '<!DOCTYPE html>\n<html>\n<head>\n  <meta charset="UTF-8">\n  <link rel="stylesheet" href="style.css">\n</head>\n<body>\n  <h1>Salom, CodeSync! 🚀</h1>\n  <button onclick="salomla()">Bos meni</button>\n  <script src="script.js"></script>\n</body>\n</html>'),
        ('style.css', 'body {\n  font-family: system-ui, sans-serif;\n  background: #1e1e1e;\n  color: white;\n  text-align: center;\n  padding: 50px;\n}'),
        ('script.js', 'function salomla() {\n  console.log("Salom! 👋");\n  document.querySelector("h1").textContent = "Ishladi! ✅";\n}')]},
    'python': {'name': 'Python', 'icon': '🐍', 'lang': 'python', 'files': [
        ('main.py', 'import time\n\ndef salom(ism):\n    return f"Salom, {ism}!"\n\nfor i in range(3):\n    print(salom("Dasturchi"), i)\n    time.sleep(1)\n')]},
    'flask': {'name': 'Flask App', 'icon': '🧪', 'lang': 'python', 'files': [
        ('app.py', 'import os\nfrom flask import Flask\n\napp = Flask(__name__)\n\n@app.route("/")\ndef home():\n    return "<h1>Salom, Flask ishlayapti!</h1>"\n\nif __name__ == "__main__":\n    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5001)))\n'),
        ('requirements.txt', 'flask\n')]},
}

# ============================================================
# BAZA
# ============================================================
def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def query(sql, args=(), one=False):
    c = db()
    try:
        cur = c.execute(sql, args)
        res = cur.fetchone() if one else cur.fetchall()
        return res
    finally:
        c.close()

@contextmanager
def tx():
    c = db()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init_db():
    c = db()
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, owner_id INTEGER NOT NULL, room_code TEXT UNIQUE NOT NULL, language TEXT DEFAULT 'python', project_type TEXT DEFAULT 'web', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, name TEXT NOT NULL, content TEXT DEFAULT '', version INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS members (project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, last_seen REAL DEFAULT 0, PRIMARY KEY (project_id, user_id));
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT NOT NULL, text TEXT NOT NULL, created REAL);
        CREATE TABLE IF NOT EXISTS voice_members (project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT NOT NULL, last_seen REAL DEFAULT 0, PRIMARY KEY (project_id, user_id));
        CREATE TABLE IF NOT EXISTS voice_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, from_user INTEGER NOT NULL, to_user INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created REAL);
        CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, message_id INTEGER NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, stored TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_bans (project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (project_id, user_id));
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
        CREATE INDEX IF NOT EXISTS idx_msg_project ON messages(project_id, id);
        CREATE INDEX IF NOT EXISTS idx_vsig_project ON voice_signals(project_id, to_user, id);
    ''')
    for alter in ('ALTER TABLE files ADD COLUMN version INTEGER DEFAULT 1',
                  'ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0',
                  'ALTER TABLE messages ADD COLUMN reply_to INTEGER'):
        try:
            c.execute(alter)
        except sqlite3.OperationalError:
            pass
    c.execute('UPDATE files SET version = 1 WHERE version IS NULL')
    c.commit()
    c.close()

def get_secret():
    p = os.path.join(BASE, '.secret_key')
    try:
        if os.path.exists(p):
            with open(p) as f: return f.read().strip()
        k = secrets.token_hex(32)
        with open(p, 'w') as f: f.write(k)
        return k
    except OSError:
        return secrets.token_hex(32)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or get_secret()
app.config.update(SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_HTTPONLY=True, MAX_CONTENT_LENGTH=25 * 1024 * 1024)
app.jinja_env.loader = DictLoader(TEMPLATES)
init_db()

login_manager = LoginManager(app)

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(uid):
    u = query('SELECT id, username, banned FROM users WHERE id=?', (uid,), one=True)
    return User(u['id'], u['username']) if u and not u['banned'] else None

@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith('/api/'):
        return jsonify(error='Kirish kerak'), 401
    return redirect(url_for('login'))

USER_RE = re.compile(r'^[A-Za-z0-9_.\-]{3,30}$')
SEG_RE = re.compile(r'^[\w\-. ]+$')

def valid_filename(n):
    if not n or len(n) > 100 or n.startswith('/') or n.endswith('/'):
        return False
    for seg in n.split('/'):
        if seg in ('', '.', '..') or not SEG_RE.match(seg):
            return False
    return True

def verify_password(user, pw):
    h = user['password_hash']
    if h.startswith(('pbkdf2:', 'scrypt:')):
        return check_password_hash(h, pw)
    if secrets.compare_digest(hashlib.sha256(pw.encode()).hexdigest(), h):
        with tx() as c:
            c.execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(pw), user['id']))
        return True
    return False

def get_project(pid):
    uid = current_user.id
    return query(
        'SELECT * FROM projects WHERE id=? AND (owner_id=? OR id IN (SELECT project_id FROM members WHERE user_id=?))',
        (pid, uid, uid), one=True)

def file_access(fid):
    f = query('SELECT * FROM files WHERE id=?', (fid,), one=True)
    if not f:
        return None, None
    p = get_project(f['project_id'])
    return (f, p) if p else (None, None)

def asset(text, mime):
    r = Response(text, mimetype=mime)
    r.headers['Cache-Control'] = 'no-cache'
    return r

# ============================================================
# ROUTES
# ============================================================
@app.route('/')
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
        if INVITE_CODE and not secrets.compare_digest((request.form.get('invite') or '').strip(), INVITE_CODE):
            error = "Taklif kodi noto'g'ri"
        elif not USER_RE.match(u):
            error = "Login 3-30 ta belgi bo'lsin"
        elif len(p) < 4:
            error = "Parol kamida 4 ta belgi bo'lsin"
        else:
            try:
                with tx() as c:
                    uid = c.execute('INSERT INTO users (username, password_hash) VALUES (?,?)',
                                    (u, generate_password_hash(p))).lastrowid
                login_user(User(uid, u), remember=True)
                return redirect(url_for('dashboard'))
            except sqlite3.IntegrityError:
                error = 'Bu login band'
    return render_template('auth.html', mode='register', error=error, invite_required=bool(INVITE_CODE))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
        row = query('SELECT * FROM users WHERE username=?', (u,), one=True)
        if row and verify_password(row, p):
            if row['banned']:
                error = "Akkaunt bloklangan. Administrator bilan bog'laning"
            else:
                login_user(User(row['id'], row['username']), remember=True)
                return redirect(url_for('dashboard'))
        else:
            error = "Login yoki parol xato"
    return render_template('auth.html', mode='login', error=error)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('landing'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', username=current_user.username, templates=PROJECT_TEMPLATES)

@app.route('/project/<int:pid>')
@login_required
def project_page(pid):
    if not get_project(pid):
        return redirect(url_for('dashboard'))
    return render_template('editor.html', project_id=pid, username=current_user.username, uid=current_user.id)

@app.route('/project/<int:pid>/terminal')
@login_required
def terminal_page(pid):
    p = get_project(pid)
    if not p:
        return redirect(url_for('dashboard'))
    return render_template('terminal.html', project_id=pid, project_name=p['name'])

@app.route('/project/<int:pid>/preview')
@login_required
def preview_page(pid):
    p = get_project(pid)
    if not p:
        return redirect(url_for('dashboard'))
    return render_template('preview.html', project_id=pid, project_name=p['name'],
                           port=proj_port(pid), token=make_proxy_token(pid, current_user.id))

@app.route('/app.css')
def css():
    return asset(APP_CSS, 'text/css')

@app.route('/dashboard.js')
def dash_js():
    return asset(DASH_JS, 'application/javascript')

@app.route('/editor.js')
def editor_js():
    return asset(EDITOR_JS, 'application/javascript')

# ============================================================
# PWA MARSHRUTLARI (Manifest, Service Worker, Icon)
# ============================================================
@app.route('/manifest.json')
def manifest():
    data = {
        "name": "CodeSync IDE",
        "short_name": "CodeSync",
        "description": "Brauzerdagi jamoaviy kod muharriri",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#1e1e1e",
        "theme_color": "#0e7fd0",
        "icons": [
            {
                "src": "/icon.png",
                "sizes": "192x192",
                "type": "image/png"
            },
            {
                "src": "/icon.png",
                "sizes": "512x512",
                "type": "image/png"
            }
        ]
    }
    return jsonify(data)

@app.route('/service-worker.js')
def service_worker():
    js = """
    self.addEventListener('install', (e) => {
      self.skipWaiting();
    });
    self.addEventListener('fetch', (e) => {
      // Tarmoq so'rovlarini o'zgartirmasdan o'tkazib yuborish
      e.respondWith(fetch(e.request).catch(() => new Response("Oflayn rejimda ishlamaydi")));
    });
    """
    return Response(js, mimetype='application/javascript')

@app.route('/icon.png')
def serve_icon():
    # Bu fayl app.py bilan bir papkada turishi shart
    icon_path = os.path.join(BASE, 'icon.png')
    if not os.path.exists(icon_path):
        # Agar rasm bo'lmasa, oddiy SVG qaytaramiz (o'rnatish tugmasi chiqishi uchun)
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">⚡</text></svg>'''
        return Response(svg, mimetype='image/svg+xml')
    return send_from_directory(BASE, 'icon.png')

# ============================================================
# API: LOYIHALAR
# ============================================================
@app.route('/api/projects', methods=['GET', 'POST'])
@login_required
def api_projects():
    uid = current_user.id
    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        name = (d.get('name') or '').strip()[:60] or 'Loyiha'
        ptype = d.get('project_type') if d.get('project_type') in PROJECT_TEMPLATES else 'web'
        tpl = PROJECT_TEMPLATES[ptype]
        room = secrets.token_urlsafe(6)
        with tx() as c:
            pid = c.execute('INSERT INTO projects (name, owner_id, room_code, language, project_type) VALUES (?,?,?,?,?)',
                            (name, uid, room, tpl['lang'], ptype)).lastrowid
            c.execute('INSERT OR IGNORE INTO members (project_id, user_id, last_seen) VALUES (?,?,0)', (pid, uid))
            for fname, content in tpl['files']:
                c.execute('INSERT INTO files (project_id, name, content, version) VALUES (?,?,?,1)', (pid, fname, content))
        return jsonify(id=pid)
    rows = query(
        'SELECT id, name, room_code, project_type, (owner_id=?) AS is_owner FROM projects '
        'WHERE owner_id=? OR id IN (SELECT project_id FROM members WHERE user_id=?) ORDER BY id DESC',
        (uid, uid, uid))
    return jsonify([dict(r) for r in rows])

@app.route('/api/join', methods=['POST'])
@login_required
def api_join():
    d = request.get_json(silent=True) or {}
    code = (d.get('code') or '').strip().lstrip('#')
    p = query('SELECT id FROM projects WHERE room_code=?', (code,), one=True) if code else None
    if not p:
        return jsonify(error='Bunday kod topilmadi'), 404
    if query('SELECT 1 FROM project_bans WHERE project_id=? AND user_id=?', (p['id'], current_user.id), one=True):
        return jsonify(error='Siz bu loyihadan chiqarilgansiz'), 403
    with tx() as c:
        c.execute('INSERT OR IGNORE INTO members (project_id, user_id, last_seen) VALUES (?,?,0)', (p['id'], current_user.id))
    return jsonify(id=p['id'])

@app.route('/api/projects/<int:pid>', methods=['GET', 'DELETE'])
@login_required
def api_project(pid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    if request.method == 'DELETE':
        if p['owner_id'] != current_user.id:
            return jsonify(error="Faqat egasi o'chira oladi"), 403
        _delete_project(pid)
        return jsonify(ok=True)
    files = query('SELECT id, name, content, version FROM files WHERE project_id=?', (pid,))
    return jsonify(
        project={'id': p['id'], 'name': p['name'], 'room_code': p['room_code'], 'project_type': p['project_type']},
        files=[dict(f) for f in files],
        is_owner=p['owner_id'] == current_user.id)

@app.route('/api/projects/<int:pid>/files', methods=['POST'])
@login_required
def api_create_file(pid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '').strip()
    if not valid_filename(name):
        return jsonify(error="Fayl nomi noto'g'ri"), 400
    if query('SELECT id FROM files WHERE project_id=? AND name=?', (pid, name), one=True):
        return jsonify(error='Bu nom band'), 400
    if query('SELECT COUNT(*) AS n FROM files WHERE project_id=?', (pid,), one=True)['n'] >= 200:
        return jsonify(error='Fayllar soni chegaradan oshdi'), 400
    with tx() as c:
        fid = c.execute('INSERT INTO files (project_id, name, content, version) VALUES (?,?,?,1)', (pid, name, '')).lastrowid
    return jsonify(id=fid, name=name, version=1)

@app.route('/api/files/<int:fid>', methods=['PUT', 'DELETE'])
@login_required
def api_file(fid):
    f, p = file_access(fid)
    if not f:
        return jsonify(error='Topilmadi'), 404
    if request.method == 'DELETE':
        with tx() as c:
            c.execute('DELETE FROM files WHERE id=?', (fid,))
        return jsonify(ok=True)
    content = (request.get_json(silent=True) or {}).get('content', '')
    if not isinstance(content, str) or len(content) > 500000:
        return jsonify(error='Fayl juda katta'), 400
    with tx() as c:
        c.execute('UPDATE files SET content=?, version=version+1 WHERE id=?', (content, fid))
        v = c.execute('SELECT version FROM files WHERE id=?', (fid,)).fetchone()['version']
    return jsonify(ok=True, version=v)

@app.route('/api/projects/<int:pid>/sync', methods=['POST'])
@login_required
def api_sync(pid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    d = request.get_json(silent=True) or {}
    known = d.get('versions') or {}
    try:
        since = int(d.get('chat_since') or 0)
    except (TypeError, ValueError):
        since = 0
    now = time.time()
    uid = current_user.id
    with tx() as c:
        c.execute('INSERT OR IGNORE INTO members (project_id, user_id, last_seen) VALUES (?,?,?)', (pid, uid, now))
        c.execute('UPDATE members SET last_seen=? WHERE project_id=? AND user_id=?', (now, pid, uid))
    files = []
    for r in query('SELECT id, name, version, content FROM files WHERE project_id=?', (pid,)):
        item = {'id': r['id'], 'name': r['name'], 'version': r['version']}
        if known.get(str(r['id'])) != r['version']:
            item['content'] = r['content'] or ''
        files.append(item)
    online = query('SELECT u.username FROM members m JOIN users u ON u.id=m.user_id WHERE m.project_id=? AND m.last_seen>?',
                   (pid, now - 8))
    return jsonify(files=files, chat=[], online=[r['username'] for r in online])

@app.route('/api/projects/<int:pid>/chat', methods=['POST'])
@login_required
def api_chat(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    text = ((request.get_json(silent=True) or {}).get('text') or '').strip()[:1000]
    if not text:
        return jsonify(error="Bo'sh xabar"), 400
    with tx() as c:
        mid = c.execute('INSERT INTO messages (project_id, user_id, username, text, created) VALUES (?,?,?,?,?)',
                        (pid, current_user.id, current_user.username, text, time.time())).lastrowid
    return jsonify(id=mid, username=current_user.username, text=text)

# ============================================================
# API: ZIP DOWNLOAD
# ============================================================
@app.route('/api/projects/<int:pid>/download')
@login_required
def api_download_project(pid):
    p = get_project(pid)
    if not p:
        return 'Topilmadi', 404

    files = query('SELECT name, content FROM files WHERE project_id=?', (pid,))
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f['name'], f['content'] or '')
    
    memory_file.seek(0)
    
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', p['name'])
    return Response(
        memory_file.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename={safe_name}.zip'}
    )

# ============================================================
# API: PROXY (Real Server Preview) — xavfsiz, token orqali
# ============================================================
from itsdangerous import URLSafeTimedSerializer

PORT_MIN, PORT_MAX = 5001, 5999          # foydalanuvchi serverlari shu oraliqda ishlaydi
MAIN_PORT = int(os.environ.get('PORT', 5000))
MAX_LINES = 3000                          # terminal chiqishi chegarasi
MAX_RUN_SECONDS = 3600                    # jarayon eng ko'pi bilan 1 soat ishlaydi

def proj_port(pid):
    return 5100 + (pid % 800)             # har loyihaga o'z porti (5100-5899)

def make_proxy_token(pid, uid):
    return URLSafeTimedSerializer(app.secret_key, salt='proxy').dumps({'p': pid, 'u': uid})

_ATTR_RE = re.compile(r'''(\b(?:href|src|action)\s*=\s*)(["'])/(?!/)''', re.I)

def _rewrite_html(html, base):
    html = _ATTR_RE.sub(lambda m: m.group(1) + m.group(2) + base, html)
    shim = ('<base href="%s"><script>(function(){var B="%s";function f(u){return typeof u==="string"&&u.charAt(0)==="/"&&u.charAt(1)!=="/"?B+u.slice(1):u}'
            'var F=window.fetch;if(F)window.fetch=function(u,o){return F.call(this,f(u),o)};'
            'var O=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=f(u);return O.apply(this,arguments)}})();</script>') % (base, base)
    if re.search(r'<head[^>]*>', html, re.I):
        return re.sub(r'<head[^>]*>', lambda m: m.group(0) + shim, html, count=1, flags=re.I)
    return shim + html

PROXY_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']

@app.route('/px/<token>/<int:port>/', defaults={'path': ''}, methods=PROXY_METHODS)
@app.route('/px/<token>/<int:port>/<path:path>', methods=PROXY_METHODS)
def px(token, port, path):
    try:
        data = URLSafeTimedSerializer(app.secret_key, salt='proxy').loads(token, max_age=43200)
        pid, uid = int(data['p']), int(data['u'])
    except Exception:
        return 'Havola eskirgan. Preview sahifasini qaytadan oching.', 403
    ok = query('SELECT 1 FROM projects WHERE id=? AND (owner_id=? OR id IN (SELECT project_id FROM members WHERE user_id=?))',
               (pid, uid, uid), one=True)
    if not ok:
        return 'Ruxsat yo\'q', 403
    if not (PORT_MIN <= port <= PORT_MAX) or port == MAIN_PORT:
        return 'Port ruxsat etilmagan (faqat %d-%d)' % (PORT_MIN, PORT_MAX), 400

    target = 'http://127.0.0.1:%d/%s' % (port, path)
    if request.query_string:
        target += '?' + request.query_string.decode('utf-8', 'replace')
    base = '/px/%s/%d/' % (token, port)
    # Cookie/Authorization uzatilmaydi: jamoadosh kodi sizning sessiyangizni ko'rmaydi
    fwd = {k: v for k, v in request.headers.items()
           if k.lower() in ('content-type', 'accept', 'accept-language', 'user-agent', 'x-requested-with')}
    try:
        resp = requests.request(request.method, target, headers=fwd, data=request.get_data(),
                                allow_redirects=False, timeout=15)
    except requests.exceptions.ConnectionError:
        return ('<div style="color:#fff;background:#333;padding:20px;font-family:sans-serif;"><h2>Server ishlamayapti</h2>'
                '<p>Avval kodni Run bilan ishga tushiring. Server <code>PORT</code> muhit o\'zgaruvchisidagi portda ishlashi kerak.</p></div>'), 502
    except Exception as e:
        return 'Proxy xatosi: %s' % e, 502

    content = resp.content
    ctype = resp.headers.get('Content-Type', '')
    out = {}
    for k in ('Cache-Control', 'ETag', 'Last-Modified'):
        if k in resp.headers:
            out[k] = resp.headers[k]
    if 'text/html' in ctype:
        content = _rewrite_html(content.decode(resp.encoding or 'utf-8', 'replace'), base).encode('utf-8')
        out['Content-Type'] = 'text/html; charset=utf-8'
    elif ctype:
        out['Content-Type'] = ctype
    loc = resp.headers.get('Location')
    if resp.status_code in (301, 302, 303, 307, 308) and loc:
        sp = urlsplit(loc)
        if not sp.netloc or sp.hostname in ('127.0.0.1', 'localhost'):
            p = sp.path or '/'
            out['Location'] = base + p.lstrip('/') + ('?' + sp.query if sp.query else '')
        else:
            out['Location'] = loc
    out['X-Content-Type-Options'] = 'nosniff'
    out['Content-Security-Policy'] = 'sandbox allow-scripts allow-forms allow-modals allow-popups'
    return Response(content, resp.status_code, out)

# ============================================================
# API: TERMINAL & PROCESS MANAGEMENT
# ============================================================
def kill_process_tree(process):
    if process is None:
        return
    try:
        if sys.platform != 'win32':
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass

def _emit(run, text, err=False):
    out = run['output']
    out.append({'text': text, 'err': err, 'idx': run['since']})
    run['since'] += 1
    if len(out) > MAX_LINES:
        del out[:len(out) - MAX_LINES]

def _sandbox_env(pid, pdir, deps):
    # Serverning maxfiy o'zgaruvchilari (SECRET_KEY va h.k.) uzatilmaydi
    env = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'), 'HOME': pdir, 'LANG': 'C.UTF-8',
           'PYTHONUNBUFFERED': '1', 'PYTHONPATH': deps, 'PORT': str(proj_port(pid))}
    if sys.platform == 'win32':
        env['SYSTEMROOT'] = os.environ.get('SYSTEMROOT', '')
    return env

def _write_project(pid, pdir):
    if os.path.exists(pdir):
        shutil.rmtree(pdir, ignore_errors=True)
    os.makedirs(pdir, exist_ok=True)
    root = os.path.realpath(pdir)
    for f in query('SELECT name, content FROM files WHERE project_id=?', (pid,)):
        if not valid_filename(f['name']):
            continue
        fpath = os.path.realpath(os.path.join(root, f['name']))
        if not fpath.startswith(root + os.sep):
            continue
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, 'w', encoding='utf-8') as fh:
            fh.write(f['content'] or '')

def _stream(run, proc):
    run['process'] = proc
    for line in iter(proc.stdout.readline, ''):
        _emit(run, line)
    proc.wait()
    return proc.returncode

def _run_worker(run, pid, pdir, deps, fname):
    timer = None
    try:
        env = _sandbox_env(pid, pdir, deps)
        req = os.path.join(pdir, 'requirements.txt')
        if os.path.isfile(req):
            _emit(run, '📦 pip install -r requirements.txt ...\n')
            os.makedirs(deps, exist_ok=True)
            pp = subprocess.Popen(
                [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
                 '--retries', '1', '--timeout', '15', '--target', deps, '-r', req],
                cwd=pdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, bufsize=1, env=env, start_new_session=True)
            code = _stream(run, pp)
            if run['stop']:
                return
            if code != 0:
                _emit(run, '⚠️ pip xato bilan tugadi (kod %s)\n' % code, True)
        if fname is None:
            _emit(run, '\n[pip tugadi]\n', True)
            return
        proc = subprocess.Popen(
            [sys.executable, '-u', os.path.join(pdir, fname)],
            cwd=pdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, bufsize=1, env=env, start_new_session=True)
        if resource is not None and hasattr(resource, 'prlimit'):
            try:
                resource.prlimit(proc.pid, resource.RLIMIT_CPU, (MAX_RUN_SECONDS, MAX_RUN_SECONDS))
                resource.prlimit(proc.pid, resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
            except Exception:
                pass
        timer = threading.Timer(MAX_RUN_SECONDS, kill_process_tree, args=(proc,))
        timer.daemon = True
        timer.start()
        code = _stream(run, proc)
        _emit(run, '\n[Jarayon tugadi. Kod: %s]\n' % code, True)
    except Exception as e:
        _emit(run, 'Xato: %s\n' % e, True)
    finally:
        if timer:
            timer.cancel()
        run['done'] = True

def _cleanup_project(pid):
    run = active_runs.pop(pid, None)
    if run:
        run['stop'] = True
        kill_process_tree(run.get('process'))
    for d in ('project_%d' % pid, 'deps_%d' % pid):
        shutil.rmtree(os.path.join(SANDBOX, d), ignore_errors=True)

_run_seq = [0]

def _start_run(pid, filename):
    old = active_runs.get(pid)
    if old and not old['done']:
        return False, "Oldingi jarayon tugamagan. Kuting yoki 'stop' yozing."
    if filename is not None and (not valid_filename(filename) or not filename.endswith('.py')):
        return False, 'Faqat .py fayllarni ishga tushirish mumkin'
    pdir = os.path.join(SANDBOX, 'project_%d' % pid)
    deps = os.path.join(SANDBOX, 'deps_%d' % pid)
    _write_project(pid, pdir)
    if filename is not None and not os.path.isfile(os.path.join(pdir, filename)):
        return False, 'Fayl topilmadi'
    _run_seq[0] += 1
    run = {'process': None, 'output': [], 'since': 0, 'done': False, 'stop': False, 'rid': _run_seq[0]}
    active_runs[pid] = run
    threading.Thread(target=_run_worker, args=(run, pid, pdir, deps, filename), daemon=True).start()
    return True, None

@app.route('/api/projects/<int:pid>/terminal/run', methods=['POST'])
@login_required
def api_term_run(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    filename = ((request.get_json(silent=True) or {}).get('filename') or '').strip()
    if not filename:
        return jsonify(error='Fayl tanlanmagan'), 400
    ok, err = _start_run(pid, filename)
    if not ok:
        return jsonify(error=err), 400
    return jsonify(ok=True, port=proj_port(pid))

@app.route('/api/projects/<int:pid>/terminal/poll', methods=['POST'])
@login_required
def api_term_poll(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    run = active_runs.get(pid)
    if not run:
        return jsonify(lines=[], since=0, running=False, rid=0)
    body = request.get_json(silent=True) or {}
    try:
        since = int(body.get('since') or 0)
    except (TypeError, ValueError):
        since = 0
    if body.get('rid') != run['rid']:
        since = 0
    lines = [l for l in list(run['output']) if l['idx'] >= since]
    return jsonify(lines=lines, since=run['since'], running=not run['done'], rid=run['rid'])

@app.route('/api/projects/<int:pid>/terminal/stop', methods=['POST'])
@login_required
def api_term_stop(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    run = active_runs.get(pid)
    if run and not run['done']:
        run['stop'] = True
        kill_process_tree(run.get('process'))
        _emit(run, "\n[Server to'xtatildi]\n", True)
    return jsonify(ok=True)

# ============================================================
# API: PREVIEW (HTML/CSS) — to'g'ridan-to'g'ri bazadan, diskdan emas
# ============================================================
MIMES = {'html': 'text/html', 'htm': 'text/html', 'css': 'text/css', 'js': 'application/javascript',
         'json': 'application/json', 'svg': 'image/svg+xml', 'txt': 'text/plain', 'md': 'text/plain'}

@app.route('/api/projects/<int:pid>/preview/<path:filename>')
@login_required
def api_preview(pid, filename):
    if not get_project(pid):
        return 'Topilmadi', 404
    f = query('SELECT content FROM files WHERE project_id=? AND name=?', (pid, filename), one=True)
    if not f:
        return 'Fayl topilmadi: %s' % filename, 404
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    r = Response((f['content'] or '').encode('utf-8'), mimetype=MIMES.get(ext, 'text/plain'))
    r.headers['Content-Security-Policy'] = 'sandbox allow-scripts allow-forms allow-modals allow-popups'
    r.headers['X-Content-Type-Options'] = 'nosniff'
    r.headers['Cache-Control'] = 'no-store'
    return r

# ============================================================
# QO'SHIMCHA: sozlamalar, texnik ishlar, so'rovlar hisoblagichi
# ============================================================
UPLOAD_DIR = os.path.join(BASE, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_UPLOAD = 20 * 1024 * 1024
INLINE_MIMES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp',
                'video/mp4', 'video/webm', 'video/ogg',
                'audio/mpeg', 'audio/ogg', 'audio/wav', 'audio/webm', 'audio/mp4'}
EXT_MIME = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp',
            'mp4': 'video/mp4', 'webm': 'video/webm', 'ogv': 'video/ogg', 'mov': 'video/mp4',
            'mp3': 'audio/mpeg', 'ogg': 'audio/ogg', 'oga': 'audio/ogg', 'wav': 'audio/wav', 'm4a': 'audio/mp4'}
START_TIME = time.time()
ADMIN_USER = os.environ.get('ADMIN_USER', '33maktabit')
ADMIN_PASS = os.environ.get('ADMIN_PASS', '')
if not ADMIN_PASS:
    print('[CodeSync] OGOHLANTIRISH: ADMIN_PASS o\'rnatilmagan, admin panel o\'chirilgan.')
_req_counts = {}
_settings_cache = {'t': 0.0, 'v': {}}
_admin_fails = {}
_size_cache = {'t': 0.0, 'v': 0.0}
MAINT_OK = ('/admin', '/app.css', '/manifest.json', '/icon.png', '/service-worker.js', '/icons.js')

def get_setting(key, default=''):
    now = time.time()
    if now - _settings_cache['t'] > 2:
        try:
            _settings_cache['v'] = {r['key']: r['value'] for r in query('SELECT key, value FROM settings')}
        except Exception:
            pass
        _settings_cache['t'] = now
    return _settings_cache['v'].get(key, default)

def set_setting(key, value):
    with tx() as c:
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)', (key, value))
    _settings_cache['t'] = 0.0

@app.before_request
def _guard():
    m = int(time.time() // 60)
    _req_counts[m] = _req_counts.get(m, 0) + 1
    for k in [k for k in _req_counts if k < m - 10]:
        _req_counts.pop(k, None)
    path = request.path
    if request.content_length and request.content_length > 2 * 1024 * 1024 and not path.endswith('/messages/send'):
        return jsonify(error="So'rov juda katta"), 413
    if get_setting('maintenance') == '1' and not session.get('admin') and not path.startswith(MAINT_OK):
        if path.startswith(('/api/', '/px/', '/s/')):
            return jsonify(error='Texnik ishlar olib borilmoqda'), 503
        return render_template('stopped.html', message=get_setting('maintenance_msg')), 503

def _member(pid, uid):
    return query('SELECT 1 FROM projects WHERE id=? AND (owner_id=? OR id IN (SELECT project_id FROM members WHERE user_id=?))',
                 (pid, uid, uid), one=True)

def _delete_project(pid):
    _cleanup_project(pid)
    with tx() as c:
        c.execute('DELETE FROM attachments WHERE project_id=?', (pid,))
        for t in ('files', 'messages', 'members', 'voice_members', 'voice_signals', 'project_bans'):
            c.execute('DELETE FROM %s WHERE project_id=?' % t, (pid,))
        c.execute('DELETE FROM projects WHERE id=?', (pid,))
    shutil.rmtree(os.path.join(UPLOAD_DIR, str(pid)), ignore_errors=True)

def _delete_user(uid):
    for r in query('SELECT id FROM projects WHERE owner_id=?', (uid,)):
        _delete_project(r['id'])
    with tx() as c:
        c.execute('DELETE FROM members WHERE user_id=?', (uid,))
        c.execute('DELETE FROM voice_members WHERE user_id=?', (uid,))
        c.execute('DELETE FROM voice_signals WHERE from_user=? OR to_user=?', (uid, uid))
        c.execute('DELETE FROM project_bans WHERE user_id=?', (uid,))
        c.execute('DELETE FROM users WHERE id=?', (uid,))

# ---------- qo'shimcha statik fayllar ----------
@app.route('/icons.js')
def icons_js():
    return asset(ICONS_JS, 'application/javascript')

@app.route('/messages.js')
def messages_js():
    return asset(MESSAGES_JS, 'application/javascript')

@app.route('/admin.js')
def admin_js():
    return asset(ADMIN_JS, 'application/javascript')

# ============================================================
# SAHIFALAR: chat (messages), static ko'rish
# ============================================================
@app.route('/project/<int:pid>/messages')
@login_required
def messages_page(pid):
    p = get_project(pid)
    if not p:
        return redirect(url_for('dashboard'))
    return render_template('messages.html', project_id=pid, project_name=p['name'], uid=current_user.id)

def make_static_token(pid, uid):
    return URLSafeTimedSerializer(app.secret_key, salt='static').dumps({'p': pid, 'u': uid})

@app.route('/project/<int:pid>/static')
@login_required
def static_page(pid):
    p = get_project(pid)
    if not p:
        return redirect(url_for('dashboard'))
    return render_template('static.html', project_id=pid, project_name=p['name'],
                           token=make_static_token(pid, current_user.id))

@app.route('/api/projects/<int:pid>/versions')
@login_required
def api_versions(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    r = query('SELECT COUNT(*) AS n, COALESCE(SUM(version),0) AS s, COALESCE(SUM(LENGTH(name)),0) AS l FROM files WHERE project_id=?',
              (pid,), one=True)
    return jsonify(v='%d-%d-%d' % (r['n'], r['s'], r['l']))

STATIC_MIMES = {'html': 'text/html', 'htm': 'text/html', 'css': 'text/css', 'js': 'application/javascript',
                'mjs': 'application/javascript', 'json': 'application/json', 'svg': 'image/svg+xml',
                'txt': 'text/plain', 'md': 'text/plain', 'xml': 'application/xml', 'csv': 'text/csv',
                'webmanifest': 'application/manifest+json'}

@app.route('/s/<token>/', defaults={'path': ''})
@app.route('/s/<token>/<path:path>')
def static_serve(token, path):
    try:
        data = URLSafeTimedSerializer(app.secret_key, salt='static').loads(token, max_age=43200)
        pid, uid = int(data['p']), int(data['u'])
    except Exception:
        return 'Havola eskirgan. Static sahifani qaytadan oching.', 403
    if not _member(pid, uid):
        return "Ruxsat yo'q", 403
    if path == '' or path.endswith('/'):
        cands = [path + 'index.html']
    else:
        cands = [path, path + '.html', path + '/index.html']
    f, name = None, None
    for c in cands:
        f = query('SELECT content FROM files WHERE project_id=? AND name=?', (pid, c), one=True)
        if f:
            name = c
            break
    if not f and path == '':
        row = query("SELECT name, content FROM files WHERE project_id=? AND (name LIKE '%.html' OR name LIKE '%.htm') ORDER BY name LIMIT 1",
                    (pid,), one=True)
        if row:
            f, name = row, row['name']
    if not f:
        return ('<body style="font-family:sans-serif;padding:24px;color:#555"><h3>404</h3>Fayl topilmadi: %s</body>'
                % (path or 'index.html')).replace('<h3>', '<h3>'), 404
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    r = Response((f['content'] or '').encode('utf-8'), mimetype=STATIC_MIMES.get(ext, 'text/plain'))
    r.headers['Content-Security-Policy'] = 'sandbox allow-scripts allow-forms allow-modals allow-popups'
    r.headers['X-Content-Type-Options'] = 'nosniff'
    r.headers['Cache-Control'] = 'no-store'
    return r

# ============================================================
# CHAT: xabarlar, fayl/rasm/video, javoblar
# ============================================================
def _guess_mime(name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return EXT_MIME.get(ext) or mimetypes.guess_type(name)[0] or 'application/octet-stream'

def _att_json(a):
    mime = a['mime']
    kind = 'file'
    if mime in INLINE_MIMES:
        kind = mime.split('/')[0]
    return {'id': a['id'], 'name': a['filename'], 'mime': mime, 'size': a['size'], 'kind': kind}

def _build_messages(rows):
    if not rows:
        return []
    ids = [r['id'] for r in rows]
    atts = {a['message_id']: a for a in
            query('SELECT * FROM attachments WHERE message_id IN (%s)' % ','.join('?' * len(ids)), ids)}
    reply_ids = list({r['reply_to'] for r in rows if r['reply_to']})
    replies, reply_att = {}, set()
    if reply_ids:
        ph = ','.join('?' * len(reply_ids))
        for rr in query('SELECT id, username, text FROM messages WHERE id IN (%s)' % ph, reply_ids):
            replies[rr['id']] = rr
        reply_att = {a['message_id'] for a in query('SELECT message_id FROM attachments WHERE message_id IN (%s)' % ph, reply_ids)}
    out = []
    for r in rows:
        rep = None
        if r['reply_to'] and r['reply_to'] in replies:
            rr = replies[r['reply_to']]
            rep = {'id': rr['id'], 'username': rr['username'], 'text': (rr['text'] or '')[:100], 'att': rr['id'] in reply_att}
        a = atts.get(r['id'])
        out.append({'id': r['id'], 'user_id': r['user_id'], 'username': r['username'], 'text': r['text'] or '',
                    'created': r['created'], 'reply': rep, 'attachment': _att_json(a) if a else None})
    return out

@app.route('/api/projects/<int:pid>/messages/poll', methods=['POST'])
@login_required
def api_msg_poll(pid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    try:
        since = int((request.get_json(silent=True) or {}).get('since') or 0)
    except (TypeError, ValueError):
        since = 0
    now = time.time()
    uid = current_user.id
    with tx() as c:
        c.execute('INSERT OR IGNORE INTO members (project_id, user_id, last_seen) VALUES (?,?,?)', (pid, uid, now))
        c.execute('UPDATE members SET last_seen=? WHERE project_id=? AND user_id=?', (now, pid, uid))
    if since == 0:
        rows = list(reversed(query('SELECT * FROM messages WHERE project_id=? ORDER BY id DESC LIMIT 100', (pid,))))
    else:
        rows = query('SELECT * FROM messages WHERE project_id=? AND id>? ORDER BY id LIMIT 200', (pid, since))
    voice_ids = {r['user_id'] for r in query('SELECT user_id FROM voice_members WHERE project_id=? AND last_seen>?', (pid, now - 60))}
    members = []
    for r in query('SELECT u.id, u.username, COALESCE(m.last_seen,0) AS ls FROM users u '
                   'LEFT JOIN members m ON m.user_id=u.id AND m.project_id=? WHERE m.project_id=? OR u.id=?',
                   (pid, pid, p['owner_id'])):
        members.append({'id': r['id'], 'username': r['username'], 'online': r['ls'] > now - 10,
                        'voice': r['id'] in voice_ids, 'owner': r['id'] == p['owner_id']})
    is_owner = p['owner_id'] == uid
    banned = []
    if is_owner:
        banned = [dict(r) for r in query('SELECT u.id, u.username FROM project_bans b JOIN users u ON u.id=b.user_id WHERE b.project_id=?', (pid,))]
    return jsonify(messages=_build_messages(rows), members=members, banned=banned, is_owner=is_owner)

@app.route('/api/projects/<int:pid>/messages/send', methods=['POST'])
@login_required
def api_msg_send(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    text = (request.form.get('text') or '').strip()[:2000]
    reply_to = request.form.get('reply_to', type=int)
    f = request.files.get('file')
    has_file = bool(f and f.filename)
    if not text and not has_file:
        return jsonify(error="Bo'sh xabar"), 400
    if reply_to and not query('SELECT 1 FROM messages WHERE id=? AND project_id=?', (reply_to, pid), one=True):
        reply_to = None
    stored = fname = mime = None
    size = 0
    if has_file:
        data = f.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            return jsonify(error='Fayl 20 MB dan katta'), 413
        fname = re.sub(r'[\\/\x00-\x1f]', '_', f.filename)[:120] or 'fayl'
        mime = _guess_mime(fname)
        size = len(data)
        stored = secrets.token_hex(12)
        d = os.path.join(UPLOAD_DIR, str(pid))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, stored), 'wb') as fh:
            fh.write(data)
    with tx() as c:
        mid = c.execute('INSERT INTO messages (project_id, user_id, username, text, created, reply_to) VALUES (?,?,?,?,?,?)',
                        (pid, current_user.id, current_user.username, text, time.time(), reply_to)).lastrowid
        if has_file:
            c.execute('INSERT INTO attachments (project_id, message_id, filename, mime, size, stored) VALUES (?,?,?,?,?,?)',
                      (pid, mid, fname, mime, size, stored))
    return jsonify(ok=True, id=mid)

@app.route('/api/attachments/<int:aid>')
@login_required
def api_attachment(aid):
    a = query('SELECT * FROM attachments WHERE id=?', (aid,), one=True)
    if not a or not get_project(a['project_id']):
        return 'Topilmadi', 404
    path = os.path.join(UPLOAD_DIR, str(a['project_id']), a['stored'])
    if not os.path.isfile(path):
        return 'Fayl topilmadi', 404
    inline = a['mime'] in INLINE_MIMES and request.args.get('dl') != '1'
    resp = send_file(path, mimetype=a['mime'], as_attachment=not inline, download_name=a['filename'], conditional=True)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    return resp

# ---------- a'zolarni boshqarish (faqat loyiha egasi) ----------
@app.route('/api/projects/<int:pid>/members/<int:uid>/kick', methods=['POST'])
@login_required
def api_kick(pid, uid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    if p['owner_id'] != current_user.id:
        return jsonify(error='Faqat loyiha egasi chiqara oladi'), 403
    if uid == p['owner_id']:
        return jsonify(error="Loyiha egasini chiqarib bo'lmaydi"), 400
    with tx() as c:
        c.execute('DELETE FROM members WHERE project_id=? AND user_id=?', (pid, uid))
        c.execute('DELETE FROM voice_members WHERE project_id=? AND user_id=?', (pid, uid))
        c.execute('DELETE FROM voice_signals WHERE project_id=? AND (from_user=? OR to_user=?)', (pid, uid, uid))
        c.execute('INSERT OR IGNORE INTO project_bans (project_id, user_id) VALUES (?,?)', (pid, uid))
    return jsonify(ok=True)

@app.route('/api/projects/<int:pid>/members/<int:uid>/unban', methods=['POST'])
@login_required
def api_unban(pid, uid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    if p['owner_id'] != current_user.id:
        return jsonify(error='Faqat loyiha egasi'), 403
    with tx() as c:
        c.execute('DELETE FROM project_bans WHERE project_id=? AND user_id=?', (pid, uid))
    return jsonify(ok=True)

# ============================================================
# TERMINAL BUYRUQ QATORI (sandbox: faqat loyiha fayllari, tashqariga chiqib bo'lmaydi)
# ============================================================
HELP_TEXT = """Buyruqlar:
  ls [papka]            fayllar ro'yxati
  cd <papka>            papkaga o'tish (cd .. orqaga, cd / ildizga)
  pwd                   joriy papka
  cat <fayl>            fayl ichini ko'rish
  touch <fayl>          bo'sh fayl yaratish
  mkdir <papka>         papka yaratish
  rm [-r] <yo'l>        fayl yoki papkani o'chirish
  mv <a> <b>            ko'chirish / nomini o'zgartirish
  cp <a> <b>            fayl nusxasi
  echo matn [> fayl]    matn chiqarish yoki faylga yozish (>> qo'shish)
  python <fayl.py>      dasturni ishga tushirish
  pip install <paket>   kutubxona o'rnatish (requirements.txt ga ham yoziladi)
  stop                  ishlayotgan jarayonni to'xtatish
  clear                 ekranni tozalash
Loyiha papkasidan tashqariga chiqib bo'lmaydi."""

def _clean_dir(p):
    parts = []
    for s in (p or '').replace('\\', '/').split('/'):
        if s in ('', '.'):
            continue
        if s == '..':
            if parts:
                parts.pop()
            continue
        parts.append(s)
    return '/'.join(parts)

def _resolve(cwd, arg):
    if arg.startswith('/') or arg == '~':
        return _clean_dir(arg.lstrip('/~'))
    return _clean_dir((cwd + '/' if cwd else '') + arg)

def _is_dir(names, d):
    return d == '' or any(n.startswith(d + '/') for n in names)

PKG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,\-]+\])?([=<>!~]=?[A-Za-z0-9_.*+\-]+(,[=<>!~]=?[A-Za-z0-9_.*+\-]+)*)?$')

def _file_write(pid, name, content, append=False):
    row = query('SELECT content FROM files WHERE project_id=? AND name=?', (pid, name), one=True)
    with tx() as c:
        if row:
            new = ((row['content'] or '') + content) if append else content
            c.execute('UPDATE files SET content=?, version=version+1 WHERE project_id=? AND name=?', (new, pid, name))
        else:
            c.execute('INSERT INTO files (project_id, name, content, version) VALUES (?,?,?,1)', (pid, name, content))

def _shell(p, line, cwd):
    pid = p['id']

    def R(text='', err=False, **kw):
        r = {'out': text, 'err': err, 'cwd': cwd, 'clear': False, 'started': False}
        r.update(kw)
        return r

    if not line:
        return R()
    try:
        toks = shlex.split(line)
    except ValueError:
        return R('Tirnoq yopilmagan', True)
    if not toks:
        return R()
    cmd, args = toks[0], toks[1:]
    names = [r['name'] for r in query('SELECT name FROM files WHERE project_id=?', (pid,))]
    nset = set(names)

    if cmd == 'help':
        return R(HELP_TEXT)
    if cmd == 'clear':
        return R(clear=True)
    if cmd == 'pwd':
        return R('/%s%s' % (p['name'], '/' + cwd if cwd else ''))
    if cmd == 'ls':
        paths = [a for a in args if not a.startswith('-')] or ['']
        outs = []
        for a in paths:
            d = _resolve(cwd, a) if a else cwd
            if d in nset:
                outs.append(d.split('/')[-1])
                continue
            if not _is_dir(names, d):
                return R('ls: %s: topilmadi' % a, True)
            dirs, files = set(), set()
            for n in names:
                if d == '' or n.startswith(d + '/'):
                    rest = n[len(d) + 1:] if d else n
                    if '/' in rest:
                        dirs.add(rest.split('/')[0] + '/')
                    elif rest != '.keep':
                        files.add(rest)
            ent = sorted(dirs) + sorted(files)
            if len(paths) > 1:
                outs.append(a + ':')
            outs.append('  '.join(ent) if ent else "(bo'sh)")
        return R('\n'.join(outs))
    if cmd == 'cd':
        target = args[0] if args else ''
        d = '' if target in ('', '~', '/') else _resolve(cwd, target)
        if d in nset:
            return R('cd: %s: papka emas' % target, True)
        if not _is_dir(names, d):
            return R('cd: %s: papka topilmadi' % target, True)
        return R(cwd=d)
    if cmd == 'cat':
        if not args:
            return R('cat: fayl nomi kerak', True)
        parts = []
        for a in args:
            row = query('SELECT content FROM files WHERE project_id=? AND name=?', (pid, _resolve(cwd, a)), one=True)
            if not row:
                return R('cat: %s: fayl topilmadi' % a, True)
            parts.append((row['content'] or '')[:20000])
        return R('\n'.join(parts))
    if cmd == 'touch':
        if not args:
            return R('touch: fayl nomi kerak', True)
        for a in args:
            n = _resolve(cwd, a)
            if not valid_filename(n):
                return R("touch: nom noto'g'ri: %s" % a, True)
            if n in nset:
                continue
            if len(nset) >= 200:
                return R('Fayllar soni chegaradan oshdi', True)
            _file_write(pid, n, '')
            nset.add(n)
        return R()
    if cmd == 'mkdir':
        if not args:
            return R('mkdir: papka nomi kerak', True)
        for a in args:
            d = _resolve(cwd, a)
            if d == '' or d in nset:
                return R('mkdir: %s: yaratib bo\'lmaydi' % a, True)
            if _is_dir(names, d):
                return R('mkdir: %s: mavjud' % a, True)
            if not valid_filename(d + '/.keep'):
                return R("mkdir: nom noto'g'ri: %s" % a, True)
            _file_write(pid, d + '/.keep', '')
        return R()
    if cmd == 'rm':
        rec = any(a in ('-r', '-rf', '-fr', '-R') for a in args)
        targets = [a for a in args if not a.startswith('-')]
        if not targets:
            return R('rm: nima o\'chirilsin?', True)
        for a in targets:
            n = _resolve(cwd, a)
            if n == '':
                return R("rm: loyiha ildizini o'chirib bo'lmaydi", True)
            if n in nset:
                victims = [n]
            elif _is_dir(names, n):
                if not rec:
                    return R('rm: %s papka, -r kerak' % a, True)
                victims = [x for x in names if x.startswith(n + '/')]
            else:
                return R('rm: %s: topilmadi' % a, True)
            with tx() as c:
                for v in victims:
                    c.execute('DELETE FROM files WHERE project_id=? AND name=?', (pid, v))
            names = [x for x in names if x not in victims]
            nset = set(names)
        new_cwd = cwd if _is_dir(names, cwd) else ''
        return R(cwd=new_cwd)
    if cmd in ('mv', 'cp'):
        if len(args) != 2:
            return R('Foydalanish: %s <manba> <manzil>' % cmd, True)
        src, dst = _resolve(cwd, args[0]), _resolve(cwd, args[1])
        if src == '':
            return R('%s: ildiz papkani ko\'chirib bo\'lmaydi' % cmd, True)
        base = src.split('/')[-1]
        if src in nset:
            if dst != '' and dst not in nset and _is_dir(names, dst):
                dst = dst + '/' + base
            elif dst == '':
                dst = base
            if not valid_filename(dst):
                return R("%s: manzil noto'g'ri" % cmd, True)
            if dst in nset:
                return R('%s: %s mavjud' % (cmd, dst), True)
            if cmd == 'mv':
                with tx() as c:
                    c.execute('UPDATE files SET name=?, version=version+1 WHERE project_id=? AND name=?', (dst, pid, src))
            else:
                row = query('SELECT content FROM files WHERE project_id=? AND name=?', (pid, src), one=True)
                _file_write(pid, dst, row['content'] or '')
            return R()
        if _is_dir(names, src):
            if cmd == 'cp':
                return R('cp: papkani nusxalash qo\'llanmaydi', True)
            if dst in nset:
                return R('mv: manzil fayl', True)
            if dst == '' or _is_dir(names, dst):
                dst = (dst + '/' if dst else '') + base
            if dst == src or dst.startswith(src + '/'):
                return R("mv: papkani o'zining ichiga ko'chirib bo'lmaydi", True)
            moves = [(n, dst + n[len(src):]) for n in names if n.startswith(src + '/')]
            for _, new in moves:
                if not valid_filename(new) or new in nset:
                    return R('mv: manzilda nom to\'qnashuvi: %s' % new, True)
            with tx() as c:
                for old, new in moves:
                    c.execute('UPDATE files SET name=?, version=version+1 WHERE project_id=? AND name=?', (new, pid, old))
            return R(cwd='' if cwd == src or cwd.startswith(src + '/') else cwd)
        return R('%s: %s: topilmadi' % (cmd, args[0]), True)
    if cmd == 'echo':
        for op in ('>>', '>'):
            if op in args:
                i = args.index(op)
                if i + 1 >= len(args):
                    return R('echo: fayl nomi kerak', True)
                n = _resolve(cwd, args[i + 1])
                if not valid_filename(n):
                    return R("echo: nom noto'g'ri", True)
                _file_write(pid, n, ' '.join(args[:i]) + '\n', append=(op == '>>'))
                return R()
        return R(' '.join(args))
    if cmd in ('python', 'python3', 'py'):
        if not args:
            return R('Foydalanish: python <fayl.py>', True)
        n = _resolve(cwd, args[0])
        if n not in nset:
            return R('python: %s: fayl topilmadi' % args[0], True)
        ok, err = _start_run(pid, n)
        if not ok:
            return R(err, True)
        return R('▶ %s ishga tushirildi (PORT=%d)' % (n, proj_port(pid)), started=True)
    if cmd in ('pip', 'pip3'):
        pkgs = [a for a in args[1:] if not a.startswith('-')] if args and args[0] == 'install' else []
        if not pkgs:
            return R('Foydalanish: pip install <paket> ...', True)
        for a in pkgs:
            if not PKG_RE.match(a):
                return R("pip: paket nomi noto'g'ri: %s" % a, True)
        row = query('SELECT content FROM files WHERE project_id=? AND name=?', (pid, 'requirements.txt'), one=True)
        lines = [l for l in (row['content'] or '').split('\n')] if row else []
        have = {re.split(r'[=<>!~\[]', l.strip())[0].lower() for l in lines if l.strip()}
        add = [a for a in pkgs if re.split(r'[=<>!~\[]', a)[0].lower() not in have]
        if add:
            body = '\n'.join(l for l in lines if l.strip())
            _file_write(pid, 'requirements.txt', (body + '\n' if body else '') + '\n'.join(add) + '\n')
        ok, err = _start_run(pid, None)
        if not ok:
            return R(err, True)
        return R('📦 o\'rnatilmoqda: %s' % ' '.join(pkgs), started=True)
    if cmd == 'stop':
        run = active_runs.get(pid)
        if run and not run['done']:
            run['stop'] = True
            kill_process_tree(run.get('process'))
            _emit(run, "\n[Server to'xtatildi]\n", True)
            return R("To'xtatilmoqda...")
        return R("Ishlayotgan jarayon yo'q")
    return R("%s: buyruq topilmadi. 'help' yozing" % cmd, True)

@app.route('/api/projects/<int:pid>/terminal/exec', methods=['POST'])
@login_required
def api_term_exec(pid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    d = request.get_json(silent=True) or {}
    cmd = str(d.get('cmd') or '').strip()[:500]
    cwd = _clean_dir(str(d.get('cwd') or ''))
    return jsonify(_shell(p, cmd, cwd))

# ============================================================
# ADMIN PANEL
# ============================================================
def admin_required(f):
    @functools.wraps(f)
    def wrapper(*a, **k):
        if not session.get('admin'):
            return jsonify(error='Admin kirishi kerak'), 401
        return f(*a, **k)
    return wrapper

@app.route('/admin', methods=['GET', 'POST'])
def admin_page():
    error = None
    if request.method == 'POST':
        ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[-1].strip()
        rec = _admin_fails.get(ip, [0, 0])
        if rec[1] > time.time():
            error = "Juda ko'p urinish. 5 daqiqadan keyin qayta urining"
        elif not ADMIN_PASS:
            error = "Admin paroli sozlanmagan"
        else:
            u = (request.form.get('username') or '').encode('utf-8')
            pw = (request.form.get('password') or '').encode('utf-8')
            ok = secrets.compare_digest(u, ADMIN_USER.encode('utf-8')) & secrets.compare_digest(pw, ADMIN_PASS.encode('utf-8'))
            if ok:
                session['admin'] = True
                _admin_fails.pop(ip, None)
                return redirect('/admin')
            rec[0] += 1
            if rec[0] >= 5:
                rec = [0, time.time() + 300]
            _admin_fails[ip] = rec
            error = 'Login yoki parol xato'
    if session.get('admin'):
        return render_template('admin.html')
    return render_template('admin_login.html', error=error, disabled=not ADMIN_PASS)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/admin')

def _read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None

def _mem_info():
    host_total = host_used = None
    mi = _read_file('/proc/meminfo')
    if mi:
        d = {}
        for line in mi.splitlines():
            k, _, v = line.partition(':')
            try:
                d[k] = int(v.strip().split()[0]) * 1024
            except (ValueError, IndexError):
                pass
        host_total = d.get('MemTotal')
        if host_total and d.get('MemAvailable') is not None:
            host_used = host_total - d['MemAvailable']
    cur, mx = _read_file('/sys/fs/cgroup/memory.current'), _read_file('/sys/fs/cgroup/memory.max')
    if cur and cur.isdigit():
        used = int(cur)
        total = int(mx) if mx and mx.isdigit() else host_total
    else:
        used, total = host_used, host_total
    if used is None:
        return None
    return {'used_mb': used // 1048576, 'total_mb': (total // 1048576) if total else None,
            'pct': round(used * 100 / total) if total else None}

def _dir_mb(path):
    now = time.time()
    if now - _size_cache['t'] < 30 and path == UPLOAD_DIR:
        return _size_cache['v']
    total = 0
    for root_, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(root_, f))
            except OSError:
                pass
    val = round(total / 1048576, 1)
    if path == UPLOAD_DIR:
        _size_cache.update(t=now, v=val)
    return val

@app.route('/admin/api/stats')
@admin_required
def admin_stats():
    now = time.time()
    m = int(now // 60)
    cpus = os.cpu_count() or 1
    try:
        load = list(os.getloadavg())
    except (OSError, AttributeError):
        load = None
    mem = _mem_info()
    status = 'ok'
    pct = (mem or {}).get('pct') or 0
    l1 = (load[0] / cpus) if load else 0
    if l1 > 1.5 or pct > 90:
        status = 'overload'
    elif l1 > 0.8 or pct > 75:
        status = 'busy'
    try:
        disk_free = round(shutil.disk_usage(BASE).free / 1073741824, 1)
    except Exception:
        disk_free = 0
    try:
        db_mb = round(os.path.getsize(DB) / 1048576, 2)
    except OSError:
        db_mb = 0
    online = [r['username'] for r in query('SELECT DISTINCT u.username FROM members m JOIN users u ON u.id=m.user_id WHERE m.last_seen>?', (now - 10,))]
    voice = [r['username'] for r in query('SELECT DISTINCT username FROM voice_members WHERE last_seen>?', (now - 60,))]
    return jsonify(
        users=query('SELECT COUNT(*) AS n FROM users', one=True)['n'],
        banned=query('SELECT COUNT(*) AS n FROM users WHERE banned=1', one=True)['n'],
        projects=query('SELECT COUNT(*) AS n FROM projects', one=True)['n'],
        online=online, voice=voice,
        maintenance={'on': get_setting('maintenance') == '1', 'message': get_setting('maintenance_msg')},
        server={'status': status, 'uptime': int(now - START_TIME), 'load': load, 'cpus': cpus, 'mem': mem,
                'rpm': _req_counts.get(m, 0), 'rpm_prev': _req_counts.get(m - 1, 0),
                'runs': sum(1 for r in active_runs.values() if not r['done']),
                'disk_free_gb': disk_free, 'db_mb': db_mb, 'uploads_mb': _dir_mb(UPLOAD_DIR)})

@app.route('/admin/api/users')
@admin_required
def admin_users():
    now = time.time()
    rows = query('SELECT u.id, u.username, COALESCE(u.banned,0) AS banned, '
                 '(SELECT COUNT(*) FROM projects p WHERE p.owner_id=u.id) AS owned, '
                 '(SELECT COUNT(*) FROM members m WHERE m.user_id=u.id AND m.project_id NOT IN (SELECT id FROM projects WHERE owner_id=u.id)) AS joined, '
                 '(SELECT MAX(last_seen) FROM members m2 WHERE m2.user_id=u.id) AS last_seen '
                 'FROM users u ORDER BY u.id')
    return jsonify(users=[{'id': r['id'], 'username': r['username'], 'banned': bool(r['banned']), 'owned': r['owned'],
                           'joined': r['joined'], 'last_seen': r['last_seen'] or 0,
                           'online': bool(r['last_seen'] and r['last_seen'] > now - 10)} for r in rows])

@app.route('/admin/api/users/<int:uid>/ban', methods=['POST'])
@admin_required
def admin_ban(uid):
    banned = 1 if (request.get_json(silent=True) or {}).get('banned') else 0
    with tx() as c:
        c.execute('UPDATE users SET banned=? WHERE id=?', (banned, uid))
        if banned:
            c.execute('DELETE FROM voice_members WHERE user_id=?', (uid,))
    return jsonify(ok=True)

@app.route('/admin/api/users/<int:uid>/delete', methods=['POST'])
@admin_required
def admin_delete_user(uid):
    _delete_user(uid)
    return jsonify(ok=True)

@app.route('/admin/api/maintenance', methods=['POST'])
@admin_required
def admin_maintenance():
    d = request.get_json(silent=True) or {}
    set_setting('maintenance', '1' if d.get('on') else '0')
    set_setting('maintenance_msg', str(d.get('message') or '')[:300])
    return jsonify(ok=True)

# ============================================================
# API: OVOZLI CHAT (WebRTC)
# ============================================================
def ice_servers():
    raw = os.environ.get('ICE_SERVERS')
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                return v
        except Exception:
            pass
    return [{'urls': ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302']}]

@app.route('/api/projects/<int:pid>/voice', methods=['POST'])
@login_required
def api_voice(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    action = (request.get_json(silent=True) or {}).get('action')
    now = time.time()
    uid = current_user.id
    if action == 'leave':
        with tx() as c:
            c.execute('DELETE FROM voice_members WHERE project_id=? AND user_id=?', (pid, uid))
            c.execute('DELETE FROM voice_signals WHERE project_id=? AND (from_user=? OR to_user=?)', (pid, uid, uid))
        return jsonify(ok=True)
    with tx() as c:
        c.execute('INSERT OR REPLACE INTO voice_members (project_id, user_id, username, last_seen) VALUES (?,?,?,?)',
                  (pid, uid, current_user.username, now))
        c.execute('DELETE FROM voice_members WHERE project_id=? AND last_seen < ?', (pid, now - 60))
    return jsonify(ok=True, ice=ice_servers())

@app.route('/api/projects/<int:pid>/voice/poll', methods=['POST'])
@login_required
def api_voice_poll(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    since = int((request.get_json(silent=True) or {}).get('since') or 0)
    now = time.time()
    uid = current_user.id
    with tx() as c:
        row = c.execute('SELECT 1 FROM voice_members WHERE project_id=? AND user_id=?', (pid, uid)).fetchone()
        if not row:
            return jsonify(gone=True)
        c.execute('UPDATE voice_members SET last_seen=? WHERE project_id=? AND user_id=?', (now, pid, uid))
        c.execute('DELETE FROM voice_members WHERE project_id=? AND last_seen < ?', (pid, now - 60))
        c.execute('DELETE FROM voice_signals WHERE project_id=? AND created < ?', (pid, now - 60))
    peers = query('SELECT user_id AS id, username FROM voice_members WHERE project_id=? AND user_id!=?', (pid, uid))
    signals = query('SELECT id, from_user, kind, data FROM voice_signals WHERE project_id=? AND to_user=? AND id>? ORDER BY id', (pid, uid, since))
    for s in signals:
        with tx() as c:
            c.execute('DELETE FROM voice_signals WHERE id=?', (s['id'],))
    return jsonify(peers=[dict(p) for p in peers], signals=[dict(s) for s in signals],
                   ice=ice_servers())

@app.route('/api/projects/<int:pid>/voice/signal', methods=['POST'])
@login_required
def api_voice_signal(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    d = request.get_json(silent=True) or {}
    try:
        to = int(d.get('to'))
    except (TypeError, ValueError):
        return jsonify(error='Xato signal'), 400
    kind = d.get('kind')
    data = d.get('data')
    if kind not in ('offer', 'answer', 'ice') or not isinstance(data, str) or not data or len(data) > 20000:
        return jsonify(error='Xato signal'), 400
    if not query('SELECT 1 FROM voice_members WHERE project_id=? AND user_id=?', (pid, to), one=True):
        return jsonify(ok=True)
    with tx() as c:
        c.execute('INSERT INTO voice_signals (project_id, from_user, to_user, kind, data, created) VALUES (?,?,?,?,?,?)',
                  (pid, current_user.id, to, kind, data, time.time()))
    return jsonify(ok=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
