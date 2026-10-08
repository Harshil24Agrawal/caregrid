/* Cases: list, or one case with the Decide panel, assistant and evidence tabs. One question per view: "what is going on with this case?"
   Everything comes from /api/cases/{id}[...]; permissions and their reasons come from the server. */
(async function () {
  var me = await CG.init('case.html');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading…');
  var list;
  try { list = await CG_API.get('/api/cases'); } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load cases.'); return; }
  var wanted = CG.q('case');
  if (!wanted) return showList();
  var visible = list.some(function (c) { return c.id === wanted; });
  if (!visible) {
    el.innerHTML = '<div class="page-head"><a class="small" href="case.html">← All cases</a><h1>' + CG.esc(wanted) + '</h1></div><div class="card">' + CG.lock({ message: 'ACCESS RESTRICTED: this case is not visible to ' + me.name + ' (' + CG.role(me.role) + ').' }) + '</div>';
    return;
  }
  showCase(wanted);

  // ------------------------------------------------------------------ list
  function showList() {
    var states = Array.from(new Set(list.map(function (c) { return c.state; })));
    el.innerHTML = '<div class="page-head"><h1>Cases</h1><p class="muted">' + list.length + ' case' + (list.length === 1 ? '' : 's') + ' visible to ' + CG.esc(me.name) + ' (' + CG.esc(CG.role(me.role)) + ').</p></div>' +
      '<div class="card"><div class="flex" style="margin-bottom:10px"><label class="small muted">Show <select id="f-state"><option value="">all</option>' + states.map(function (s) { return '<option value="' + CG.esc(s) + '">' + CG.esc(CG.human(s)) + '</option>'; }).join('') + '</select></label></div>' +
      '<div class="tablewrap"><table><thead><tr><th>Case</th><th>What</th><th>State</th><th>Risk</th><th>Team</th><th>Age</th></tr></thead><tbody id="rows"></tbody></table></div></div>';
    function draw() {
      var f = document.getElementById('f-state').value;
      document.getElementById('rows').innerHTML = list.filter(function (c) { return !f || c.state === f; }).map(function (c) {
        return '<tr class="click" data-id="' + CG.esc(c.id) + '"><td>' + CG.caseLink(c.id) + '</td><td>' + CG.esc(CG.what(c.request_type)) + '</td><td>' + CG.stateTag(c.state) + '</td><td>' + CG.riskTag(c.risk) +
          '</td><td class="small">' + CG.esc(CG.team(c.team)) + '</td><td class="nowrap small">' + CG.esc(CG.age(c.age_hours)) + '</td></tr>';
      }).join('') || '<tr><td colspan="6">' + CG.empty('No cases in this state.') + '</td></tr>';
      CG.$$('#rows tr.click').forEach(function (tr) { tr.onclick = function (ev) { if (ev.target.tagName !== 'A') window.location.href = 'case.html?case=' + encodeURIComponent(tr.dataset.id); }; });
    }
    document.getElementById('f-state').onchange = draw;
    draw();
  }

  // ------------------------------------------------------------------ one case
  var c, events = [], tab = 'evidence', lastResult = null, chat = [];

  async function showCase(id) {
    try {
      var r = await Promise.all([CG_API.get('/api/cases/' + encodeURIComponent(id)), CG_API.get('/api/cases/' + encodeURIComponent(id) + '/audit')]);
      c = r[0]; events = r[1];
    } catch (e) { CG.fail(e); el.innerHTML = '<div class="card">' + CG.lock() + '</div>'; return; }
    draw();
    if (CG.q('decide') === '1') { var d = document.getElementById('decide'); if (d) d.scrollIntoView(); }
  }

  function rowsHtml() {
    var r = c.reviewer, p = c.proposal, locked = CG.isRestricted(r);
    var why;
    if (c.routing === 'auto') why = '<span class="tag green">Auto</span> Answered automatically from approved policy. No person was needed.';
    else why = (c.reason_codes.length ? CG.reasonChips(c.reason_codes) : '') + '<div class="small muted">' + (c.approver_role ? 'A ' + CG.esc(CG.role(c.approver_role).toLowerCase()) + ' in ' : 'A person in ') + CG.esc(CG.team(c.assigned_team)) + ' decides.</div>';
    var risk = CG.riskTag(c.risk) + (locked ? '' : (r.risk_reasons.length ? '<ul style="margin:6px 0 0 18px;padding:0">' + r.risk_reasons.map(function (x) { return '<li>' + CG.esc(x) + '</li>'; }).join('') + '</ul>' : ''));
    var conf = locked ? (CG.band(c.confidence) + '<div class="small muted" style="margin-top:6px">Details are limited for your role.</div>') : (r.confidence ? CG.confidenceBar(r.confidence) : CG.empty('Not scored.'));
    var missing = locked ? CG.lock(r) : (r.missing_fields.length || Object.keys(r.invalid_fields).length ?
      '<ul style="margin:0 0 0 18px;padding:0">' + r.missing_fields.map(function (f) { return '<li>' + CG.esc(CG.human(f)) + ': missing</li>'; }).join('') +
      Object.keys(r.invalid_fields).map(function (f) { return '<li>' + CG.esc(CG.human(f)) + ': ' + CG.esc(r.invalid_fields[f]) + '</li>'; }).join('') + '</ul>' : '<span class="muted">Nothing missing.</span>');
    var next = p ? (p.next_steps.length ? CG.esc(p.next_steps[0]) + (p.next_steps.length > 1 ? ' <span class="small muted">(+' + (p.next_steps.length - 1) + ' more)</span>' : '') : 'Route to ' + CG.esc(CG.team(p.route_team || c.assigned_team)) + '.') : '<span class="muted">No recommendation yet.</span>';
    var extra = locked ? '' : r.conflicts.map(function (x) { return '<div class="callout red"><b>Policies disagree:</b> ' + CG.esc(x) + '</div>'; }).join('') + r.notes.map(function (x) { return '<div class="callout grey">' + CG.esc(x) + '</div>'; }).join('');
    function row(label, html) { return '<div class="row"><div class="label">' + label + '</div><div>' + html + '</div></div>'; }
    return '<div class="rows">' + row('What', '<span style="white-space:pre-wrap">' + CG.esc(c.masked_text) + '</span>' + (c.pii_types.length ? '<div style="margin-top:4px">' + c.pii_types.map(function (t) { return '<span class="chip grey">masked: ' + CG.esc(CG.piiLabel(t)) + '</span>'; }).join('') + '</div>' : '')) +
      row('Why a human', why) + row('Risk', risk) + row('Confidence', conf) + row('Missing', missing) +
      row('Sources', p && p.citations.length ? p.citations.map(CG.citationChip).join('') : '<span class="muted">No verified source: sent to a person.</span>') + row('Next step', next) + '</div>' + extra;
  }

  function decidePanel() {
    var a = c.actions;
    var done = '';
    if (lastResult) {
      var r = lastResult.result;
      done = '<div class="callout green" id="decision-result"><b>Decision recorded.</b><div class="stack" style="margin-top:6px"><div>' + r.state_path.map(CG.stateTag).join(' → ') + '</div>' +
        '<div class="small">Precedent: <b class="mono">' + CG.esc(r.precedent_id || 'none saved') + '</b></div>' +
        '<div class="small">Trust: ' + (r.trust ? CG.esc(CG.what(r.trust.request_type) + ': level ' + r.trust.level_before + ' → ' + r.trust.level_after + ', streak ' + r.trust.streak_before + ' → ' + r.trust.streak_after) : 'unchanged') + '</div>' +
        (r.pr_id ? '<div class="small">Change request opened: <a href="knowledge.html?tab=prs" class="mono">' + CG.esc(r.pr_id) + '</a></div>' : '') +
        (r.communications.length ? '<div class="small">Messages: ' + r.communications.map(function (m) { return CG.esc(m.channel + ' (' + m.status + ')'); }).join(', ') + '</div>' : '') + '</div></div>';
    }
    if (!a.decidable) return '<div class="card" id="decide"><h2>Decide</h2>' + done + (done ? '' : '<p class="muted" style="margin-top:6px">This case is ' + CG.esc(CG.human(c.state).toLowerCase()) + ': there is nothing to decide.</p>') + '</div>';
    if (!a.approve.allowed && !a.ask.allowed) return '<div class="card" id="decide"><h2>Decide</h2>' + done + '<div class="lock" style="margin-top:10px"><span aria-hidden="true">🔒</span><span>You can’t decide this case. ' + CG.esc(a.approve.reason) + '</span></div></div>';
    var policies = c.proposal ? c.proposal.citations.filter(function (x) { return x.page_type === 'policy'; }) : [];
    var ACTIONS = [['approve', 'Approve'], ['edit_approve', 'Edit and approve'], ['reject', 'Reject'], ['escalate', 'Escalate'], ['ask_requester', 'Ask requester']];
    return '<div class="card" id="decide"><h2>Decide</h2>' + done + (a.approve.allowed ? '' : '<div class="lock" style="margin:10px 0"><span aria-hidden="true">🔒</span><span>Approving is disabled: ' + CG.esc(a.approve.reason) + '</span></div>') +
      '<div class="flex" style="margin:10px 0" id="actions" role="radiogroup" aria-label="Decision">' + ACTIONS.map(function (x, i) {
        var ok = x[0] === 'ask_requester' ? a.ask.allowed : a.approve.allowed;
        return '<label class="chip' + (ok ? '' : ' dim') + '"><input type="radio" name="action" value="' + x[0] + '" ' + (ok ? '' : 'disabled ') + (i === 0 && a.approve.allowed ? 'checked' : '') + '> ' + x[1] + '</label>';
      }).join('') + '</div>' +
      '<div id="edit-wrap" style="display:none;margin-bottom:10px"><label class="field">Edited answer<textarea id="edited" rows="4">' + CG.esc(c.proposal ? c.proposal.answer_text : '') + '</textarea></label></div>' +
      '<label class="field">Note <textarea id="note" rows="2" maxlength="2000" placeholder="Why? (masked before it is stored)"></textarea></label>' +
      '<div class="flex" style="margin:10px 0"><label><input type="checkbox" id="prec" checked> Save as precedent</label></div>' +
      '<details id="more"><summary class="small" style="cursor:pointer">More options: change request, contact, channels</summary><div class="stack" style="margin-top:10px">' +
      '<label><input type="checkbox" id="pr" ' + (policies.length ? '' : 'disabled') + '> Propose a change to a policy' + (policies.length ? '' : ' (this case cites none)') + '</label>' +
      '<div id="pr-wrap" style="display:none" class="stack"><label class="field">Policy <select id="pr-target">' + policies.map(function (x) { return '<option>' + CG.esc(x.page_id) + '</option>'; }).join('') + '</select></label>' +
      '<label><input type="checkbox" id="pr-retire"> Retire this policy (no replacement)</label></div>' +
      '<label class="field">Official contact email<input type="text" id="email" placeholder="desk@clinic.example"></label><label class="field">Official contact phone<input type="text" id="phone" placeholder="+91 …"></label>' +
      '<div class="flex"><span class="small muted">Notify via</span>' + ['email', 'whatsapp', 'sms', 'portal'].map(function (ch) { return '<label class="small"><input type="checkbox" class="chan" value="' + ch + '"> ' + ch + '</label>'; }).join('') + '</div></div></details>' +
      '<div class="flex" style="margin-top:14px"><button class="btn primary" id="go" type="button">Submit decision</button><span id="go-why" class="small muted"></span></div></div>';
  }

  function wireDecide() {
    var go = document.getElementById('go');
    if (!go) return;
    function sync() {
      var act = (document.querySelector('input[name=action]:checked') || {}).value, ask = act === 'ask_requester';
      document.getElementById('edit-wrap').style.display = act === 'edit_approve' ? '' : 'none';
      var hasPolicy = c.proposal && c.proposal.citations.some(function (x) { return x.page_type === 'policy'; });
      document.getElementById('prec').disabled = ask; document.getElementById('pr').disabled = ask || !hasPolicy;
      document.getElementById('pr-wrap').style.display = document.getElementById('pr').checked && !document.getElementById('pr').disabled ? '' : 'none';
      var ok = ask ? c.actions.ask.allowed : c.actions.approve.allowed;
      go.disabled = !ok || !act;
      document.getElementById('go-why').textContent = ok ? '' : (ask ? c.actions.ask.reason : c.actions.approve.reason);
    }
    CG.$$('input[name=action], #pr').forEach(function (i) { i.onchange = sync; });
    sync();
    go.onclick = async function () {
      var act = document.querySelector('input[name=action]:checked').value;
      var body = { action: act, note: document.getElementById('note').value, save_as_precedent: document.getElementById('prec').checked && act !== 'ask_requester',
        propose_pr: document.getElementById('pr').checked && !document.getElementById('pr').disabled, contact_email: document.getElementById('email').value || null,
        contact_phone: document.getElementById('phone').value || null, channels: CG.$$('.chan').filter(function (x) { return x.checked; }).map(function (x) { return x.value; }), meta_changes: {} };
      if (act === 'edit_approve') body.edited_answer = document.getElementById('edited').value;
      if (body.propose_pr) { body.meta_changes.target_page = document.getElementById('pr-target').value; if (document.getElementById('pr-retire').checked) body.meta_changes.retire = true; }
      go.disabled = true;
      try {
        lastResult = await CG_API.post('/api/cases/' + encodeURIComponent(c.id) + '/decision', body);
        c = lastResult.case;
        events = await CG_API.get('/api/cases/' + encodeURIComponent(c.id) + '/audit');
        CG.toast('Decision recorded for ' + c.id + '.', 'ok');
        draw();
      } catch (e) { CG.fail(e); go.disabled = false; }
    };
  }

  var CHIPS = ['Why is this case flagged?', 'Which policy applies?', 'Show related cases', 'Explain the recommendation', 'What should I do next?', 'Prepare for approval'];
  function linkIds(text) {
    return CG.esc(text).replace(/\[([A-Za-z]+-[A-Za-z0-9]+)\]/g, function (m, id) {
      return /^(KA|WF|TEAM|FIELD|REG|P)-/.test(id) ? '<a class="chip" href="' + CG.pageLink(id) + '">' + id + '</a>' : '<span class="chip grey">' + id + '</span>';
    });
  }
  function assistantHtml() {
    return '<div class="card"><h2>Assistant</h2><p class="small muted" style="margin:4px 0 8px">Answers only from what your role can see on ' + CG.esc(c.id) + '.</p><div>' +
      CHIPS.map(function (q) { return '<button type="button" class="chip" data-q="' + CG.esc(q) + '">' + CG.esc(q) + '</button>'; }).join('') + '</div>' +
      '<div class="chat" id="chat" aria-live="polite">' + chat.map(function (m) { return '<div class="msg ' + m.role + '">' + (m.role === 'user' ? CG.esc(m.text) : linkIds(m.text)) + '</div>'; }).join('') + '</div>' +
      '<form id="ask-form" class="flex"><label class="sr-only" for="ask-input">Question</label><input id="ask-input" type="text" maxlength="500" style="flex:1" placeholder="Ask about this case…"><button class="btn sm primary" type="submit">Ask</button></form></div>';
  }
  function wireAssistant() {
    async function ask(q) {
      if (!q.trim()) return;
      chat.push({ role: 'user', text: q });
      refreshAssistant();
      document.getElementById('chat').insertAdjacentHTML('beforeend', '<div class="msg bot"><span class="spinner"></span></div>');
      try { chat.push({ role: 'bot', text: (await CG_API.post('/api/cases/' + encodeURIComponent(c.id) + '/assistant', { question: q })).text }); } catch (e) { chat.push({ role: 'bot', text: e.message }); }
      refreshAssistant();
    }
    function refreshAssistant() { document.getElementById('assistant').innerHTML = assistantHtml(); wireAssistant(); var ch = document.getElementById('chat'); ch.scrollTop = ch.scrollHeight; }
    CG.$$('#assistant [data-q]').forEach(function (b) { b.onclick = function () { ask(b.dataset.q); }; });
    document.getElementById('ask-form').onsubmit = function (ev) { ev.preventDefault(); var i = document.getElementById('ask-input'); var q = i.value; i.value = ''; ask(q); };
  }

  // ---- tabs
  function evidenceBlock(label, v, fmt) {
    return '<div class="row"><div class="label">' + label + '</div><div>' + (CG.isRestricted(v) ? CG.lock(v) : v.length ? v.map(fmt).join('') : '<span class="muted">None linked.</span>') + '</div></div>';
  }
  function evidenceTab() {
    var ev = c.evidence, r = c.reviewer;
    var out = '<div class="rows">' + evidenceBlock('Profile', ev.profile, function (x) { return '<div class="mono small">' + CG.esc(x.id) + (x.specialty ? ' · ' + CG.esc(x.specialty) : '') + '</div>'; }) +
      evidenceBlock('Invoice', ev.invoice, function (x) { return '<div class="mono small">' + CG.esc(x.id) + ' · ₹' + Number(x.amount_inr).toLocaleString('en-IN') + ' · ' + CG.esc(x.status) + ' · due ' + CG.esc(x.due_date) + '</div>'; }) +
      evidenceBlock('System logs', ev.logs, function (x) { return '<div class="mono small">' + CG.esc(x.id) + ' · ' + CG.esc(x.system) + ' · ' + CG.esc(x.level) + '</div>'; }) +
      evidenceBlock('JIRA', ev.jira, function (x) { return '<div class="mono small">' + CG.esc(x.id) + ' · ' + CG.esc(x.status) + '</div>'; }) +
      evidenceBlock('Runbook', ev.runbook, function (x) { return '<div class="small">' + CG.esc(x.id + ' · ' + x.title) + '</div>'; });
    if (CG.isRestricted(r)) return out + '<div class="row"><div class="label">Policies and past cases</div><div>' + CG.lock(r) + '</div></div></div>';
    var rows = r.citations_considered.map(function (x) {
      var stale = x.status === 'stale' || (x.version && x.current_version && x.version !== x.current_version);
      return '<tr><td><a class="mono" href="' + CG.pageLink(x.page_id, x.version) + '">' + CG.esc(x.page_id + (x.version ? ' v' + x.version : '')) + '</a></td><td>' + CG.esc(x.page_type) + '</td><td>' + CG.esc(x.title) + '</td><td>' +
        (stale ? CG.tag('Stale', 'grey', 'used v' + x.version + ', current v' + x.current_version) : CG.tag(CG.human(x.status), 'green')) + '</td></tr>';
    }).join('');
    return out + '<div class="row"><div class="label">Policies and past cases</div><div>' + (rows ? '<table><tbody>' + rows + '</tbody></table>' : '<span class="muted">None.</span>') + '</div></div></div>';
  }
  function graphTab(box) {
    CG.loading(box, 'Building the graph…');
    CG_API.get('/api/cases/' + encodeURIComponent(c.id) + '/graph').then(function (g) {
      var color = function (k) { return k === 'case' ? '#13294b' : (k === 'policy' || k === 'workflow') ? '#1d5fd1' : '#8794a7'; };
      box.innerHTML = '<div id="graph" style="height:360px;border:1px solid var(--line);border-radius:10px;background:#fff"></div><div class="small muted" style="margin:8px 0">Navy: this case · Blue: policies and workflows · Grey: everything else · Red dashed: policies that disagree.</div>' +
        '<details><summary class="small" style="cursor:pointer">' + g.edges.length + ' relationships as a table</summary><table><tbody>' + g.edges.map(function (e) {
          return '<tr><td class="mono small">' + CG.esc(e.source) + '</td><td>' + CG.tag(CG.human(e.relation), e.relation === 'conflicts_with' ? 'red' : 'grey') + '</td><td class="mono small">' + CG.esc(e.target) + '</td></tr>'; }).join('') + '</tbody></table></details>';
      if (window.vis) {
        new vis.Network(document.getElementById('graph'), {
          nodes: new vis.DataSet(g.nodes.map(function (n) { return { id: n.id, label: n.id + (n.version ? ' v' + n.version : ''), title: n.label, shape: 'box', margin: 8, color: { background: color(n.kind), border: '#fff' }, font: { color: '#fff', size: 12 } }; })),
          edges: new vis.DataSet(g.edges.map(function (e) { return { from: e.source, to: e.target, label: CG.human(e.relation), arrows: 'to', font: { size: 9, align: 'middle' }, color: e.relation === 'conflicts_with' ? '#b42318' : '#9aa6b6', dashes: e.relation === 'conflicts_with' }; }))
        }, { physics: { stabilization: true }, interaction: { hover: true } });
      }
    }).catch(function () { box.innerHTML = CG.lock({ message: 'ACCESS RESTRICTED: the context graph is not available for your role.' }); });
  }
  var HOT = { review_submitted: 1, precedent_saved: 1, communication_sent: 1, guard_blocked: 1, pr_opened: 1, pr_decided: 1 };
  function auditTab() {
    return '<div class="timeline">' + events.map(function (e) {
      return '<div class="ev ' + (HOT[e.event] ? 'hot' : '') + '"><b>' + CG.esc(CG.eventName(e.event)) + '</b> <span class="small muted">' + CG.esc(CG.time(e.ts)) + ' · ' + CG.esc(e.actor_id) + '</span></div>';
    }).join('') + '</div>';
  }
  function messagesTab(box) {
    CG.loading(box);
    CG_API.get('/api/comms').then(function (all) {
      var mine = all.filter(function (m) { return m.case_id === c.id; });
      box.innerHTML = mine.length ? '<table><thead><tr><th>Channel</th><th>To</th><th>Message</th><th>Status</th></tr></thead><tbody>' + mine.map(function (m) {
        return '<tr><td>' + CG.esc(m.channel) + '</td><td class="small mono">' + CG.esc(m.recipient) + '</td><td class="small">' + CG.esc(m.message) + '</td><td>' + (m.simulated ? CG.tag('Simulated', 'grey') : CG.tag(CG.human(m.status), 'green')) + '</td></tr>'; }).join('') + '</tbody></table>' :
        CG.empty('No messages yet. Approving the case with a channel selected sends one (simulated).');
    }).catch(function () { box.innerHTML = CG.empty('Could not load messages.'); });
  }

  function draw() {
    var lit = events.map(function (e) { return e.event; });
    el.innerHTML = '<div class="page-head"><a class="small" href="case.html">← All cases</a><div class="flex" style="margin-top:6px"><h1 class="mono">' + CG.esc(c.id) + '</h1><span style="font-size:18px;font-weight:600">' + CG.esc(CG.what(c.request_type)) + '</span>' +
      CG.stateTag(c.state) + CG.riskTag(c.risk) + '<span class="small muted">' + CG.esc(CG.age(c.age_hours)) + ' old · requested by ' + CG.esc(c.requester.name) + '</span></div></div>' +
      '<div class="card" style="margin-bottom:16px">' + CG.pipeline(lit, -1) + '</div>' +
      '<div class="grid g-case"><div class="card">' + rowsHtml() + '</div><div class="stack">' + decidePanel() + '<div id="assistant">' + assistantHtml() + '</div></div></div>' +
      '<div class="tabs" role="tablist">' + [['evidence', 'Evidence'], ['graph', 'Graph'], ['audit', 'Audit timeline'], ['messages', 'Messages']].map(function (t) {
        return '<button type="button" role="tab" class="tab ' + (t[0] === tab ? 'on' : '') + '" data-t="' + t[0] + '" aria-selected="' + (t[0] === tab) + '">' + t[1] + '</button>'; }).join('') + '</div><div class="card" id="tab-body"></div>';
    CG.$$('.tab', el).forEach(function (b) { b.onclick = function () { tab = b.dataset.t; draw(); }; });
    var box = document.getElementById('tab-body');
    if (tab === 'evidence') box.innerHTML = evidenceTab(); else if (tab === 'graph') graphTab(box); else if (tab === 'audit') box.innerHTML = auditTab(); else messagesTab(box);
    wireDecide();
    wireAssistant();
  }
})();
