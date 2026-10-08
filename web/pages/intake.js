/* New request: one question - what happens to my request? POST /api/requests, then ONE result card for the state the backend returned. */
(async function () {
  var me = await CG.init('intake.html');
  var el = document.getElementById('content');
  var MAX = (CG.config && CG.config.max_request_chars) || 4000;
  var EXAMPLES = [
    ['S1 · Policy question', 'What supporting documents are accepted for provider record changes?'],
    ['S2 · Missing details', 'Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789.'],
    ['S3 · Policies disagree', 'A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890. Can we reset it?']
  ];

  el.innerHTML = '<div class="page-head"><h1>New request</h1><p class="muted">Type what you need. Personal data is masked on the server before anything is stored or sent to a model.</p></div>' +
    '<div class="card"><label for="req-text" class="sr-only">Request</label><textarea id="req-text" rows="7" placeholder="Describe the request…"></textarea>' +
    '<div class="flex" style="margin-top:10px"><button id="submit" class="btn primary" type="button" disabled>Submit</button><span id="counter" class="small muted mono">0 / ' + MAX + '</span>' +
    '<span class="right small muted">Try an example:</span><span id="examples"></span></div></div>' +
    '<div class="card" id="run" style="display:none"></div>';

  var ta = document.getElementById('req-text'), btn = document.getElementById('submit'), counter = document.getElementById('counter');
  function refresh() {
    var n = ta.value.length;
    counter.textContent = n + ' / ' + MAX;
    counter.style.color = n > MAX ? 'var(--red)' : '';
    btn.disabled = n === 0 || n > MAX;
  }
  ta.addEventListener('input', refresh);
  document.getElementById('examples').innerHTML = EXAMPLES.map(function (x, i) { return '<button type="button" class="chip" data-i="' + i + '">' + CG.esc(x[0]) + '</button>'; }).join('');
  CG.$$('#examples button').forEach(function (b) { b.onclick = function () { ta.value = EXAMPLES[+b.dataset.i][1]; refresh(); ta.focus(); }; });
  refresh();

  function headline(c) {
    var p = c.proposal || {}, team = CG.team(c.assigned_team);
    if (c.result_kind === 'answered') return ['green', 'Answered automatically'];
    if (c.result_kind === 'needs_info') { var n = (p.questions_for_requester || []).length; return ['amber', 'Need ' + n + ' more detail' + (n === 1 ? '' : 's')]; }
    if (c.result_kind === 'refused') {
      var hard = ['CLINICAL', 'ACCESS_DENIED', 'SENSITIVE', 'ACCOUNT_SPECIFIC'].filter(function (x) { return c.reason_codes.indexOf(x) >= 0; })[0] || c.reason_codes[0];
      return ['red', 'Refused: ' + (hard ? CG.reasonLabel(hard).toLowerCase() : 'not answerable') + ' → ' + team];
    }
    return ['blue', 'Sent to ' + team + ' for review'];
  }

  function resultCard(c) {
    var p = c.proposal || {}, h = headline(c);
    var body = '';
    if (c.result_kind === 'needs_info') body = '<p class="muted">Please reply to this one message with:</p><ol class="stack" style="margin:8px 0 0 20px;padding:0">' + (p.questions_for_requester || []).map(function (q) { return '<li>' + CG.esc(q) + '</li>'; }).join('') + '</ol>';
    else if (c.result_kind === 'answered') body = '<p style="white-space:pre-wrap">' + CG.esc(p.answer_text) + '</p>';
    else if (c.result_kind === 'refused') body = '<p class="muted" style="white-space:pre-wrap">' + CG.esc(p.answer_text) + '</p>';
    else body = '<div style="margin:4px 0">' + CG.reasonChips(c.reason_codes) + '</div>' + (c.approver_role ? '<p class="small muted">Needs a ' + CG.esc(CG.role(c.approver_role).toLowerCase()) + ' to decide.</p>' : '');
    var pii = c.pii_types.length ? '<div class="row"><div class="label">Masked</div><div>' + c.pii_types.map(function (t) { return '<span class="chip grey">' + CG.esc(CG.piiLabel(t)) + '</span>'; }).join('') + '</div></div>' :
      '<div class="row"><div class="label">Masked</div><div class="muted">No personal data found.</div></div>';
    var src = (p.citations || []).length ? '<div class="row"><div class="label">Sources</div><div>' + p.citations.map(CG.citationChip).join('') + '</div></div>' : '';
    return '<div class="headline ' + h[0] + '">' + CG.esc(h[1]) + '</div>' + body + '<div class="rows" style="margin-top:12px">' + pii +
      '<div class="row"><div class="label">Confidence</div><div>' + (CG.band(c.confidence) || '<span class="muted">not scored</span>') + '</div></div>' + src + '</div>' +
      '<div class="flex" style="margin-top:14px"><a class="btn primary" href="case.html?case=' + encodeURIComponent(c.id) + '">Open case ' + CG.esc(c.id) + '</a></div>';
  }

  btn.onclick = async function () {
    var text = ta.value;
    if (!text.trim() || text.length > MAX) return;
    ta.value = '';                                  // cleared at once: the browser keeps no copy after sending
    refresh();
    btn.disabled = true;
    var box = document.getElementById('run');
    box.style.display = '';
    var step = 0;
    function paint(lit) { box.innerHTML = '<div class="label" style="margin-bottom:8px">Reasoning over policy and precedents…</div>' + CG.pipeline(lit || CG.STEPS.slice(0, step).map(function (s) { return s[1]; }), step) + '<p class="muted" style="margin-top:10px"><span class="spinner"></span> Working…</p>'; }
    paint();
    var timer = setInterval(function () { step = Math.min(step + 1, CG.STEPS.length - 1); paint(); }, 450);   // cosmetic progress only
    try {
      var res = await CG_API.post('/api/requests', { text: text });
      text = null;
      clearInterval(timer);
      var c = res.case, events = [];
      try { events = (await CG_API.get('/api/cases/' + encodeURIComponent(c.id) + '/audit')).map(function (e) { return e.event; }); } catch (e) { /* pipeline row stays unlit */ }
      box.innerHTML = '<div style="margin-bottom:12px">' + CG.pipeline(events, -1) + '</div>' + resultCard(c);
    } catch (e) {
      clearInterval(timer);
      box.innerHTML = '<div class="headline red">The request could not be processed</div><p class="muted">' + CG.esc(e.message) + '</p>';
      CG.fail(e);
    }
    refresh();
  };
})();
