/* Case intelligence: header, overview, recommendation, evidence, audit, graph and the assistant. Data from /api/cases/{id}[...]. */
(async function () {
  var me = await CG.init('case.html', 'Case Intelligence');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading cases…');
  var list;
  try { list = await CG_API.get('/api/cases'); } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load cases.'); return; }
  if (!list.length) { el.innerHTML = '<div class="card">' + CG.empty('No cases are visible to you yet.') + '</div>'; return; }

  var wanted = CG.q('case');
  var ids = list.map(function (c) { return c.id; });
  var current = wanted || ids[0];
  var denied = wanted && ids.indexOf(wanted) < 0;

  el.innerHTML =
    '<div class="flex flex-wrap items-end justify-between gap-3 mb-4"><div><div class="eyebrow">Case Intelligence</div><h1 class="font-headline-xl text-headline-xl">Case dossier</h1></div>' +
    '<label class="text-body-sm text-slate-600">Case <select id="case-pick" class="border border-slate-200 rounded-lg text-sm" style="min-width:20rem">' +
    list.map(function (c) { return '<option value="' + CG.esc(c.id) + '">' + CG.esc(c.id + ' · ' + c.request_type + ' · ' + c.state) + '</option>'; }).join('') + '</select></label></div>' +
    '<div id="denied"></div><div id="case-root"></div>';
  var pick = document.getElementById('case-pick');
  if (!denied) pick.value = current;
  pick.onchange = function () { window.history.replaceState(null, '', 'case.html?case=' + encodeURIComponent(pick.value)); load(pick.value); };
  load(denied ? ids[0] : current);

  var tab = 'overview';
  var data = null;

  async function load(id) {
    var root = document.getElementById('case-root');
    var dn = document.getElementById('denied');
    dn.innerHTML = denied && wanted ? '<div class="card mb-4">' + CG.restricted({ message: "ACCESS RESTRICTED: " + wanted + " is not visible to your role." }) + '</div>' : '';
    CG.loading(root, 'Loading ' + id + '…');
    try { data = await CG_API.get('/api/cases/' + encodeURIComponent(id)); } catch (e) { root.innerHTML = '<div class="card">' + CG.restricted() + '</div>'; CG.fail(e); return; }
    pick.value = id;
    draw();
  }

  function header(c) {
    function m(label, value) { return '<div class="tile"><div class="eyebrow">' + CG.esc(label) + '</div><div class="font-headline-sm text-headline-sm mt-1">' + value + '</div></div>'; }
    return '<div class="grid grid-cols-2 md:grid-cols-7 gap-3 mb-4">' + m('Case', '<span class="mono">' + CG.esc(c.id) + '</span>') + m('Type', '<span class="mono text-xs">' + CG.esc(c.request_type) + '</span>') +
      m('State', CG.stateChip(c.state)) + m('Age', CG.esc(CG.age(c.age_hours))) + m('Risk', CG.riskChip(c.risk) || '-') +
      m('Confidence', c.confidence ? CG.bandChip(c.confidence.band, c.confidence.score) : '-') + m('Trust', CG.esc(CG.trustText(c.trust))) + '</div>' +
      '<div class="flex flex-wrap items-center gap-3 mb-4 text-body-sm text-slate-600"><span>' + CG.esc(CG.routingText(c)) + '</span><span>· approver: ' + CG.esc(c.approver_role || '-') +
      '</span><span>· requested by ' + CG.esc(c.requester.name) + ' via ' + CG.esc(c.channel) + '</span>' +
      '<a class="btn btn-sm ml-auto" href="approval.html?case=' + encodeURIComponent(c.id) + '">Open approval →</a></div>';
  }

  function overviewTab(c) {
    var r = c.reviewer;
    var risk = CG.isRestricted(r) ? CG.restricted(r) :
      (r.risk_reasons.length ? '<ul class="list-disc pl-5 text-body-sm">' + r.risk_reasons.map(function (x) { return '<li>' + CG.esc(x) + '</li>'; }).join('') + '</ul>' : CG.empty('No risk reasons recorded.'));
    return '<div class="card mb-4"><div class="flex items-center gap-2 mb-2"><span class="material-symbols-outlined text-slate-500">lock</span><h3 class="font-headline-sm text-headline-sm">Masked request</h3></div>' +
      '<p class="whitespace-pre-wrap">' + CG.esc(c.masked_text) + '</p><div class="mt-3">' + (c.pii_types.length ? c.pii_types.map(function (t) { return CG.chip(t, 'chip-purple'); }).join('') : '') + '</div></div>' +
      '<div class="card mb-4"><h3 class="font-headline-sm text-headline-sm mb-2">Classification</h3><div class="text-body-sm">Type <span class="mono">' + CG.esc(c.request_type) + '</span> · urgency ' + CG.esc(c.urgency) +
      ' · sentiment ' + CG.esc(c.sentiment) + '</div><div class="mt-2">' + CG.reasonChips(c.reason_codes) + '</div></div>' +
      '<div class="card"><h3 class="font-headline-sm text-headline-sm mb-2">Risk reasons</h3>' + risk + '</div>';
  }

  function recommendationTab(c) {
    var p = c.proposal, r = c.reviewer, out = '';
    if (!p) return '<div class="card">' + CG.empty('No recommendation yet.') + '</div>';
    out += '<div class="card mb-4"><h3 class="font-headline-sm text-headline-sm mb-2">Recommendation</h3><div class="mb-2">' + CG.chip(p.decision_code, 'chip-dark') + ' → <b>' + CG.esc(p.route_team || c.assigned_team || '-') + '</b></div>' +
      '<p class="whitespace-pre-wrap">' + CG.esc(p.answer_text) + '</p>' +
      (p.next_steps.length ? '<div class="eyebrow mt-3 mb-1">Next steps</div><ol class="list-decimal pl-5 text-body-sm">' + p.next_steps.map(function (s) { return '<li>' + CG.esc(s) + '</li>'; }).join('') + '</ol>' : '') +
      '<div class="mt-3"><div class="eyebrow mb-1">Sources</div>' + (p.citations.length ? p.citations.map(CG.citationChip).join('') : CG.empty('No verified source: sent to a human.')) + '</div></div>';
    if (CG.isRestricted(r)) return out + '<div class="card">' + CG.restricted(r) + '</div>';
    out += '<div class="card mb-4"><h3 class="font-headline-sm text-headline-sm mb-2">Summary for the reviewer</h3><p class="whitespace-pre-wrap">' + CG.esc(r.summary_for_reviewer) + '</p>' +
      '<p class="text-body-sm text-slate-500 mt-2">Wording by ' + CG.esc(r.model_used || '-') + ' · tiers used: ' + CG.esc((r.llm_tiers_used || []).join(' + ') || 'none') + '</p></div>';
    out += '<div class="card mb-4"><h3 class="font-headline-sm text-headline-sm mb-3">Confidence</h3>' + (r.confidence ? CG.confidenceBar(r.confidence) : CG.empty('Not scored.')) + '</div>';
    if (r.conflicts.length) out += '<div class="p-3 rounded-xl mb-3" style="background:#fef2f2;border:1px solid #fecaca;color:#991b1b"><b>Conflict</b><ul class="list-disc pl-5">' + r.conflicts.map(function (x) { return '<li>' + CG.esc(x) + '</li>'; }).join('') + '</ul></div>';
    if (r.notes.length) out += '<div class="p-3 rounded-xl mb-3" style="background:#f1f5f9;border:1px solid #e2e8f0;color:#334155"><ul class="list-disc pl-5 text-body-sm">' + r.notes.map(function (x) { return '<li>' + CG.esc(x) + '</li>'; }).join('') + '</ul></div>';
    return out;
  }

  function evidenceBlock(label, v, fmt) {
    var body = CG.isRestricted(v) ? CG.restricted(v) : (v.length ? v.map(fmt).join('') : CG.empty('None linked.'));
    return '<div class="card mb-3"><div class="eyebrow mb-1">' + CG.esc(label) + '</div>' + body + '</div>';
  }

  function evidenceTab(c) {
    var ev = c.evidence, r = c.reviewer, out = '';
    out += evidenceBlock('Profile', ev.profile, function (x) { return '<div class="mono text-sm">' + CG.esc(x.id) + (x.specialty ? ' · ' + CG.esc(x.specialty) : '') + '</div>'; });
    out += evidenceBlock('Invoice', ev.invoice, function (x) {
      return '<div class="mono text-sm">' + CG.esc(x.id) + ' · ₹' + Number(x.amount_inr).toLocaleString('en-IN') + ' · ' + CG.esc(x.status) + ' · due ' + CG.esc(x.due_date) + '</div>';
    });
    out += evidenceBlock('System logs', ev.logs, function (x) { return '<div class="mono text-sm">' + CG.esc(x.id) + ' · ' + CG.esc(x.system) + ' · ' + CG.esc(x.level) + ' · ' + CG.esc(x.ts) + '</div>'; });
    out += evidenceBlock('JIRA', ev.jira, function (x) { return '<div class="mono text-sm">' + CG.esc(x.id) + ' · ' + CG.esc(x.status) + '</div>'; });
    out += evidenceBlock('Runbook', ev.runbook, function (x) { return '<div class="mono text-sm">' + CG.esc(x.id) + ' · ' + CG.esc(x.title) + '</div>'; });
    if (CG.isRestricted(r)) return out + '<div class="card">' + CG.restricted(r) + '</div>';
    var rows = r.citations_considered.map(function (x) {
      var stale = x.status === 'stale' || (x.version && x.current_version && x.version !== x.current_version);
      var label = stale ? 'STALE: used v' + x.version + (x.current_version ? ', current v' + x.current_version : '') : x.status;
      return '<tr class="' + (stale ? 'opacity-60' : '') + '"><td><a class="mono text-secondary hover:underline" href="' + CG.pageLink(x.page_id, x.version) + '">' + CG.esc(x.page_id + (x.version ? ' v' + x.version : '')) +
        '</a></td><td>' + CG.esc(x.page_type) + '</td><td>' + CG.esc(x.title) + '</td><td>' + CG.chip(label, stale ? 'chip-red' : 'chip-green') + '</td></tr>';
    }).join('');
    return out + '<div class="card"><div class="eyebrow mb-1">Policies and precedents considered</div>' + (rows ? '<table class="data"><thead><tr><th>Id</th><th>Type</th><th>Title</th><th>Status</th></tr></thead><tbody>' + rows + '</tbody></table>' : CG.empty('None.')) + '</div>';
  }

  var HIGHLIGHT = { guard_blocked: 'chip-red', auto_with_audit: 'chip-teal', review_submitted: 'chip-blue', precedent_saved: 'chip-purple', communication_sent: 'chip-purple', pii_remasked: 'chip-amber' };

  async function auditTab(c) {
    var box = document.getElementById('tab-body');
    CG.loading(box, 'Loading the audit trail…');
    try {
      var ev = await CG_API.get('/api/cases/' + encodeURIComponent(c.id) + '/audit');
      box.innerHTML = '<div class="card"><ol class="relative border-l border-slate-200 ml-2">' + ev.map(function (e) {
        return '<li class="ml-4 mb-3"><div class="flex flex-wrap items-center gap-2">' + CG.chip(e.event, HIGHLIGHT[e.event] || 'chip-slate') + '<span class="mono text-xs text-slate-500">' + CG.esc(CG.time(e.ts)) +
          '</span><span class="text-body-sm text-slate-600">' + CG.esc(e.actor_id + ' (' + e.actor_role + ')') + '</span></div><div class="mono text-xs text-slate-500 break-all mt-1">' +
          CG.esc(JSON.stringify(e.details)) + '</div></li>';
      }).join('') + '</ol></div>';
    } catch (e) { box.innerHTML = '<div class="card">' + CG.restricted() + '</div>'; }
  }

  var KIND_COLOR = { case: '#0f172a', requester: '#94a3b8', policy: '#2b6cb0', workflow: '#0d9488', precedent: '#6b46c1', team: '#d97706', profile: '#64748b', invoice: '#dc2626', log: '#ea580c', jira: '#4f46e5', runbook: '#059669', comm: '#7c3aed' };
  async function graphTab(c) {
    var box = document.getElementById('tab-body');
    CG.loading(box, 'Building the graph…');
    try {
      var g = await CG_API.get('/api/cases/' + encodeURIComponent(c.id) + '/graph');
      box.innerHTML = '<div class="card mb-3"><div id="graph" style="height:420px"></div><div class="mt-2">' +
        Object.keys(KIND_COLOR).map(function (k) { return '<span class="chip" style="background:' + KIND_COLOR[k] + ';color:#fff">' + k + '</span>'; }).join('') + '</div></div>' +
        '<div class="card"><table class="data"><thead><tr><th>From</th><th>Relation</th><th>To</th></tr></thead><tbody>' + g.edges.map(function (e) {
          return '<tr><td class="mono text-xs">' + CG.esc(e.source) + '</td><td>' + CG.chip(e.relation, e.relation === 'conflicts_with' ? 'chip-red' : 'chip-slate') + '</td><td class="mono text-xs">' + CG.esc(e.target) + '</td></tr>';
        }).join('') + '</tbody></table></div>';
      if (window.vis) {
        var nodes = new vis.DataSet(g.nodes.map(function (n) {
          return { id: n.id, label: n.id + (n.version ? ' v' + n.version : ''), title: n.label + (n.status ? ' (' + n.status + ')' : ''), color: { background: KIND_COLOR[n.kind] || '#64748b', border: '#fff' }, font: { color: '#fff', size: 12 }, shape: 'box', margin: 8 };
        }));
        var edges = new vis.DataSet(g.edges.map(function (e) {
          return { from: e.source, to: e.target, label: e.relation, arrows: 'to', font: { size: 9, align: 'middle' }, color: e.relation === 'conflicts_with' ? '#dc2626' : '#94a3b8', dashes: e.relation === 'conflicts_with' };
        }));
        new vis.Network(document.getElementById('graph'), { nodes: nodes, edges: edges }, { physics: { stabilization: true, barnesHut: { springLength: 140 } }, interaction: { hover: true } });
      }
    } catch (e) { box.innerHTML = '<div class="card">' + CG.restricted({ message: 'ACCESS RESTRICTED: the context graph is not available for your role.' }) + '</div>'; }
  }

  function draw() {
    var c = data, root = document.getElementById('case-root');
    var TABS = [['overview', '1. Overview'], ['recommendation', '2. Recommendation'], ['evidence', '3. Evidence'], ['audit', '4. Audit timeline'], ['graph', '5. Context graph']];
    root.innerHTML = header(c) + '<div class="grid grid-cols-1 xl:grid-cols-3 gap-4"><div class="xl:col-span-2"><div class="tabs">' +
      TABS.map(function (t) { return '<button type="button" class="tab ' + (t[0] === tab ? 'active' : '') + '" data-t="' + t[0] + '">' + t[1] + '</button>'; }).join('') +
      '</div><div id="tab-body"></div></div><aside id="assistant"></aside></div>';
    CG.$$('.tab', root).forEach(function (b) { b.onclick = function () { tab = b.dataset.t; draw(); }; });
    var box = document.getElementById('tab-body');
    if (tab === 'overview') box.innerHTML = overviewTab(c);
    else if (tab === 'recommendation') box.innerHTML = recommendationTab(c);
    else if (tab === 'evidence') box.innerHTML = evidenceTab(c);
    else if (tab === 'audit') auditTab(c);
    else graphTab(c);
    drawAssistant(c);
  }

  // ---------------------------------------------------------------- assistant
  var CHIPS = ['Why is this case flagged?', 'Which policy applies?', 'Show related cases', 'Explain the recommendation', 'What should I do next?', 'Prepare for approval'];
  function linkIds(text) {
    return CG.esc(text).replace(/\[([A-Za-z]+-[A-Za-z0-9]+)\]/g, function (m, id) {
      return /^(KA|WF|TEAM|FIELD|REG|RB|P)-/.test(id) && !/^RB-/.test(id) ? '<a class="chip chip-blue" href="' + CG.pageLink(id) + '">' + id + '</a>' : '<span class="chip chip-slate">' + id + '</span>';
    });
  }
  var logs = {};
  function drawAssistant(c) {
    var box = document.getElementById('assistant');
    var log = logs[c.id] = logs[c.id] || [];
    box.innerHTML = '<div class="card" style="position:sticky;top:5.5rem"><div class="flex items-center gap-2 mb-2"><span class="material-symbols-outlined">smart_toy</span><h3 class="font-headline-sm text-headline-sm">Assistant</h3></div>' +
      '<p class="text-body-sm text-slate-500 mb-2">Answers only from what your role can see on ' + CG.esc(c.id) + '.</p><div class="mb-2">' +
      CHIPS.map(function (q) { return '<button type="button" class="chip chip-slate cursor-pointer" data-q="' + CG.esc(q) + '">' + CG.esc(q) + '</button>'; }).join('') + '</div>' +
      '<div id="chat" class="flex flex-col gap-2 mb-2" style="max-height:340px;overflow-y:auto">' + log.map(function (m) { return '<div class="' + (m.role === 'user' ? 'msg-user' : 'msg-bot') + '">' + (m.role === 'user' ? CG.esc(m.text) : linkIds(m.text)) + '</div>'; }).join('') + '</div>' +
      '<form id="ask-form" class="flex gap-2"><input id="ask-input" type="text" maxlength="500" class="flex-1 border border-slate-200 rounded-lg text-sm" placeholder="Ask about this case…"/><button class="btn btn-primary btn-sm" type="submit">Ask</button></form></div>';
    var chat = document.getElementById('chat');
    chat.scrollTop = chat.scrollHeight;
    async function ask(q) {
      if (!q.trim()) return;
      log.push({ role: 'user', text: q });
      drawAssistant(c);
      var pending = document.getElementById('chat');
      pending.insertAdjacentHTML('beforeend', '<div class="msg-bot"><span class="spinner"></span></div>');
      try {
        var r = await CG_API.post('/api/cases/' + encodeURIComponent(c.id) + '/assistant', { question: q });
        log.push({ role: 'bot', text: r.text });
      } catch (e) { log.push({ role: 'bot', text: e.message }); }
      drawAssistant(c);
    }
    CG.$$('#assistant [data-q]').forEach(function (b) { b.onclick = function () { ask(b.dataset.q); }; });
    document.getElementById('ask-form').onsubmit = function (ev) { ev.preventDefault(); var i = document.getElementById('ask-input'); var q = i.value; i.value = ''; ask(q); };
  }
})();
