/* Knowledge hub: pages (with versions), lint, Knowledge PRs, index.md / log.md. */
(async function () {
  var me = await CG.init('knowledge.html', 'Knowledge Hub');
  var el = document.getElementById('content');
  var isOwner = me.role === 'knowledge_owner';
  var tab = CG.q('tab') || (CG.q('page') ? 'pages' : 'pages');
  var STATUS_CLASS = { approved: 'chip-green', active: 'chip-green', draft: 'chip-amber', expired: 'chip-slate', stale: 'chip-red' };
  var TYPES = ['policy', 'workflow', 'team', 'field', 'precedent', 'regulatory', 'runbook'];
  var STATUSES = ['approved', 'draft', 'expired', 'active', 'stale'];
  var pageState = { page: CG.q('page'), v: CG.q('v') };

  el.innerHTML = '<div class="mb-4"><div class="eyebrow">Second Brain</div><h1 class="font-headline-xl text-headline-xl">Knowledge hub</h1>' +
    '<p class="text-body-md text-slate-600">Versioned pages, lint findings and the Knowledge PRs that change them. Drafts are never cited.</p></div>' +
    '<div class="tabs" id="tabs"></div><div id="body"></div>';
  var TABS = [['pages', 'Pages'], ['lint', 'Lint report'], ['prs', 'Knowledge PRs'], ['index', 'index.md'], ['log', 'log.md']];
  function drawTabs() {
    document.getElementById('tabs').innerHTML = TABS.map(function (t) { return '<button type="button" class="tab ' + (t[0] === tab ? 'active' : '') + '" data-t="' + t[0] + '">' + t[1] + '</button>'; }).join('');
    CG.$$('#tabs .tab').forEach(function (b) { b.onclick = function () { tab = b.dataset.t; drawTabs(); show(); }; });
  }
  drawTabs();
  show();

  function show() {
    var body = document.getElementById('body');
    if (tab === 'pages') pagesTab(body);
    else if (tab === 'lint') lintTab(body);
    else if (tab === 'prs') prsTab(body);
    else fileTab(body, tab);
  }

  // ------------------------------------------------------------ pages
  async function pagesTab(body) {
    body.innerHTML = '<div class="grid grid-cols-1 xl:grid-cols-5 gap-4"><section class="card xl:col-span-2"><div class="flex flex-wrap gap-2 mb-3">' +
      '<select id="f-type" class="border border-slate-200 rounded-lg text-sm"><option value="">All types</option>' + TYPES.map(function (t) { return '<option>' + t + '</option>'; }).join('') + '</select>' +
      '<select id="f-status" class="border border-slate-200 rounded-lg text-sm"><option value="">All statuses</option>' + STATUSES.map(function (t) { return '<option>' + t + '</option>'; }).join('') + '</select>' +
      '<input id="f-q" type="search" placeholder="Search id or title" class="border border-slate-200 rounded-lg text-sm"/></div><div id="plist" style="max-height:70vh;overflow:auto"></div></section>' +
      '<section class="xl:col-span-3" id="pdetail"><div class="card">' + CG.empty('Select a page.') + '</div></section></div>';
    async function loadList() {
      var p = new URLSearchParams();
      [['type', 'f-type'], ['status', 'f-status'], ['q', 'f-q']].forEach(function (x) { var v = document.getElementById(x[1]).value.trim(); if (v) p.set(x[0], v); });
      var list = document.getElementById('plist');
      CG.loading(list);
      try {
        var rows = await CG_API.get('/api/pages' + (p.toString() ? '?' + p : ''));
        list.innerHTML = '<p class="text-body-sm text-slate-500 mb-2">' + rows.length + ' page(s)</p>' + rows.map(function (r) {
          return '<a href="#" data-id="' + CG.esc(r.id) + '" data-v="' + r.version + '" class="block p-2 rounded-lg border border-slate-200 mb-1 hover:bg-white"><div class="flex flex-wrap items-center gap-1"><span class="mono text-xs font-semibold">' +
            CG.esc(r.id) + ' v' + r.version + '</span>' + CG.chip(r.status === 'draft' ? 'DRAFT' : r.status, STATUS_CLASS[r.status] || 'chip-slate') + CG.chip(r.type, 'chip-slate') +
            '</div><div class="text-body-sm truncate">' + CG.esc(r.title) + '</div></a>';
        }).join('');
        CG.$$('#plist a').forEach(function (a) { a.onclick = function (ev) { ev.preventDefault(); openPage(a.dataset.id, a.dataset.v); }; });
      } catch (e) { CG.fail(e); }
    }
    ['f-type', 'f-status'].forEach(function (id) { document.getElementById(id).onchange = loadList; });
    document.getElementById('f-q').oninput = function () { clearTimeout(loadList.t); loadList.t = setTimeout(loadList, 250); };
    loadList();
    if (pageState.page) openPage(pageState.page, pageState.v);
  }

  async function openPage(id, v) {
    var box = document.getElementById('pdetail');
    CG.loading(box);
    try {
      var p = await CG_API.get('/api/pages/' + encodeURIComponent(id) + (v ? '?version=' + encodeURIComponent(v) : ''));
      var versions = p.versions.length > 1 ? '<div class="mt-3"><div class="eyebrow mb-1">Versions</div>' + p.versions.map(function (x) {
        return '<a href="#" data-v="' + x.version + '" class="chip ' + (STATUS_CLASS[x.status] || 'chip-slate') + '">v' + x.version + ' ' + CG.esc(x.status) + '</a>';
      }).join('') + (p.versions.length >= 2 ? ' <button id="cmp" type="button" class="btn btn-sm">Compare side by side</button>' : '') + '</div>' : '';
      box.innerHTML = '<div class="card">' + (p.status === 'draft' ? '<div class="p-3 rounded-xl mb-3" style="background:#fef3c7;color:#92400e"><b>DRAFT, never cited.</b> This page is not approved and cannot be used as a source.</div>' : '') +
        '<div class="flex flex-wrap items-center gap-2 mb-2"><h2 class="font-headline-md text-headline-md">' + CG.esc(p.id + ' v' + p.version) + '</h2>' + CG.chip(p.status, STATUS_CLASS[p.status] || 'chip-slate') + CG.chip(p.type, 'chip-slate') + '</div>' +
        '<div class="font-headline-sm text-headline-sm mb-2">' + CG.esc(p.title) + '</div>' +
        '<table class="data mb-3"><tbody>' + Object.keys(p.meta || {}).slice(0, 12).map(function (k) { return '<tr><td class="mono text-xs text-slate-500" style="width:11rem">' + CG.esc(k) + '</td><td class="text-body-sm">' + CG.esc(typeof p.meta[k] === 'object' ? JSON.stringify(p.meta[k]) : p.meta[k]) + '</td></tr>'; }).join('') +
        (p.effective_from ? '<tr><td class="mono text-xs text-slate-500">effective_from</td><td class="text-body-sm">' + CG.esc(p.effective_from) + '</td></tr>' : '') +
        (p.links && p.links.length ? '<tr><td class="mono text-xs text-slate-500">links</td><td class="text-body-sm">' + p.links.map(function (l) { return '<a class="chip chip-blue" href="' + CG.pageLink(l) + '">' + CG.esc(l) + '</a>'; }).join('') + '</td></tr>' : '') +
        '</tbody></table><div class="whitespace-pre-wrap text-body-sm">' + CG.esc(p.body) + '</div>' + versions + '<div id="cmp-box"></div></div>';
      CG.$$('#pdetail a[data-v]').forEach(function (a) { a.onclick = function (ev) { ev.preventDefault(); openPage(id, a.dataset.v); }; });
      var cmp = document.getElementById('cmp');
      if (cmp) cmp.onclick = async function () {
        var vs = p.versions.map(function (x) { return x.version; });
        var a = vs[vs.length - 2], b = vs[vs.length - 1];
        var both = await Promise.all([CG_API.get('/api/pages/' + encodeURIComponent(id) + '?version=' + a), CG_API.get('/api/pages/' + encodeURIComponent(id) + '?version=' + b)]);
        document.getElementById('cmp-box').innerHTML = '<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">' + both.map(function (x) {
          return '<div class="p-3 rounded-xl border border-slate-200"><div class="mb-1">' + CG.chip('v' + x.version + ' ' + x.status, STATUS_CLASS[x.status] || 'chip-slate') + '</div><div class="whitespace-pre-wrap text-body-sm">' + CG.esc(x.body) + '</div></div>';
        }).join('') + '</div>';
      };
    } catch (e) { box.innerHTML = '<div class="card">' + CG.empty(e.status === 404 ? 'That page does not exist.' : 'Could not load the page.') + '</div>'; }
  }

  // ------------------------------------------------------------ lint
  async function lintTab(body) {
    body.innerHTML = '<div class="flex justify-end mb-3"><button id="run-lint" class="btn btn-primary" type="button">Run lint</button></div><div id="lint-out"></div>';
    async function run() {
      var out = document.getElementById('lint-out');
      CG.loading(out, 'Running lint…');
      try {
        var f = await CG_API.get('/api/lint');
        var groups = [['error', 'Errors', '#fef2f2', '#991b1b'], ['warning', 'Warnings', '#fffbeb', '#92400e'], ['info', 'Info', '#f1f5f9', '#334155']];
        out.innerHTML = '<p class="text-body-sm text-slate-500 mb-2">' + f.length + ' finding(s)</p>' + groups.map(function (g) {
          var items = f.filter(function (x) { return x.severity === g[0]; });
          if (!items.length) return '';
          var inner = items.map(function (x) {
            return '<div class="p-3 rounded-xl mb-2" style="background:' + g[2] + ';color:' + g[3] + '"><div class="mb-1">' + CG.chip(x.code, g[0] === 'error' ? 'chip-red' : g[0] === 'warning' ? 'chip-amber' : 'chip-slate') +
              x.page_ids.map(function (i) { return '<a class="chip chip-blue" href="' + CG.pageLink(i) + '">' + CG.esc(i) + '</a>'; }).join('') + '</div><div class="text-body-sm">' + CG.esc(x.message) + '</div></div>';
          }).join('');
          return g[0] === 'info' ? '<details><summary class="font-headline-sm text-headline-sm mb-2 cursor-pointer">' + g[1] + ' (' + items.length + ')</summary>' + inner + '</details>' :
            '<h3 class="font-headline-sm text-headline-sm mb-2 mt-3">' + g[1] + ' (' + items.length + ')</h3>' + inner;
        }).join('');
      } catch (e) { CG.fail(e); }
    }
    document.getElementById('run-lint').onclick = run;
    run();
  }

  // ------------------------------------------------------------ PRs
  async function prsTab(body) {
    CG.loading(body);
    var prs;
    try { prs = await CG_API.get('/api/prs'); } catch (e) { CG.fail(e); body.innerHTML = CG.empty('Could not load PRs.'); return; }
    var open = prs.filter(function (p) { return p.status === 'open'; }), done = prs.filter(function (p) { return p.status !== 'open'; });
    body.innerHTML = (isOwner ? '' : '<div class="restricted-box mb-3"><span class="material-symbols-outlined">lock</span><span>Only the knowledge owner can approve or reject Knowledge PRs. You are ' + CG.esc(me.name + ' (' + me.role + ')') + '; this list is read-only.</span></div>') +
      '<h2 class="font-headline-md text-headline-md mb-2">Open PRs (' + open.length + ')</h2>' +
      (open.length ? open.map(function (p) {
        return '<div class="card mb-3"><div class="flex flex-wrap items-center gap-2 mb-1"><span class="mono font-semibold">' + CG.esc(p.id) + '</span>' + CG.chip('on ' + p.target_page_id + ' (base v' + p.base_version + ')', 'chip-blue') +
          '<span class="text-body-sm text-slate-500">by ' + CG.esc(p.author_id) + ' · ' + CG.esc(CG.time(p.created_at)) + '</span></div><p class="text-body-sm mb-2">' + CG.esc(p.reason) + '</p>' +
          (Object.keys(p.meta_changes || {}).length ? '<div class="mb-2"><span class="eyebrow">Structured change (from the reviewer)</span><div>' + Object.keys(p.meta_changes).map(function (k) { return CG.chip(k + ' = ' + p.meta_changes[k], 'chip-purple'); }).join('') + '</div></div>' : '') +
          CG.diffHtml(p.diff) + '<div class="flex gap-2 mt-3"><button class="btn btn-primary" data-act="approve" data-id="' + CG.esc(p.id) + '" ' + (isOwner ? '' : 'disabled title="Knowledge owner only"') + ' type="button">Approve</button>' +
          '<button class="btn btn-danger" data-act="reject" data-id="' + CG.esc(p.id) + '" ' + (isOwner ? '' : 'disabled title="Knowledge owner only"') + ' type="button">Reject</button></div></div>';
      }).join('') : '<div class="card mb-3">' + CG.empty('No open PRs. A reviewer can propose one while deciding a case.') + '</div>') +
      '<h2 class="font-headline-md text-headline-md mb-2 mt-4">Decided (' + done.length + ')</h2>' + (done.length ? '<div class="card"><table class="data"><thead><tr><th>PR</th><th>Page</th><th>Status</th><th>Decided by</th></tr></thead><tbody>' +
        done.map(function (p) { return '<tr><td class="mono">' + CG.esc(p.id) + '</td><td class="mono">' + CG.esc(p.target_page_id) + '</td><td>' + CG.chip(p.status, p.status === 'approved' ? 'chip-green' : 'chip-red') + '</td><td>' + CG.esc(p.decided_by) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '<div class="card">' + CG.empty('None yet.') + '</div>');
    CG.$$('button[data-act]', body).forEach(function (b) {
      b.onclick = async function () {
        var approve = b.dataset.act === 'approve';
        var ok = await CG.confirm((approve ? 'Approve ' : 'Reject ') + b.dataset.id + '?', approve ? 'A new page version is published (or the page is retired); dependent precedents become stale.' : 'The PR is closed and the page is left unchanged.', approve ? 'Approve' : 'Reject');
        if (!ok) return;
        try { await CG_API.post('/api/prs/' + encodeURIComponent(b.dataset.id) + '/decision', { approve: approve }); CG.toast(b.dataset.id + (approve ? ' approved.' : ' rejected.'), 'ok'); prsTab(body); } catch (e) { CG.fail(e); }
      };
    });
  }

  // ------------------------------------------------------------ index.md / log.md
  async function fileTab(body, name) {
    CG.loading(body);
    try {
      var f = await CG_API.get('/api/brain/' + name);
      body.innerHTML = '<div class="card"><div class="eyebrow mb-2">' + CG.esc(f.name) + '</div><pre class="mono text-xs whitespace-pre-wrap break-words">' + CG.esc(f.text) + '</pre></div>';
    } catch (e) { CG.fail(e); }
  }
})();
