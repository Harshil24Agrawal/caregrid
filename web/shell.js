/* CareGrid web UI shell: sidebar (user switcher, LLM indicator, Reset demo), header, toasts, and small presentation helpers.
   Presentation only: every number and decision shown by the pages comes from the API. */
(function () {
  var NAV = [
    ['index.html', 'dashboard', 'Dashboard'],
    ['intake.html', 'add_task', '1 New Request'],
    ['case.html', 'psychology', '2 Case Intelligence'],
    ['approval.html', 'gavel', '3 Approval (Handoff)'],
    ['audit.html', 'lock_clock', '4 Audit Log'],
    ['knowledge.html', 'local_library', '5 Knowledge Hub'],
    ['comms.html', 'send', '6 Communications']
  ];
  var HARD = { CLINICAL: 1, ACCOUNT_SPECIFIC: 1, SENSITIVE: 1, IRREVERSIBLE_ACTION: 1, ACCESS_DENIED: 1 };
  var STATE_CLASS = { answered: 'chip-teal', needs_info: 'chip-amber', in_review: 'chip-blue', escalated: 'chip-orange', approved: 'chip-green',
    actioned: 'chip-purple', notified: 'chip-purple', rejected: 'chip-red', closed: 'chip-slate', new: 'chip-slate', classified: 'chip-slate',
    ready: 'chip-slate', proposed: 'chip-slate' };
  var RISK_CLASS = { low: 'chip-slate', medium: 'chip-amber', high: 'chip-orange', critical: 'chip-red' };
  var BAND_CLASS = { high: 'chip-green', medium: 'chip-amber', low: 'chip-red' };

  var CG = window.CG = {};
  CG.users = [];
  CG.config = null;
  CG.me = null;

  // ---------------------------------------------------------------- helpers
  CG.esc = function (s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  CG.chip = function (label, cls) { return '<span class="chip ' + (cls || 'chip-slate') + '">' + CG.esc(label) + '</span>'; };
  CG.stateChip = function (s) { return CG.chip(s, STATE_CLASS[s] || 'chip-slate'); };
  CG.riskChip = function (r) { return r ? CG.chip('RISK ' + String(r).toUpperCase(), RISK_CLASS[r] || 'chip-slate') : ''; };
  CG.bandChip = function (b, score) {
    return b ? CG.chip(String(b).toUpperCase() + (score !== undefined && score !== null ? ' ' + score : ''), BAND_CLASS[b] || 'chip-slate') : '';
  };
  CG.reasonChips = function (codes) {
    return (codes || []).map(function (c) { return CG.chip(c, HARD[c] ? 'chip-red' : 'chip-slate'); }).join('');
  };
  CG.routingText = function (c) {
    return c.routing === 'auto' ? 'AUTO (with audit)' : 'HUMAN → ' + (c.assigned_team || c.team || '?');
  };
  CG.trustText = function (t) { return t ? t.level + ' ' + t.label : ''; };
  CG.age = function (h) { return h === null || h === undefined ? '' : (h < 48 ? h.toFixed(1) + ' h' : (h / 24).toFixed(1) + ' d'); };
  CG.time = function (iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };
  CG.initials = function (name) { return (name || '?').trim().slice(0, 2).toUpperCase(); };
  CG.avatar = function (name) { return '<span class="avatar">' + CG.esc(CG.initials(name)) + '</span>'; };
  CG.caseLink = function (id, label) { return '<a class="mono font-semibold text-secondary hover:underline" href="case.html?case=' + encodeURIComponent(id) + '">' + CG.esc(label || id) + '</a>'; };
  CG.pageLink = function (id, version) {
    return 'knowledge.html?page=' + encodeURIComponent(id) + (version ? '&v=' + encodeURIComponent(version) : '');
  };
  CG.citationChip = function (c) {
    var label = c.page_id + (c.version ? ' v' + c.version : '') + ' · ' + c.page_type + ' · ' + c.title;
    return '<a class="chip chip-blue" href="' + CG.pageLink(c.page_id, c.version) + '" title="Open in the Knowledge Hub">' + CG.esc(label) + '</a>';
  };
  CG.restricted = function (r) {
    var msg = (r && r.message) || "ACCESS RESTRICTED: you don't have permission to view this information.";
    return '<div class="restricted-box"><span class="material-symbols-outlined">lock</span><span>' + CG.esc(msg) + '</span></div>';
  };
  CG.isRestricted = function (v) { return !!(v && v.restricted); };
  CG.empty = function (text) { return '<p class="text-body-sm text-slate-500">' + CG.esc(text) + '</p>'; };
  CG.q = function (name) { return new URLSearchParams(window.location.search).get(name); };
  CG.$ = function (sel, root) { return (root || document).querySelector(sel); };
  CG.$$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  CG.loading = function (el, text) { el.innerHTML = '<div class="flex items-center gap-2 text-slate-500 text-sm"><span class="spinner"></span>' + CG.esc(text || 'Loading…') + '</div>'; };

  CG.confidenceBar = function (conf) {
    if (!conf || !conf.breakdown) return '';
    var parts = [['policy', 30, '#2b6cb0'], ['precedent', 25, '#6b46c1'], ['fields', 20, '#2f855a'], ['clarity', 15, '#d69e2e'], ['no_conflict', 10, '#718096']];
    var segs = parts.map(function (p) {
      var v = conf.breakdown[p[0]] || 0;
      return '<div class="seg" style="flex:' + p[1] + ';background:' + p[2] + ';opacity:' + (0.3 + 0.7 * v / p[1]).toFixed(2) + '" title="' + p[0] + ' ' + v + '/' + p[1] + '">' + v + '</div>';
    }).join('');
    var legend = '<div class="mt-2 text-body-sm text-slate-600 flex flex-wrap gap-x-3">' + parts.map(function (p) {
      return '<span><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:' + p[2] + '"></span> ' + p[0].replace('no_conflict', 'conflict') + ' ' + (conf.breakdown[p[0]] || 0) + '/' + p[1] + '</span>';
    }).join('') + '</div>';
    var note = conf.capped_at_medium ? '<p class="text-body-sm mt-2" style="color:#92400e">Band capped at Medium: conflicting evidence.</p>' : '';
    return '<div class="flex items-baseline gap-3 mb-2"><span class="font-label-numeric-score text-label-numeric-score text-3xl font-bold">' + conf.score +
      '</span><span class="text-slate-500">/ 100</span>' + CG.bandChip(conf.band) + '</div><div class="seg-bar">' + segs + '</div>' + legend + note +
      '<p class="text-body-sm text-slate-600 mt-2">' + CG.esc(conf.explanation || '') + '</p>';
  };

  CG.diffHtml = function (diff) {
    if (!diff) return '<p class="text-body-sm text-slate-500">No text change (structured change only).</p>';
    return '<pre class="diff">' + diff.split('\n').map(function (l) {
      var cls = l.indexOf('+++') === 0 || l.indexOf('---') === 0 ? 'diff-hunk' : l[0] === '+' ? 'diff-add' : l[0] === '-' ? 'diff-del' : l[0] === '@' ? 'diff-hunk' : '';
      return '<div class="' + cls + '">' + CG.esc(l || ' ') + '</div>';
    }).join('') + '</pre>';
  };

  // ---------------------------------------------------------------- toasts and modal
  CG.toast = function (msg, kind) {
    var root = document.getElementById('toasts');
    if (!root) return;
    var el = document.createElement('div');
    el.className = 'toast ' + (kind === 'error' ? 'toast-error' : kind === 'ok' ? 'toast-ok' : '');
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, kind === 'error' ? 7000 : 4500);
  };
  CG.fail = function (err) {
    CG.toast((err && err.status === 403 ? 'Not allowed: ' : '') + (err && err.message ? err.message : 'Something went wrong.'), 'error');
  };
  CG.confirm = function (title, text, okLabel) {
    return new Promise(function (resolve) {
      var root = document.getElementById('modal-root');
      root.innerHTML = '<div class="backdrop"><div class="dialog"><h3 class="font-headline-sm text-headline-sm mb-2">' + CG.esc(title) + '</h3>' +
        '<p class="text-body-sm text-slate-600 mb-4">' + CG.esc(text) + '</p><div class="flex justify-end gap-2">' +
        '<button class="btn" id="m-cancel" type="button">Cancel</button><button class="btn btn-primary" id="m-ok" type="button">' + CG.esc(okLabel || 'Confirm') + '</button></div></div></div>';
      function done(v) { root.innerHTML = ''; resolve(v); }
      document.getElementById('m-cancel').onclick = function () { done(false); };
      document.getElementById('m-ok').onclick = function () { done(true); };
    });
  };

  // ---------------------------------------------------------------- prefs (UI only)
  function pref(key, value) {
    try { if (value === undefined) return window.localStorage.getItem(key); window.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
    return null;
  }

  // ---------------------------------------------------------------- shell
  function navHtml(active) {
    return NAV.map(function (n) {
      return '<a class="nav-item flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-medium transition-all' + (n[0] === active ? ' active-nav' : '') +
        '" href="' + n[0] + '" title="' + CG.esc(n[2]) + '"><span class="material-symbols-outlined text-lg shrink-0">' + n[1] +
        '</span><span class="sidebar-text truncate">' + CG.esc(n[2]) + '</span></a>';
    }).join('');
  }

  function render(active, title) {
    var me = CG.me, cfg = CG.config;
    var llm = cfg ? cfg.llm_provider : '?';
    var llmCls = llm === 'mock' ? 'bg-emerald-50 text-emerald-700' : 'bg-sky-50 text-sky-700';
    var aside = document.createElement('aside');
    aside.id = 'app-sidebar';
    aside.className = 'fixed left-0 top-0 h-screen z-50 flex flex-col justify-between';
    aside.innerHTML =
      '<div class="flex flex-col min-h-0">' +
      '<div class="h-16 px-5 flex items-center border-b border-slate-100/90 shrink-0"><a href="index.html" class="flex items-center gap-2 w-full overflow-hidden" title="CareGrid">' +
      '<div class="brand-full flex flex-col min-w-0"><span class="font-extrabold text-xl text-slate-900 tracking-tight leading-none">CareGrid</span>' +
      '<span class="font-mono text-[10px] font-semibold text-slate-500 tracking-widest mt-1">CLINICAL OPS</span></div>' +
      '<div class="brand-mini w-full flex items-center justify-center"><span class="font-black text-xl text-slate-900">CG</span></div></a></div>' +
      '<div class="px-3 py-3 shrink-0"><button type="button" id="user-switch" class="acting-as-box w-full text-left p-2.5 rounded-xl bg-slate-50 border border-slate-200/80 shadow-sm flex items-center gap-2.5 overflow-hidden cursor-pointer" title="Switch the acting user">' +
      CG.avatar(me.name) + '<div class="sidebar-details flex flex-col min-w-0 flex-1 overflow-hidden"><span class="text-[9px] font-mono font-semibold uppercase text-slate-400 tracking-wider">Acting as</span>' +
      '<span class="font-semibold text-xs text-slate-900 truncate leading-tight">' + CG.esc(me.name) + '</span>' +
      '<span class="font-mono text-[10px] text-slate-500 truncate">' + CG.esc(me.role + (me.team ? ' · ' + me.team : '')) + '</span></div>' +
      '<span class="material-symbols-outlined text-slate-400 text-sm shrink-0 unfold-icon">unfold_more</span></button></div>' +
      '<nav class="flex-1 px-3 py-2 space-y-1 overflow-y-auto overflow-x-hidden">' + navHtml(active) + '</nav></div>' +
      '<div class="p-3 border-t border-slate-100/90 flex flex-col gap-2 shrink-0">' +
      '<div class="pipeline-box p-2 rounded-lg bg-slate-50 border border-slate-200/70 flex items-center justify-between text-[11px] font-mono text-slate-500" title="' +
      CG.esc(cfg ? 'light: ' + cfg.models.light + ' / strong: ' + cfg.models.strong : '') + '"><span class="uppercase tracking-wider text-[10px]">Pipeline</span>' +
      '<span class="px-1.5 py-0.5 rounded font-semibold text-[10px] ' + llmCls + '">LLM: ' + CG.esc(llm) + '</span></div>' +
      '<button type="button" id="reset-demo" class="reset-btn w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-slate-50 hover:bg-slate-100 text-slate-700 text-xs font-medium border border-slate-200/80 cursor-pointer shadow-sm" title="Reset demo">' +
      '<span class="material-symbols-outlined text-base text-amber-600 shrink-0">restart_alt</span><span class="sidebar-text truncate">Reset demo</span></button></div>';
    var wrapper = document.getElementById('main-wrapper');
    document.body.insertBefore(aside, wrapper);
    var header = document.createElement('header');
    header.id = 'app-header';
    header.className = 'fixed top-0 left-64 right-0 h-16 z-40 flex items-center justify-between px-6';
    header.innerHTML =
      '<div class="flex items-center gap-3"><button id="sidebar-toggle-btn" type="button" aria-label="Toggle sidebar" title="Toggle sidebar">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.8" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg></button>' +
      '<div class="flex items-center gap-2 text-sm text-slate-500 font-medium"><a href="index.html" class="hover:text-slate-900">CareGrid</a><span class="text-slate-300">/</span>' +
      '<span class="font-semibold text-slate-900">' + CG.esc(title) + '</span></div></div>' +
      '<div class="flex items-center gap-2"><span class="chip chip-slate" title="Demo authentication: the acting user is sent as a header (not real login)">' +
      CG.esc(me.name + ' · ' + me.role) + '</span></div>';
    wrapper.insertBefore(header, wrapper.firstChild);

    // sidebar collapse (UI pref)
    if (pref('cg_sidebar') === 'collapsed') document.body.classList.add('sidebar-collapsed');
    document.getElementById('sidebar-toggle-btn').onclick = function () {
      document.body.classList.toggle('sidebar-collapsed');
      pref('cg_sidebar', document.body.classList.contains('sidebar-collapsed') ? 'collapsed' : 'open');
    };
    window.toggleSidebar = function () { document.getElementById('sidebar-toggle-btn').click(); };

    // user switcher
    document.getElementById('user-switch').onclick = function (ev) {
      ev.stopPropagation();
      var existing = document.getElementById('user-menu');
      if (existing) { existing.remove(); return; }
      var menu = document.createElement('div');
      menu.id = 'user-menu';
      menu.className = 'user-menu';
      menu.innerHTML = CG.users.map(function (u) {
        return '<button type="button" data-uid="' + CG.esc(u.id) + '" class="' + (u.id === me.id ? 'on' : '') + '">' + CG.avatar(u.name) +
          '<span class="flex flex-col min-w-0"><span class="text-xs font-semibold text-slate-900">' + CG.esc(u.name) + '</span><span class="font-mono text-[10px] text-slate-500">' +
          CG.esc(u.role + (u.team ? ' · ' + u.team : '')) + '</span></span></button>';
      }).join('');
      aside.appendChild(menu);
      CG.$$('button', menu).forEach(function (b) {
        b.onclick = function () { CG_API.setUser(b.getAttribute('data-uid')); window.location.reload(); };
      });
    };
    document.addEventListener('click', function () { var m = document.getElementById('user-menu'); if (m) m.remove(); });

    // reset demo (confirm)
    document.getElementById('reset-demo').onclick = async function () {
      var ok = await CG.confirm('Reset the demo?', 'This wipes the database and the learned precedents, recompiles the Second Brain and re-seeds the demo cases (CASE-1024 and history). It cannot be undone.', 'Reset demo');
      if (!ok) return;
      try {
        CG.toast('Resetting…');
        await CG_API.post('/api/reset');
        CG.toast('Demo reset.', 'ok');
        setTimeout(function () { window.location.href = 'index.html'; }, 600);
      } catch (e) { CG.fail(e); }
    };
  }

  /* CG.init('case.html', 'Case Intelligence') -> resolves once users, config and the acting user are loaded and the shell is drawn. */
  CG.init = async function (active, title) {
    if (!document.getElementById('toasts')) {
      var t = document.createElement('div'); t.id = 'toasts'; document.body.appendChild(t);
      var m = document.createElement('div'); m.id = 'modal-root'; document.body.appendChild(m);
    }
    try {
      var results = await Promise.all([CG_API.get('/api/users'), CG_API.get('/api/config')]);
      CG.users = results[0];
      CG.config = results[1];
    } catch (e) {
      document.getElementById('content').innerHTML = '<div class="card"><h2 class="font-headline-md">The CareGrid API is not reachable</h2>' +
        '<p class="text-body-sm text-slate-600 mt-2">Start it with <code class="mono">python -m caregrid.cli serve</code> and open <code class="mono">http://127.0.0.1:8000/</code>.</p></div>';
      throw e;
    }
    var uid = CG_API.getUser();
    CG.me = CG.users.filter(function (u) { return u.id === uid; })[0] || CG.users[0];
    CG_API.setUser(CG.me.id);
    render(active, title);
    return CG.me;
  };
})();
