import os, re, time, hashlib, secrets, sqlite3, subprocess, threading, shutil, sys, signal, zipfile, io
from contextlib import contextmanager
from datetime import timedelta
from flask import Flask, request, jsonify, redirect, url_for, render_template, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import DictLoader

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('DB_PATH', os.path.join(BASE, 'codesync.db'))
SANDBOX = os.path.join(BASE, 'sandbox')
os.makedirs(SANDBOX, exist_ok=True)

active_runs = {}

# ============================================================
# HTML SHABLONLAR
# ============================================================
TEMPLATES = {
'base.html': r'''<!DOCTYPE html>
<html lang="uz"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}CodeSync{% endblock %}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚡</text></svg>">
<link rel="stylesheet" href="/app.css">
{% block head %}{% endblock %}
</head><body>
{% block body %}{% endblock %}
{% block scripts %}{% endblock %}
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
      <a href="/api/projects/{{ project_id }}/download" class="btn btn-sec" style="color:#00ff00; border-color:#00ff00" target="_blank">⬇ ZIP</a>
      <button class="btn btn-pri" id="run-btn">▶ Run</button>
      <button class="btn btn-sec" id="panel-btn">💬</button>
    </div>
  </header>
  <div class="body">
    <aside class="side">
      <div class="side-h"><span>FAYLLAR</span><button class="btn btn-sec" id="new-file" style="padding:2px 9px">+</button></div>
      <div class="tree" id="file-tree"></div>
    </aside>
    <main class="main">
      <div class="tabname" id="active-name">&nbsp;</div>
      <div id="editor-container"></div>
      <section class="term" id="bottom-term">
        <div class="term-h">
          <span>TERMINAL (Server)</span>
          <div>
            <button class="btn btn-sec" id="clear-term" style="padding:1px 8px">Tozalash</button>
            <button class="btn btn-danger" id="stop-term" style="padding:1px 8px;display:none;">To'xtatish</button>
          </div>
        </div>
        <div id="term-out"></div>
      </section>
    </main>
    <aside class="right hidden" id="right">
      <div class="rt">
        <button class="tab active" data-panel="chat">💬 Chat</button>
        <button class="tab" data-panel="preview">🌐 HTML</button>
        <button class="tab" data-panel="voice">🎤 Ovoz</button>
        <button class="tab" id="close-right" style="flex:0 0 40px">✕</button>
      </div>
      <div class="pane" id="pane-chat">
        <div id="chat-box"></div>
        <form id="chat-form"><input class="input" id="chat-input" placeholder="Xabar yozing..." autocomplete="off" maxlength="1000"><button class="btn btn-pri" type="submit">➤</button></form>
      </div>
      <div class="pane hidden" id="pane-preview" style="padding:0">
        <iframe id="preview-frame" sandbox="allow-scripts allow-forms allow-modals allow-popups"></iframe>
      </div>
      <div class="pane hidden" id="pane-voice">
        <div class="voice" style="padding:20px;display:flex;flex-direction:column;gap:10px;height:100%">
          <div id="voice-status" class="muted" style="text-align:center"></div>
          <div id="voice-peers" style="flex:1;overflow:auto;display:flex;flex-direction:column;gap:6px;background:#1e1e1e;padding:10px;border-radius:6px"></div>
          <div id="voice-audio" style="display:none"></div>
          <div style="display:flex;gap:10px">
            <button class="btn btn-pri" id="voice-join" style="flex:1">🎤 Qo'shilish</button>
            <button class="btn btn-sec hidden" id="voice-mute" style="flex:1">🔇 O'chirish</button>
            <button class="btn btn-danger hidden" id="voice-leave" style="flex:1">📞 Chiqish</button>
          </div>
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
<script src="/editor.js"></script>
{% endblock %}''',

'terminal.html': r'''{% extends "base.html" %}
{% block title %}Terminal — {{ project_name }}{% endblock %}
{% block body %}
<div class="ide" style="background:#000;">
  <header class="bar">
    <div class="bar-l">
      <a class="btn btn-sec" href="/project/{{ project_id }}">← Orqaga</a>
      <b class="logo" style="color:#00ff00">📟 Server Terminali</b>
    </div>
    <div class="bar-r">
      <button class="btn btn-sec" id="clear-full-term">Tozalash</button>
      <button class="btn btn-danger" id="stop-full-term" style="display:none;">To'xtatish</button>
    </div>
  </header>
  <div style="flex:1;padding:10px;overflow:auto;" id="full-term-out"></div>
</div>
<script>
  var PID = "{{ project_id }}";
  var term = document.getElementById('full-term-out');
  var stopBtn = document.getElementById('stop-full-term');
  var since = 0;
  var polling = false;

  function append(text, isErr) {
    var d = document.createElement('pre');
    d.style.margin = '0'; d.style.whiteSpace = 'pre-wrap'; d.style.fontFamily = 'monospace';
    d.style.color = isErr ? '#ff6b6b' : '#00ff00';
    d.textContent = text; term.appendChild(d); term.scrollTop = term.scrollHeight;
  }

  async function poll() {
    if (polling) return; polling = true;
    try {
      var r = await fetch('/api/projects/' + PID + '/terminal/poll', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({since: since}) });
      var d = await r.json();
      if (d.lines && d.lines.length) {
        d.lines.forEach(function(l) { append(l.text, l.err); });
        since = d.since;
      }
      stopBtn.style.display = d.running ? 'inline-block' : 'none';
    } catch (e) {} finally { polling = false; }
  }

  document.getElementById('clear-full-term').onclick = function() { term.innerHTML = ''; };
  stopBtn.onclick = function() { 
      fetch('/api/projects/' + PID + '/terminal/stop', {method: 'POST'}).then(function() {
          stopBtn.style.display = 'none';
      });
  };

  setInterval(poll, 500);
  poll();
</script>
{% endblock %}''',

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
      <input type="text" id="url-input" class="input" value="http://127.0.0.1:5001" style="width:220px; padding:4px 8px;" placeholder="http://127.0.0.1:5001">
      <button class="btn btn-pri" onclick="loadPreview()">Ochish</button>
      <button class="btn btn-sec" onclick="document.getElementById('preview-iframe').src = document.getElementById('preview-iframe').src;">⟳ Yangilash</button>
      <button class="btn btn-danger" onclick="stopServer()">⏹ Serverni To'xtatish</button>
    </div>
  </header>
  <div style="flex:1; background:#fff; display:flex; flex-direction:column; position:relative;">
    <iframe id="preview-iframe" sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups" style="flex:1; width:100%; border:none; display:none;"></iframe>
    <div id="empty-msg" style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); color:#666; font-family:sans-serif; text-align:center; padding:20px;">
      <h2 style="margin-bottom:10px;">Veb-server Preview</h2>
      <p>Kodingizda <code>app.run(port=5001)</code> kabi portni ishga tushiring.</p>
      <p>So'ngra yuqoridagi maydonga to'liq URL manzilni yozib "Ochish" tugmasini bosing.</p>
    </div>
  </div>
