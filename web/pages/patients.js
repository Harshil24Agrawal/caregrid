/* Patients: one question - who is this patient, and what has happened to their requests? Data: /api/patients, /api/patients/{ref}.
   The Health ID is typed here once, sent to the API and forgotten: the page and its URL only ever hold the masked ID and an internal key. */
(async function () {
  var me = await CG.init('patients.html');
  var el = document.getElementById('content');
  var ref = CG.q('ref');
  var CONSENT = { share_across_teams: 'Share across teams', contact_by_whatsapp: 'Contact by WhatsApp', contact_by_email: 'Contact by email' };

  function lookupCard() {
    return '<div class="card"><h2>Open a patient</h2><form id="lookup" class="flex" style="margin-top:8px"><label class="sr-only" for="hid">CareGrid Health ID</label>' +
      '<input id="hid" type="text" autocomplete="off" spellcheck="false" placeholder="CG-XXXX-XXXX-XXXX" maxlength="24" style="flex:1;max-width:320px">' +
      '<button class="btn primary" type="submit">Open record</button></form><div id="lookup-msg" class="small" style="margin-top:6px" role="status"></div></div>';
  }
  function wireLookup() {
    document.getElementById('lookup').onsubmit = async function (ev) {
      ev.preventDefault();
      var box = document.getElementById('hid'), msg = document.getElementById('lookup-msg'), id = box.value.trim();
      if (!id) { msg.textContent = 'Type a Health ID first.'; return; }
      box.value = '';                                  // the ID is not kept in the page
      msg.textContent = '';
      try {
        var rec = await CG_API.get('/api/patients/' + encodeURIComponent(id));
        window.location.href = 'patients.html?ref=' + encodeURIComponent(rec.ref);
      } catch (e) {
        msg.className = 'small';
        msg.style.color = 'var(--red)';
        msg.textContent = e.status === 422 ? 'That Health ID does not pass its checksum. Please re-check the digits.' : 'No patient found that you can open.';
      }
    };
  }

  async function listView() {
    var rows;
    try { rows = await CG_API.get('/api/patients'); } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load patients.'); return; }
    el.innerHTML = '<div class="page-head"><h1>Patients</h1><p class="muted">Who is this patient, and what has happened to their requests? Personal details are never shown here.</p></div>' + lookupCard() +
      '<div class="card" style="margin-top:16px"><div class="card-title"><h2>Patients you can open</h2></div>' + (rows.length ? '<div class="tablewrap"><table><thead><tr><th>Health ID</th><th>Plan</th><th>Cases you see</th><th></th></tr></thead><tbody>' + rows.map(function (r) {
        return '<tr class="click" data-ref="' + CG.esc(r.ref) + '"><td class="nw mono">' + CG.esc(r.masked_id) + '</td><td class="nw">' + CG.esc(r.plan || '') + '</td><td class="nw">' + r.cases + '</td><td class="nw"><a class="btn sm" href="patients.html?ref=' + encodeURIComponent(r.ref) + '">Open</a></td></tr>';
      }).join('') + '</tbody></table></div>' : CG.empty('No patient is linked to a case you can see. Put a valid Health ID in a request, and the case is linked to that patient.')) + '</div>';
    wireLookup();
    CG.$$('tr.click', el).forEach(function (tr) { tr.onclick = function (ev) { if (ev.target.tagName !== 'A') window.location.href = 'patients.html?ref=' + encodeURIComponent(tr.dataset.ref); }; });
  }

  async function detailView() {
    var p;
    try { p = await CG_API.get('/api/patients/' + encodeURIComponent(ref)); } catch (e) {
      el.innerHTML = '<div class="page-head"><a class="small" href="patients.html">← All patients</a><h1>Patient</h1></div><div class="card">' + CG.empty('No patient found that you can open.') + '</div>';
      return;
    }
    var consent = Object.keys(CONSENT).map(function (k) { var on = p.consent && p.consent[k]; return '<span class="chip ' + (on ? 'green' : 'grey') + '">' + (on ? '✓ ' : '✗ ') + CONSENT[k] + '</span>'; }).join('');
    var timeline = p.access_log ? '' :
      '<div class="card" style="margin-top:16px"><div class="card-title"><h2>Timeline</h2><span class="small muted">' + p.timeline.length + ' case' + (p.timeline.length === 1 ? '' : 's') + ' you can see</span></div>' +
      (p.timeline.length ? '<div class="tablewrap"><table><thead><tr><th>Date</th><th>Case</th><th>What</th><th>State</th><th>Team</th><th>Summary</th></tr></thead><tbody>' + p.timeline.map(function (t) {
        return '<tr><td class="nw small">' + CG.esc(t.date) + '</td><td class="nw">' + CG.caseLink(t.case_id) + '</td><td class="nw">' + CG.esc(CG.shortWhat(t.request_type)) + '</td><td class="nw">' + CG.stateTag(t.state) + '</td><td class="nw small">' + CG.esc(CG.teamShort(t.team)) + '</td><td class="ellip small" title="' + CG.esc(t.summary) + '">' + CG.esc(t.summary) + '</td></tr>';
      }).join('') + '</tbody></table></div>' : CG.empty('None of this patient\'s cases are visible to your role.')) + '</div>';
    var log = p.access_log ? '<div class="card" style="margin-top:16px"><div class="card-title"><h2>Access log</h2><span class="small muted">who looked at this record</span></div>' + (p.access_log.length ?
      '<div class="tablewrap"><table><thead><tr><th>When</th><th>What</th><th>Who</th><th>Role</th><th>Case</th></tr></thead><tbody>' + p.access_log.map(function (e) {
        return '<tr><td class="nw small">' + CG.esc(CG.time(e.ts)) + '</td><td class="nw">' + CG.esc(CG.eventName(e.event)) + '</td><td class="nw">' + CG.esc(CG.userName(e.actor_id)) + '</td><td class="nw small">' + CG.esc(CG.role(e.actor_role)) + '</td><td class="nw">' + (e.case_id ? CG.caseLink(e.case_id) : '') + '</td></tr>'; }).join('') + '</tbody></table></div>' : CG.empty('Nobody has opened this record yet.')) + '</div>' : '';
    var reveal = p.personal.reveal_allowed ? '<details class="fold" id="reveal-box" style="box-shadow:none;margin-top:10px"><summary class="small">Reveal personal details for one case</summary><div class="fold-body"><div class="stack">' +
      '<label class="field">Case<select id="rv-case">' + p.timeline.map(function (t) { return '<option>' + CG.esc(t.case_id) + '</option>'; }).join('') + '</select></label>' +
      '<label class="field">Reason (required, audited)<textarea id="rv-reason" rows="2" maxlength="300" placeholder="Why do you need the name, phone and date of birth?"></textarea></label>' +
      '<div class="flex"><button class="btn primary" id="rv-go" type="button">Reveal</button><span class="small muted">Shown once, never stored.</span></div><div id="rv-out"></div></div></div></details>' : '';
    el.innerHTML = '<div class="page-head"><a class="small" href="patients.html">← All patients</a><div class="flex" style="margin-top:6px"><h1 class="mono">' + CG.esc(p.masked_id) + '</h1>' + (p.plan ? CG.tag(p.plan + ' plan', 'blue') : '') + '</div></div>' +
      '<div class="card"><div class="rows"><div class="row"><div class="label">Consent</div><div>' + consent + '</div></div>' +
      '<div class="row"><div class="label">Personal details</div><div><div class="lock"><span aria-hidden="true">🔒</span><span>' + CG.esc(p.personal.note) + '</span></div>' + reveal + '</div></div></div></div>' + timeline + log;
    var go = document.getElementById('rv-go');
    if (go) go.onclick = async function () {
      var out = document.getElementById('rv-out');
      try {
        var r = await CG_API.post('/api/patients/' + encodeURIComponent(ref) + '/reveal', { case_id: document.getElementById('rv-case').value, reason: document.getElementById('rv-reason').value });
        document.getElementById('rv-reason').value = '';
        out.innerHTML = '<div class="callout amber"><b>Shown for ' + CG.esc(r.case_id) + ' only</b><div class="small">Name: ' + CG.esc(r.name) + ' · Phone: ' + CG.esc(r.phone) + ' · Date of birth: ' + CG.esc(r.dob) + '</div><div class="small muted">' + CG.esc(r.note) + '</div></div>';
        CG.toast('Reveal recorded in the audit log.', 'ok');
      } catch (e) { out.innerHTML = '<div class="callout red">' + CG.esc(e.message) + '</div>'; }
    };
  }

  if (ref) detailView(); else listView();
})();
