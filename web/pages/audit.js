/* Audit: one question - what happened, and who did it? Log tab (/api/audit) and Messages tab (/api/comms). */
(async function () {
  var me = await CG.init('audit.html');
  var el = document.getElementById('content');
  var tab = CG.q('tab') === 'messages' ? 'messages' : 'log';
  var HOT = { guard_blocked: 'red', auto_with_audit: 'green', review_submitted: 'blue', precedent_saved: 'blue', communication_sent: 'blue', pii_remasked: 'amber', pr_decided: 'blue' };

  el.innerHTML = '<div class="page-head"><h1>Audit</h1><p class="muted">Every important step is recorded. Details hold ids, codes and counts, never raw text. ' +
    (me.role === 'auditor' ? 'You can see every case (read-only).' : 'You see the cases visible to your role.') + '</p></div><div class="tabs" id="tabs" role="tablist"></div><div id="body"></div>';
  if (CG.canReset()) {                                                       // demo mode, ops manager or senior reviewer
    el.querySelector('.page-head').insertAdjacentHTML('beforeend', '<div style="margin-top:8px"><button class="btn sm" id="test-alert" type="button">Send test alert</button> <span class="small muted" id="test-alert-msg" role="status"></span></div>');
    document.getElementById('test-alert').onclick = async function () {
      var msg = document.getElementById('test-alert-msg');
      msg.textContent = 'Sending…';
      try { var r = await CG_API.post('/api/alerts/test', {}); msg.textContent = r.text; CG.toast(r.text, r.status === 'failed' ? 'error' : 'ok'); } catch (e) { msg.textContent = e.message; }
    };
  }
  var TABS = [['log', 'Log'], ['messages', 'Messages']];
  function drawTabs() {
    document.getElementById('tabs').innerHTML = TABS.map(function (t) { return '<button type="button" role="tab" class="tab ' + (t[0] === tab ? 'on' : '') + '" data-t="' + t[0] + '" aria-selected="' + (t[0] === tab) + '">' + t[1] + '</button>'; }).join('');
    CG.$$('#tabs .tab').forEach(function (b) { b.onclick = function () { tab = b.dataset.t; drawTabs(); show(); }; });
  }
  drawTabs(); show();
  function show() { var b = document.getElementById('body'); if (tab === 'log') logTab(b); else messagesTab(b); }

  async function logTab(body) {
    body.innerHTML = '<div class="card"><div class="flex" style="margin-bottom:12px"><label class="field" style="width:150px">Case<input id="f-case" type="text" placeholder="REQ-0001"></label>' +
      '<label class="field" style="width:220px">What happened<select id="f-event"><option value="">Anything</option></select></label>' +
      '<label class="field" style="width:180px">Who<select id="f-actor"><option value="">Anyone</option>' + CG.users.map(function (u) { return '<option value="' + CG.esc(u.id) + '">' + CG.esc(u.name) + '</option>'; }).join('') + '</select></label>' +
      '<button id="apply" class="btn primary" type="button" style="align-self:flex-end">Apply</button></div><div id="table"></div></div>';
    var seen = false;
    async function load() {
      var p = new URLSearchParams(); p.set('limit', '2000');
      [['case', 'f-case'], ['event', 'f-event'], ['actor', 'f-actor']].forEach(function (x) { var v = document.getElementById(x[1]).value.trim(); if (v) p.set(x[0], v); });
      if (!seen && CG.q('event') && !p.get('event')) p.set('event', CG.q('event'));            // arriving from a dashboard link
      var box = document.getElementById('table');
      CG.loading(box, 'Loading events…');
      try {
        var r = await CG_API.get('/api/audit' + (p.toString() ? '?' + p : ''));
        if (!seen) { document.getElementById('f-event').innerHTML = '<option value="">Anything</option>' + r.event_types.map(function (t) { return '<option value="' + CG.esc(t) + '"' + (t === CG.q('event') ? ' selected' : '') + '>' + CG.esc(CG.eventName(t)) + '</option>'; }).join(''); seen = true; }
        box.innerHTML = r.events.length ? '<div class="small muted" style="margin-bottom:6px">' + r.events.length + ' event' + (r.events.length === 1 ? '' : 's') + '</div><div class="tablewrap"><table><thead><tr><th>Time</th><th>Case</th><th>What happened</th><th>Who</th><th></th></tr></thead><tbody>' +
          r.events.map(function (e, i) {
            return '<tr><td class="small nowrap">' + CG.esc(CG.time(e.ts)) + '</td><td>' + (e.case_id ? CG.caseLink(e.case_id) : '<span class="faint">system</span>') + '</td><td>' +
              CG.tag(CG.eventName(e.event), HOT[e.event] || 'grey', e.event) + '</td><td class="small">' + CG.esc(CG.userName(e.actor_id) + ' · ' + CG.role(e.actor_role)) + '</td><td><button class="btn sm" type="button" data-i="' + i + '" aria-expanded="false">Details</button></td></tr>' +
              '<tr id="d' + i + '" hidden><td colspan="5"><pre class="mono small" style="margin:0;white-space:pre-wrap;word-break:break-all;background:var(--bg);padding:10px;border-radius:8px">' + CG.esc(JSON.stringify(e.details, null, 2)) + '</pre></td></tr>';
          }).join('') + '</tbody></table></div>' : CG.empty('No events match. Clear a filter or submit a request.');
        CG.$$('button[data-i]', box).forEach(function (b) { b.onclick = function () { var row = document.getElementById('d' + b.dataset.i); row.hidden = !row.hidden; b.setAttribute('aria-expanded', String(!row.hidden)); }; });
      } catch (e) { CG.fail(e); box.innerHTML = CG.empty('Could not load the audit log.'); }
    }
    document.getElementById('apply').onclick = load;
    load();
  }

  async function messagesTab(body) {
    CG.loading(body, 'Loading messages…');
    var rows;
    try { rows = await CG_API.get('/api/comms'); } catch (e) { CG.fail(e); body.innerHTML = CG.empty('Could not load messages.'); return; }
    body.innerHTML = '<div class="card"><p class="small muted" style="margin-bottom:10px">Sent when a reviewer approves a case. E-mail is simulated in this build; WhatsApp and SMS always are. Nothing leaves the system.</p>' +
      (rows.length ? '<div class="tablewrap"><table id="msgs"><thead><tr><th>Case</th><th>Channel</th><th>To</th><th>Status</th><th>Time</th></tr></thead><tbody>' + rows.map(function (r, i) {
        return '<tr class="click" data-i="' + i + '"><td>' + CG.caseLink(r.case_id) + '</td><td>' + CG.esc(CG.human(r.channel)) + '</td><td class="small">' + CG.esc(r.recipient) + '</td><td>' + (r.simulated ? CG.tag('Simulated', 'grey') : CG.tag(CG.human(r.status), r.status === 'failed' ? 'red' : 'green')) +
          '</td><td class="small nowrap">' + CG.esc(CG.time(r.ts)) + '</td></tr><tr id="m' + i + '" hidden><td colspan="5"><div class="callout grey" style="white-space:pre-wrap">' + CG.esc(r.message) + '</div></td></tr>';
      }).join('') + '</tbody></table></div>' : CG.empty('No messages yet. Approve a case with a channel selected to create one.')) + '</div>';
    CG.$$('#msgs tr.click').forEach(function (tr) { tr.onclick = function (ev) { if (ev.target.tagName === 'A') return; var d = document.getElementById('m' + tr.dataset.i); d.hidden = !d.hidden; }; });
  }
})();