</div>
<script>
  function loadPreview() {
    var url = document.getElementById('url-input').value.trim();
    var iframe = document.getElementById('preview-iframe');
    var emptyMsg = document.getElementById('empty-msg');
    
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        url = 'http://' + url;
    }
    
    if (url) {
      iframe.src = url;
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
  var ide = $('ide');
  var PID = ide.dataset.project;
  var ME = ide.dataset.user;
  var UID = parseInt(ide.dataset.uid, 10);
  var state = { files: [], active: null, project: null, chatSince: 0 };
  var seen = new Set();
  var ed = null, applying = false, syncing = false, running = false;
  var termSince = 0, termPollTimer = null;

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
        if (folder) { var fo = document.createElement('div'); fo.className = 'fold'; fo.textContent = '📁 ' + folder; box.appendChild(fo); }
      }
      var el = document.createElement('div');
      el.className = 'fi' + (state.active === f.id ? ' active' : '');
      if (folder) el.style.paddingLeft = '24px';
      var ic = document.createElement('span'); ic.textContent = '📄';
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

  function openFile(id) {
    var f = byId(id); if (!f) return;
    state.active = id;
    ed.load(f.content, modeFor(f.name));
    $('active-name').textContent = f.name;
    renderTree();
  }
  function closeEditor() { state.active = null; ed.load('', 'text/plain'); $('active-name').innerHTML = '&nbsp;'; }
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
    if (state.active === id) pickFirst();
    renderTree();
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

  async function sync() {
    if (syncing) return; syncing = true;
    try {
      var versions = {};
      state.files.forEach(function (f) { versions[f.id] = f.version; });
      var r = await api('/api/projects/' + PID + '/sync', 'POST', { versions: versions, chat_since: state.chatSince });
      var changed = false;
      var ids = new Set(r.files.map(function (x) { return x.id; }));
      var before = state.files.length;
      state.files = state.files.filter(function (f) { return ids.has(f.id); });
      if (state.files.length !== before) { changed = true; if (!byId(state.active)) pickFirst(); }
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
      if (changed) renderTree();
      r.chat.forEach(addMsg);
      var names = r.online.filter(function (n) { return n !== ME; });
      $('online').textContent = names.length ? '👥 ' + names.join(', ') : '';
      $('status').textContent = '🟢';
    } catch (e) { $('status').textContent = '🔴 Oflayn'; }
    finally { syncing = false; }
  }

  function addMsg(m) {
    if (seen.has(m.id)) return;
    seen.add(m.id);
    if (m.id > state.chatSince) state.chatSince = m.id;
    var box = $('chat-box');
    var near = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    var d = document.createElement('div');
    d.className = 'msg' + (m.username === ME ? ' me' : '');
    var b = document.createElement('b'); b.textContent = m.username;
    d.appendChild(b); d.appendChild(document.createTextNode(': ' + m.text));
    box.appendChild(d);
    if (near || m.username === ME) box.scrollTop = box.scrollHeight;
  }
  $('chat-form').onsubmit = async function (e) {
    e.preventDefault();
    var inp = $('chat-input');
    var text = inp.value.trim();
    if (!text) return;
    inp.value = '';
    try { var m = await api('/api/projects/' + PID + '/chat', 'POST', { text: text }); addMsg(m); }
    catch (err) { inp.value = text; alert(err.message); }
  };

  var voice = { on: false, stream: null, pcs: {}, since: 0, timer: null, muted: false, ice: [], peers: [], polling: false };
  function vstatus(t) { $('voice-status').textContent = t; }
  function sendSignal(to, kind, data) { api('/api/projects/' + PID + '/voice/signal', 'POST', { to: to, kind: kind, data: JSON.stringify(data) }).catch(function () {}); }
  function closePeer(pid) {
    var pc = voice.pcs[pid];
    if (pc) { pc.onconnectionstatechange = null; pc.onicecandidate = null; try { pc.close(); } catch (e) {} }
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
      if (pc.connectionState === 'failed' && voice.pcs[pid] === pc) closePeer(pid);
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
      Object.keys(voice.pcs).forEach(function (k) { if (!ids[k]) closePeer(+k); });
      for (var i = 0; i < r.signals.length; i++) {
        var s = r.signals[i]; if (s.id > voice.since) voice.since = s.id;
        await handleSignal(s);
      }
      renderVoice();
    } catch (e) {} finally { voice.polling = false; }
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
    var tab = document.querySelector('.tab[data-panel="voice"]');
    tab.textContent = '🎤 Ovoz' + (voice.on ? ' (' + (voice.peers.length + 1) + ')' : '');
    $('voice-join').classList.toggle('hidden', voice.on);
    $('voice-mute').classList.toggle('hidden', !voice.on);
    $('voice-leave').classList.toggle('hidden', !voice.on);
    $('voice-mute').textContent = voice.muted ? '🎤 Yoqish' : '🔇 O\'chirish';
    if (voice.on) vstatus(voice.peers.length ? 'Ovozli chat ishlayapti' : 'Boshqalar qo\'shilishini kutyapmiz...');
  }
  async function joinVoice() {
    if (voice.on) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.RTCPeerConnection) {
      vstatus('Brauzer mikrofonni qo\'llamaydi (HTTPS kerak)'); return;
    }
    vstatus('Mikrofon so\'ralmoqda...');
    try { voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false }); }
    catch (e) { vstatus('Mikrofonga ruxsat berilmadi'); return; }
    try { var d = await api('/api/projects/' + PID + '/voice', 'POST', { action: 'join' }); voice.ice = d.ice || []; }
    catch (e) { voice.stream.getTracks().forEach(function (t) { t.stop(); }); voice.stream = null; vstatus('Xato: ' + e.message); return; }
    voice.on = true; voice.since = 0; voice.muted = false; voice.peers = [];
    renderVoice(); voicePoll();
    voice.timer = setInterval(voicePoll, 800);
  }
  function leaveVoice() {
    if (!voice.on) return;
    voice.on = false; clearInterval(voice.timer);
    Object.keys(voice.pcs).forEach(function (k) { closePeer(+k); });
    if (voice.stream) voice.stream.getTracks().forEach(function (t) { t.stop(); });
    voice.stream = null; voice.peers = [];
    api('/api/projects/' + PID + '/voice', 'POST', { action: 'leave' }).catch(function () {});
    renderVoice(); vstatus('Ovozli chatdan chiqdingiz');
  }
  $('voice-join').onclick = joinVoice;
  $('voice-leave').onclick = leaveVoice;
  $('voice-mute').onclick = function () {
    voice.muted = !voice.muted;
    if (voice.stream) voice.stream.getAudioTracks().forEach(function (t) { t.enabled = !voice.muted; });
    renderVoice();
  };
  window.addEventListener('pagehide', function () {
    if (voice.on && navigator.sendBeacon) {
      navigator.sendBeacon('/api/projects/' + PID + '/voice', new Blob([JSON.stringify({ action: 'leave' })], { type: 'application/json' }));
    }
  });

  function showPanel(name) {
    $('right').classList.remove('hidden');
    document.querySelectorAll('.tab[data-panel]').forEach(function (b) { b.classList.toggle('active', b.dataset.panel === name); });
    document.querySelectorAll('.pane').forEach(function (p) { p.classList.toggle('hidden', p.id !== 'pane-' + name); });
  }
  document.querySelectorAll('.tab[data-panel]').forEach(function (b) { b.onclick = function () { showPanel(b.dataset.panel); }; });
  $('close-right').onclick = function () { $('right').classList.add('hidden'); };
  $('panel-btn').onclick = function () { if ($('right').classList.contains('hidden')) showPanel('chat'); else $('right').classList.add('hidden'); };
  $('room-code').onclick = function () {
    var code = state.project ? state.project.room_code : '';
    var el = $('room-code');
    function ok() { el.textContent = 'Nusxalandi ✓'; setTimeout(function () { el.textContent = '#' + code; }, 1200); }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(code).then(ok, function () { prompt('Jamoa kodi:', code); });
    else prompt('Jamoa kodi:', code);
  };

  function stopTermPoll() {
    if (termPollTimer) { clearInterval(termPollTimer); termPollTimer = null; }
    $('stop-term').style.display = 'none';
  }
  function startTermPoll() {
    stopTermPoll();
    $('stop-term').style.display = 'inline-block';
    termSince = 0;
    $('term-out').innerHTML = '';
    termPollTimer = setInterval(async function () {
      try {
        var r = await api('/api/projects/' + PID + '/terminal/poll', 'POST', { since: termSince });
        if (r.lines && r.lines.length) {
          r.lines.forEach(function (l) { term(l.text, l.err ? 'error' : 'log'); });
          termSince = r.since;
        }
        if (!r.running) stopTermPoll();
      } catch (e) { term('Polling xatosi', 'error'); }
    }, 500);
  }
  $('stop-term').onclick = async function () {
    try { await api('/api/projects/' + PID + '/terminal/stop', 'POST'); term('[To\'xtatildi]', 'sys'); stopTermPoll(); }
    catch (e) { alert(e.message); }
  };

  async function runCode() {
    if (running) return;
    var f = byId(state.active);
    if (!f) { term('Avval faylni oching', 'error'); return; }
    var ext = extOf(f.name);
    $('term-out').innerHTML = '';
    running = true;
    try {
      if (ext === 'html' || ext === 'htm' || ext === 'css') {
        showPanel('preview');
        $('preview-frame').src = '/api/projects/' + PID + '/preview/' + f.name;
        term('Veb-sahifa Preview panelida ochildi.', 'ok');
      } else {
        term('> python ' + f.name, 'sys');
        var r = await api('/api/projects/' + PID + '/terminal/run', 'POST', { filename: f.name });
        if (r.error) { term(r.error, 'error'); running = false; return; }
        startTermPoll();
      }
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
      renderTree();
      pickFirst();
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

PROJECT_TEMPLATES = {
    'web': {'name': 'Web sayt', 'icon': '🌐', 'lang': 'html', 'files': [
        ('index.html', '<!DOCTYPE html>\n<html>\n<head>\n  <meta charset="UTF-8">\n  <link rel="stylesheet" href="style.css">\n</head>\n<body>\n  <h1>Salom, CodeSync! 🚀</h1>\n  <button onclick="salomla()">Bos meni</button>\n  <script src="script.js"></script>\n</body>\n</html>'),
        ('style.css', 'body {\n  font-family: system-ui, sans-serif;\n  background: #1e1e1e;\n  color: white;\n  text-align: center;\n  padding: 50px;\n}'),
        ('script.js', 'function salomla() {\n  console.log("Salom! 👋");\n  document.querySelector("h1").textContent = "Ishladi! ✅";\n}')]},
    'python': {'name': 'Python', 'icon': '🐍', 'lang': 'python', 'files': [
        ('main.py', 'import time\n\ndef salom(ism):\n    return f"Salom, {ism}!"\n\nfor i in range(3):\n    print(salom("Dasturchi"), i)\n    time.sleep(1)\n')]},
    'flask': {'name': 'Flask App', 'icon': '🧪', 'lang': 'python', 'files': [
        ('app.py', 'from flask import Flask\n\napp = Flask(__name__)\n\n@app.route("/")\ndef home():\n    return "<h1>Salom, Flask 5001-portda ishlayapti!</h1>"\n\nif __name__ == "__main__":\n    app.run(host="0.0.0.0", port=5001)\n'),
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
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, owner_id INTEGER NOT NULL, room_code TEXT UNIQUE NOT NULL, language TEXT DEFAULT 'python', project_type TEXT DEFAULT 'web', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, name TEXT NOT NULL, content TEXT DEFAULT '', version INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS members (project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, last_seen REAL DEFAULT 0, PRIMARY KEY (project_id, user_id));
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT NOT NULL, text TEXT NOT NULL, created REAL);
        CREATE TABLE IF NOT EXISTS voice_members (project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT NOT NULL, last_seen REAL DEFAULT 0, PRIMARY KEY (project_id, user_id));
        CREATE TABLE IF NOT EXISTS voice_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, from_user INTEGER NOT NULL, to_user INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created REAL);
        CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
        CREATE INDEX IF NOT EXISTS idx_msg_project ON messages(project_id, id);
        CREATE INDEX IF NOT EXISTS idx_vsig_project ON voice_signals(project_id, to_user, id);
    ''')
    try:
        c.execute('ALTER TABLE files ADD COLUMN version INTEGER DEFAULT 1')
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
app.config.update(SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_HTTPONLY=True, MAX_CONTENT_LENGTH=2 * 1024 * 1024)
app.jinja_env.loader = DictLoader(TEMPLATES)
init_db()

login_manager = LoginManager(app)

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(uid):
    u = query('SELECT id, username FROM users WHERE id=?', (uid,), one=True)
    return User(u['id'], u['username']) if u else None

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
        if not USER_RE.match(u):
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
    return render_template('auth.html', mode='register', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
        row = query('SELECT * FROM users WHERE username=?', (u,), one=True)
        if row and verify_password(row, p):
            login_user(User(row['id'], row['username']), remember=True)
            return redirect(url_for('dashboard'))
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
    return render_template('preview.html', project_id=pid, project_name=p['name'])

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
        with tx() as c:
            c.execute('DELETE FROM files WHERE project_id=?', (pid,))
            c.execute('DELETE FROM messages WHERE project_id=?', (pid,))
            c.execute('DELETE FROM members WHERE project_id=?', (pid,))
            c.execute('DELETE FROM voice_members WHERE project_id=?', (pid,))
            c.execute('DELETE FROM voice_signals WHERE project_id=?', (pid,))
            c.execute('DELETE FROM projects WHERE id=?', (pid,))
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
    if since == 0:
        rows = list(reversed(query('SELECT id, username, text FROM messages WHERE project_id=? ORDER BY id DESC LIMIT 50', (pid,))))
    else:
        rows = query('SELECT id, username, text FROM messages WHERE project_id=? AND id>? ORDER BY id LIMIT 100', (pid, since))
    online = query('SELECT u.username FROM members m JOIN users u ON u.id=m.user_id WHERE m.project_id=? AND m.last_seen>?',
                   (pid, now - 8))
    return jsonify(files=files, chat=[dict(r) for r in rows], online=[r['username'] for r in online])

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
# API: TERMINAL & PROCESS MANAGEMENT
# ============================================================
def kill_process_tree(process):
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

@app.route('/api/projects/<int:pid>/terminal/run', methods=['POST'])
@login_required
def api_term_run(pid):
    p = get_project(pid)
    if not p:
        return jsonify(error='Topilmadi'), 404
    if pid in active_runs and active_runs[pid]['process'].poll() is None:
        return jsonify(error="Oldingi jarayon tugamagan. Kuting yoki to'xtating."), 400

    filename = ((request.get_json(silent=True) or {}).get('filename') or '').strip()
    if not filename:
        return jsonify(error='Fayl tanlanmagan'), 400

    pdir = os.path.join(SANDBOX, 'project_%d' % pid)
    if os.path.exists(pdir):
        shutil.rmtree(pdir, ignore_errors=True)
    os.makedirs(pdir, exist_ok=True)

    files = query('SELECT name, content FROM files WHERE project_id=?', (pid,))
    for f in files:
        fpath = os.path.join(pdir, f['name'])
        parent = os.path.dirname(fpath)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(fpath, 'w', encoding='utf-8') as fh:
            fh.write(f['content'] or '')

    req_file = os.path.join(pdir, 'requirements.txt')
    if os.path.exists(req_file):
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', req_file],
                           cwd=pdir, capture_output=True, timeout=60)
        except Exception:
            pass

    fpath = os.path.join(pdir, filename)
    if not os.path.exists(fpath):
        return jsonify(error='Fayl topilmadi'), 404

    try:
        process = subprocess.Popen(
            [sys.executable, fpath],
            cwd=pdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True
        )
        active_runs[pid] = {'process': process, 'output': [], 'since': 0}

        def read_output():
            try:
                for line in iter(process.stdout.readline, ''):
                    active_runs[pid]['output'].append({'text': line, 'err': False, 'idx': active_runs[pid]['since']})
                    active_runs[pid]['since'] += 1
                process.wait()
                active_runs[pid]['output'].append({'text': '\n[Jarayon tugadi. Kod: %s]\n' % process.returncode, 'err': True, 'idx': active_runs[pid]['since']})
                active_runs[pid]['since'] += 1
            except Exception:
                pass

        threading.Thread(target=read_output, daemon=True).start()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route('/api/projects/<int:pid>/terminal/poll', methods=['POST'])
@login_required
def api_term_poll(pid):
    if pid not in active_runs:
        return jsonify(lines=[], since=0, running=False)
    run = active_runs[pid]
    since = int((request.get_json(silent=True) or {}).get('since') or 0)
    lines = [l for l in run['output'] if l['idx'] >= since]
    running = run['process'].poll() is None
    return jsonify(lines=lines, since=run['since'], running=running)

@app.route('/api/projects/<int:pid>/terminal/stop', methods=['POST'])
@login_required
def api_term_stop(pid):
    if pid in active_runs:
        proc = active_runs[pid]['process']
        kill_process_tree(proc)
        try:
            active_runs[pid]['output'].append({'text': '\n[Server to\'xtatildi]\n', 'err': True, 'idx': active_runs[pid]['since']})
            active_runs[pid]['since'] += 1
        except Exception:
            pass
    return jsonify(ok=True)

# ============================================================
# API: PREVIEW
# ============================================================
@app.route('/api/projects/<int:pid>/preview/<path:filename>')
@login_required
def api_preview(pid, filename):
    if not get_project(pid):
        return 'Topilmadi', 404
    pdir = os.path.join(SANDBOX, 'project_%d' % pid)
    if not os.path.exists(pdir):
        os.makedirs(pdir, exist_ok=True)
        files = query('SELECT name, content FROM files WHERE project_id=?', (pid,))
        for f in files:
            fpath = os.path.join(pdir, f['name'])
            parent = os.path.dirname(fpath)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(fpath, 'w', encoding='utf-8') as fh:
                fh.write(f['content'] or '')

    fpath = os.path.join(pdir, filename)
    if not os.path.isfile(fpath):
        return 'Fayl topilmadi: %s' % filename, 404

    ext = filename.rsplit('.', 1)[-1].lower()
    mimes = {'html': 'text/html', 'css': 'text/css', 'js': 'application/javascript',
             'json': 'application/json', 'png': 'image/png', 'jpg': 'image/jpeg',
             'jpeg': 'image/jpeg', 'svg': 'image/svg+xml', 'gif': 'image/gif'}
    with open(fpath, 'rb') as f:
        content = f.read()
    return Response(content, mimetype=mimes.get(ext, 'text/plain'))

# ============================================================
# API: OVOZLI CHAT (WebRTC)
# ============================================================
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
        c.execute('DELETE FROM voice_members WHERE project_id=? AND last_seen < ?', (pid, now - 20))
    return jsonify(ok=True, ice=[{'urls': 'stun:stun.l.google.com:19302'}])

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
        c.execute('DELETE FROM voice_members WHERE project_id=? AND last_seen < ?', (pid, now - 20))
        c.execute('DELETE FROM voice_signals WHERE project_id=? AND created < ?', (pid, now - 30))
    peers = query('SELECT user_id AS id, username FROM voice_members WHERE project_id=? AND user_id!=?', (pid, uid))
    signals = query('SELECT id, from_user, kind, data FROM voice_signals WHERE project_id=? AND to_user=? AND id>? ORDER BY id', (pid, uid, since))
    for s in signals:
        with tx() as c:
            c.execute('DELETE FROM voice_signals WHERE id=?', (s['id'],))
    return jsonify(peers=[dict(p) for p in peers], signals=[dict(s) for s in signals],
                   ice=[{'urls': 'stun:stun.l.google.com:19302'}])

@app.route('/api/projects/<int:pid>/voice/signal', methods=['POST'])
@login_required
def api_voice_signal(pid):
    if not get_project(pid):
        return jsonify(error='Topilmadi'), 404
    d = request.get_json(silent=True) or {}
    to = d.get('to')
    kind = d.get('kind')
    data = d.get('data')
    if not to or not kind or not data:
        return jsonify(error='Xato signal'), 400
    with tx() as c:
        c.execute('INSERT INTO voice_signals (project_id, from_user, to_user, kind, data, created) VALUES (?,?,?,?,?,?)',
                  (pid, current_user.id, to, kind, data, time.time()))
    return jsonify(ok=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
