/* Knowledge: one question - what does the Second Brain say, and what is wrong with it? Pages (with versions), needs-attention (lint), change requests. */
(async function () {
  var me = await CG.init('knowledge.html');
  var el = document.getElementById('content');
  var isOwner = me.role === 'knowledge_owner';
  var tab = CG.q('tab') || 'pages';
  var STATUS = { approved: ['Approved', 'green'], active: ['Approved', 'green'], draft: ['DRAFT', 'amber'], expired: ['Expired', 'grey'], stale: ['Stale', 'grey'] };
  var TYPE_NAME = { policy: 'Policies', workflow: 'Workflows', team: 'Teams', field: 'Fields', precedent: 'Past decisions', regulatory: 'Regulatory', runbook: 'Runbooks' };
  var LINT = { CONTRADICTION: 'Policies disagree', EXPIRED_LINKED: 'Expired policy still linked', STALE_PRECEDENT: 'Past decision uses an old policy version', ORPHAN: 'Page nobody links to',
    MISSING_TEAM: 'Team is missing', ESCALATION_HOTSPOT: 'Many escalations on one topic', PII_LEAK: 'Personal data found' };
  var statusTag = function (s) { var x = STATUS[s] || [CG.human(s), 'grey']; return CG.tag(x[0], x[1], s); };

  el.innerHTML = '<div class="page-head"><h1>Knowledge</h1><p class="muted">The pages the assistant may cite. Drafts are never cited.</p></div><div class="tabs" id="tabs" role="tablist"></div><div id="body"></div>';
  var TABS = [['pages', 'Pages'], ['lint', 'Needs attention'], ['prs', 'Change requests']];
  function drawTabs() {
    document.getElementById('tabs').innerHTML = TABS.map(function (t) { return '<button type="button" role="tab" class="tab ' + (t[0] === tab ? 'on' : '') + '" data-t="' + t[0] + '" aria-selected="' + (t[0] === tab) + '">' + t[1] + '</button>'; }).join('');
    CG.$$('#tabs .tab').forEach(function (b) { b.onclick = function () { tab = b.dataset.t; drawTabs(); show(); }; });
  }
  drawTabs(); show();
  function show() { var b = document.getElementById('body'); if (tab === 'pages') pagesTab(b); else if (tab === 'lint') lintTab(b); else prsTab(b); }

  // ------------------------------------------------------------ pages
  async function pagesTab(body) {
    body.innerHTML = '<div class="grid g-kb"><div class="card"><div class="flex" style="margin-bottom:8px"><input id="f-q" type="search" placeholder="Search pages" aria-label="Search pages" style="flex:1"><select id="f-status" aria-label="Status"><option value="">All</option><option value="approved">Approved</option><option value="draft">Draft</option><option value="expired">Expired</option><option value="stale">Stale</option></select></div><div id="plist" class="scroll"></div></div><div id="pdetail"><div class="card">' + CG.empty('Pick a page from the list.') + '</div></div></div>';
    var cur = CG.q('page'), curV = CG.q('v');
    async function loadList() {
      var p = new URLSearchParams();
      var q = document.getElementById('f-q').value.trim(), s = document.getElementById('f-status').value;
      if (q) p.set('q', q); if (s) p.set('status', s);
      var list = document.getElementById('plist');
      try {
        var rows = await CG_API.get('/api/pages' + (p.toString() ? '?' + p : ''));
        var groups = {};
        rows.forEach(function (r) { (groups[r.type] = groups[r.type] || []).push(r); });
        list.innerHTML = Object.keys(TYPE_NAME).filter(function (t) { return groups[t]; }).map(function (t) {
          return '<div class="grouphead">' + TYPE_NAME[t] + ' (' + groups[t].length + ')</div>' + groups[t].map(function (r) {
            return '<a href="#" class="listitem' + (r.id === cur && (curV ? String(r.version) === String(curV) : r.status !== 'expired') ? ' on' : '') + '" data-id="' + CG.esc(r.id) + '" data-v="' + r.version + '"><span class="t"><b class="mono small">' + CG.esc(r.id) + ' v' + r.version + '</b> ' + CG.esc(r.title) + '</span>' + statusTag(r.status) + '</a>';
          }).join('');
        }).join('') || CG.empty('No pages match.');
        CG.$$('#plist a').forEach(function (a) { a.onclick = function (ev) { ev.preventDefault(); cur = a.dataset.id; curV = a.dataset.v; CG.$$('#plist a').forEach(function (x) { x.classList.remove('on'); }); a.classList.add('on'); openPage(a.dataset.id, a.dataset.v); }; });
      } catch (e) { CG.fail(e); }
    }
    document.getElementById('f-status').onchange = loadList;
    document.getElementById('f-q').oninput = function () { clearTimeout(loadList.t); loadList.t = setTimeout(loadList, 250); };
    loadList();
    if (cur) openPage(cur, CG.q('v'));
  }

  async function openPage(id, v) {
    var box = document.getElementById('pdetail');
    CG.loading(box);
    try {
      var p = await CG_API.get('/api/pages/' + encodeURIComponent(id) + (v ? '?version=' + encodeURIComponent(v) : ''));
      var meta = Object.keys(p.meta || {}).slice(0, 8).map(function (k) { return '<div class="row"><div class="label">' + CG.esc(CG.human(k)) + '</div><div class="small">' + CG.esc(typeof p.meta[k] === 'object' ? JSON.stringify(p.meta[k]) : p.meta[k]) + '</div></div>'; }).join('');
      var versions = p.versions.length > 1 ? '<div class="flex" style="margin:10px 0"><span class="label">Versions</span>' + p.versions.map(function (x) {
        return '<a href="#" class="chip' + (x.version === p.version ? ' on' : '') + '" data-v="' + x.version + '">v' + x.version + ' · ' + CG.esc(CG.human(x.status)) + '</a>'; }).join('') +
        '<button id="cmp" type="button" class="btn sm">Compare v' + p.versions[p.versions.length - 2].version + ' → v' + p.versions[p.versions.length - 1].version + '</button></div>' : '';
      box.innerHTML = '<div class="card">' + (p.status === 'draft' ? '<div class="callout amber"><b>Draft: never cited.</b> This page is not approved and cannot be used as a source.</div>' : '') +
        '<div class="flex"><h2 class="mono">' + CG.esc(p.id + ' v' + p.version) + '</h2>' + statusTag(p.status) + CG.tag(TYPE_NAME[p.type] || p.type, 'grey') + '</div><h3 style="margin:6px 0 10px">' + CG.esc(p.title) + '</h3>' + versions +
        '<div style="white-space:pre-wrap">' + CG.esc(p.body) + '</div>' + (meta ? '<div class="rows" style="margin-top:12px">' + meta + '</div>' : '') +
        (p.links && p.links.length ? '<div style="margin-top:10px"><span class="label">Links</span> ' + p.links.map(function (l) { return '<a class="chip" href="' + CG.pageLink(l) + '">' + CG.esc(l) + '</a>'; }).join('') + '</div>' : '') + '<div id="cmp-box"></div></div>';
      CG.$$('#pdetail a[data-v]').forEach(function (a) { a.onclick = function (ev) { ev.preventDefault(); openPage(id, a.dataset.v); }; });
      var cmp = document.getElementById('cmp');
      if (cmp) cmp.onclick = async function () {
        var box2 = document.getElementById('cmp-box');
        if (box2.innerHTML) { box2.innerHTML = ''; return; }
        var vs = p.versions.map(function (x) { return x.version; });
        var both = await Promise.all(vs.slice(-2).map(function (n) { return CG_API.get('/api/pages/' + encodeURIComponent(id) + '?version=' + n); }));
        box2.innerHTML = '<div class="grid g2" style="margin-top:12px">' + both.map(function (x) {
          return '<div class="card"><div class="flex" style="margin-bottom:6px"><b>v' + x.version + '</b>' + statusTag(x.status) + '</div><div class="small" style="white-space:pre-wrap">' + CG.esc(x.body) + '</div></div>'; }).join('') + '</div>';
      };
    } catch (e) { box.innerHTML = '<div class="card">' + CG.empty(e.status === 404 ? 'That page does not exist.' : 'Could not load the page.') + '</div>'; }
  }

  // ------------------------------------------------------------ needs attention (lint)
  async function lintTab(body) {
    CG.loading(body, 'Checking the Second Brain…');
    try {
      var f = await CG_API.get('/api/lint');
      function card(x) {
        var kind = x.code === 'CONTRADICTION' ? ['Conflict', 'red'] : x.severity === 'error' ? ['Error', 'red'] : x.severity === 'warning' ? ['Warning', 'amber'] : ['Info', 'grey'];
        return '<div class="att ' + kind[1] + '"><div class="top">' + CG.tag(kind[0], kind[1]) + '<span class="small muted" title="' + CG.esc(x.code) + '">' + CG.esc(LINT[x.code] || CG.human(x.code)) + '</span></div><div class="small">' + CG.esc(x.message) + '</div>' +
          '<div style="margin-top:4px">' + x.page_ids.map(function (i) { return '<a class="chip" href="' + CG.pageLink(i) + '">' + CG.esc(i) + '</a>'; }).join('') + '</div></div>';
      }
      var main = f.filter(function (x) { return x.severity !== 'info'; }), info = f.filter(function (x) { return x.severity === 'info'; });
      body.innerHTML = '<div class="card"><div class="card-title"><h2>Needs attention (' + main.length + ')</h2><button class="btn sm" id="rerun" type="button">Run check again</button></div>' +
        (main.length ? main.map(card).join('') : CG.empty('Nothing is wrong. The Second Brain has no conflicts or stale links.')) + '</div>' +
        (info.length ? '<details class="fold"><summary>Notices (' + info.length + ')</summary><div class="fold-body">' + info.map(card).join('') + '</div></details>' : '');
      document.getElementById('rerun').onclick = function () { lintTab(body); };
    } catch (e) { CG.fail(e); }
  }

  // ------------------------------------------------------------ change requests (PRs)
  async function prsTab(body) {
    CG.loading(body);
    var prs;
    try { prs = await CG_API.get('/api/prs'); } catch (e) { CG.fail(e); body.innerHTML = CG.empty('Could not load change requests.'); return; }
    var open = prs.filter(function (p) { return p.status === 'open'; }), done = prs.filter(function (p) { return p.status !== 'open'; });
    body.innerHTML = (isOwner ? '' : '<div class="lock" style="margin-bottom:12px"><span aria-hidden="true">🔒</span><span>Only the knowledge owner approves or rejects change requests. You can read them.</span></div>') +
      '<div class="card"><div class="card-title"><h2>Open change requests (' + open.length + ')</h2></div>' + (open.length ? open.map(function (p) {
        return '<div class="att blue" style="cursor:default"><div class="top"><span><b class="mono">' + CG.esc(p.id) + '</b> on <a class="mono" href="' + CG.pageLink(p.target_page_id) + '">' + CG.esc(p.target_page_id) + '</a> (v' + p.base_version + ')</span><span class="small muted">by ' + CG.esc(p.author_id) + ' · ' + CG.esc(CG.time(p.created_at)) + '</span></div>' +
          '<p class="small" style="margin-bottom:6px">' + CG.esc(p.reason) + '</p>' + (Object.keys(p.meta_changes || {}).length ? '<div style="margin-bottom:6px">' + Object.keys(p.meta_changes).map(function (k) { return '<span class="chip amber">' + CG.esc(k === 'retire' ? 'Retire this policy' : k === 'target_page' ? 'Policy: ' + p.meta_changes[k] : CG.human(k) + ': ' + p.meta_changes[k]) + '</span>'; }).join('') + '</div>' : '') +
          CG.diffHtml(p.diff) + '<div class="flex" style="margin-top:10px"><button class="btn primary" data-act="approve" data-id="' + CG.esc(p.id) + '" type="button"' + (isOwner ? '' : ' disabled title="Knowledge owner only"') + '>Approve</button><button class="btn danger" data-act="reject" data-id="' + CG.esc(p.id) + '" type="button"' + (isOwner ? '' : ' disabled title="Knowledge owner only"') + '>Reject</button></div></div>';
      }).join('') : CG.empty('No open change requests. A reviewer can propose one while deciding a case.')) + '</div>' +
      (done.length ? '<div class="card"><div class="card-title"><h2>Decided (' + done.length + ')</h2></div><table><tbody>' + done.map(function (p) {
        return '<tr><td class="mono">' + CG.esc(p.id) + '</td><td class="mono">' + CG.esc(p.target_page_id) + '</td><td>' + CG.tag(CG.human(p.status), p.status === 'approved' ? 'green' : 'red') + '</td><td class="small">' + CG.esc(p.decided_by) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '');
    CG.$$('button[data-act]', body).forEach(function (b) {
      b.onclick = async function () {
        var approve = b.dataset.act === 'approve';
        var ok = await CG.confirm((approve ? 'Approve ' : 'Reject ') + b.dataset.id + '?', approve ? 'A new page version is published (or the page is retired) and dependent past decisions become stale.' : 'The request is closed and the page is left unchanged.', approve ? 'Approve' : 'Reject');
        if (!ok) return;
        try { await CG_API.post('/api/prs/' + encodeURIComponent(b.dataset.id) + '/decision', { approve: approve }); CG.toast(b.dataset.id + (approve ? ' approved.' : ' rejected.'), 'ok'); prsTab(body); } catch (e) { CG.fail(e); }
      };
    });
  }
})();
