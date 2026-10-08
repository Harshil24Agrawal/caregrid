/* Dashboard: one question - what needs me right now? Data: /api/metrics, /api/cases, /api/scorecard, /api/audit. */
(async function () {
  var me = await CG.init('index.html');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading the dashboard…');
  var m, cases, sc, audit;
  try {
    var r = await Promise.all([CG_API.get('/api/metrics'), CG_API.get('/api/cases'), CG_API.get('/api/scorecard'), CG_API.get('/api/audit?limit=2000')]);
    m = r[0]; cases = r[1]; sc = r[2]; audit = r[3];
  } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load the dashboard.'); return; }

  var c = m.counts, RANK = { critical: 4, high: 3, medium: 2, low: 1 };
  var queue = m.queue.slice().sort(function (a, b) { return (RANK[b.risk] || 0) - (RANK[a.risk] || 0) || b.age_hours - a.age_hours; });
  var waiting = c.awaiting_review + c.escalated;
  var conflicts = queue.filter(function (q) { return q.reason_codes.indexOf('POLICY_CONFLICT') >= 0; });
  var openHigh = cases.filter(function (x) { return (x.risk === 'high' || x.risk === 'critical') && ['in_review', 'needs_info', 'escalated'].indexOf(x.state) >= 0; }).length;
  var top = queue[0];

  // ---- attention banner (computed from the data above)
  var banner;
  if (!queue.length) {
    banner = '<div class="banner green"><div class="grow"><b>Nothing is waiting for you.</b> New requests that need a person will show up here.</div><a class="btn primary" href="intake.html">New request</a></div>';
  } else {
    var hot = (RANK[top.risk] || 0) >= 3 || conflicts.length > 0;
    banner = '<div class="banner ' + (hot ? 'red' : 'amber') + '"><div class="grow"><b>' + waiting + ' case' + (waiting === 1 ? ' is' : 's are') + ' waiting for review' + (c.needs_info ? ' and ' + c.needs_info + ' for the requester' : '') + '.</b> ' +
      'Highest risk: ' + CG.esc(top.id) + ' (' + CG.esc(top.risk) + ' risk, ' + CG.esc(CG.what(top.request_type).toLowerCase()) + ')' +
      (conflicts.length ? '. ' + conflicts.length + ' with policies that disagree' : '') + '.</div>' +
      '<a class="btn primary" href="case.html?case=' + encodeURIComponent(top.id) + '&decide=1">Review ' + CG.esc(top.id) + '</a></div>';
  }

  function kpi(label, n, cap) { return '<div class="kpi"><div class="label">' + CG.esc(label) + '</div><div class="num">' + CG.esc(n) + '</div><div class="cap">' + CG.esc(cap) + '</div></div>'; }
  var kpis = '<div class="grid g4" style="margin-bottom:16px">' + kpi('Waiting for review', waiting, 'a person must decide') + kpi('Needs info', c.needs_info, 'waiting for the requester') +
    kpi('High or critical risk', openHigh, 'open cases') + kpi('Answered automatically', c.auto_answered, 'no person needed') + '</div>';

  // ---- trust ladder
  var trust = '<div class="card"><div class="card-title"><h2>Trust ladder</h2></div><p class="small muted" style="margin-bottom:6px">Level 1 after ' + m.trust_thresholds.l1_streak + ' agreements in a row.</p>' +
    m.trust.map(function (t) {
      var dot = t.level === 2 ? 'green' : t.level === 1 ? 'amber' : 'grey';
      return '<div class="dotrow"><span class="dot ' + dot + '"></span><span class="name" title="' + CG.esc(t.request_type) + '">' + CG.esc(CG.what(t.request_type)) + '</span><span class="stat">L' + t.level + ' · ' + t.consecutive_agreements + '/' + m.trust_thresholds.l1_streak +
        ' · ' + (t.agreement_pct === null ? 'no reviews' : t.agreement_pct + '%') + '</span></div>';
    }).join('') + '</div>';

  // ---- my queue (max 8)
  var rows = queue.slice(0, 8).map(function (q) {
    return '<tr class="click" data-id="' + CG.esc(q.id) + '"><td>' + CG.caseLink(q.id) + '</td><td>' + CG.esc(CG.what(q.request_type)) + '</td><td>' + CG.stateTag(q.state) + '</td><td>' + CG.riskTag(q.risk) +
      '</td><td class="small">' + CG.esc(CG.team(q.team)) + '</td><td class="nowrap small">' + CG.esc(CG.age(q.age_hours)) + '</td></tr>';
  }).join('');
  var queueCard = '<div class="card"><div class="card-title"><h2>My queue</h2>' + (queue.length > 8 ? '<a class="small" href="case.html">See all ' + queue.length + '</a>' : '<a class="small" href="case.html">All cases</a>') + '</div>' +
    (queue.length ? '<div class="tablewrap"><table><thead><tr><th>Case</th><th>What</th><th>State</th><th>Risk</th><th>Team</th><th>Age</th></tr></thead><tbody>' + rows + '</tbody></table></div>' :
      CG.empty('Nothing is waiting. Submit a request from New request to see it flow through.')) + '</div>';

  // ---- needs attention
  function attention(q) {
    var hasC = q.reason_codes.indexOf('POLICY_CONFLICT') >= 0, gap = q.reason_codes.indexOf('POLICY_GAP') >= 0;
    var tag = hasC ? ['Conflict', 'red'] : q.risk === 'critical' ? ['Critical', 'red'] : q.risk === 'high' ? ['High', 'red'] : gap ? ['Gap', 'amber'] : q.state === 'needs_info' ? ['Needs info', 'amber'] : ['Review', 'blue'];
    var why = q.reason_codes.length ? q.reason_codes.map(CG.reasonLabel).join(', ') : CG.human(q.state);
    return '<a class="att ' + tag[1] + '" href="case.html?case=' + encodeURIComponent(q.id) + '"><div class="top">' + CG.tag(tag[0], tag[1]) + '<span class="small faint">' + CG.esc(CG.age(q.age_hours)) + '</span></div>' +
      '<div class="small"><b>' + CG.esc(q.id) + '</b> · ' + CG.esc(CG.what(q.request_type)) + '. ' + CG.esc(why) + '.</div></a>';
  }
  var sev = function (q) { return (q.reason_codes.indexOf('POLICY_CONFLICT') >= 0 ? 10 : 0) + (RANK[q.risk] || 0) * 2 + (q.reason_codes.indexOf('POLICY_GAP') >= 0 ? 1 : 0); };
  var att = queue.slice().sort(function (a, b) { return sev(b) - sev(a); }).slice(0, 4);
  var attCard = '<div class="card"><div class="card-title"><h2>Needs attention</h2></div>' + (att.length ? att.map(attention).join('') : CG.empty('Nothing needs attention.')) + '</div>';

  // ---- collapsed: evaluation and gap radar
  function pct(v) { return v && v.value !== null && v.value !== undefined ? Math.round(v.value) + '%' : 'n/a'; }
  function evalCard(label, blind, adj, meta) {
    function stat(name, k) {
      return '<div><div class="label">' + name + '</div><div class="bigstat">' + pct(blind[k]) + '</div>' +
        (adj ? '<div class="small muted">adjudicated ' + pct(adj[k]) + '</div>' : '<div class="small muted">&nbsp;</div>') + '</div>';
    }
    return '<div class="card" style="margin-top:12px"><div class="card-title"><h3>' + CG.esc(label) + '</h3><span class="small muted">' + CG.esc(meta) + '</span></div><div class="cmpgrid">' +
      stat('Request type', 'request_type_accuracy') + stat('Routing', 'routing_first_time_right') + stat('Safety', 'safety_pass_rate') + '</div></div>';
  }
  var evalHtml = sc.main ? evalCard('Main set (regression gate)', sc.main.metrics, null, sc.main.rows + ' rows · ' + sc.main.llm_provider) : CG.empty('No scorecard yet. Run python -m caregrid.cli eval.');
  ['mock', 'env'].forEach(function (k) {
    var h = sc.heldout[k];
    if (h) evalHtml += evalCard('Held-out, ' + (k === 'env' ? h.llm_provider : 'mock') + ' (blind as written)', h.metrics, h.adjudicated && h.adjudicated.metrics, h.rows + ' rows');
  });
  var gap = m.gap_radar.length ? '<div class="tablewrap"><table><thead><tr><th>Request type</th><th>Count</th><th>Est. hours saved</th></tr></thead><tbody>' + m.gap_radar.map(function (g) {
    return '<tr><td>' + CG.esc(CG.what(g.request_type)) + ' <span class="small muted">' + CG.esc(g.topic || '') + '</span></td><td>' + g.count + '</td><td>' + g.est_hours_saved + ' h</td></tr>';
  }).join('') + '</tbody></table></div>' : CG.empty('No recurring knowledge gaps.');

  // ---- pipeline chips with real event counts
  var counts = {};
  audit.events.forEach(function (e) { counts[e.event] = (counts[e.event] || 0) + 1; });
  var lit = CG.STEPS.map(function (s) { return s[1]; }).filter(function (k) { return counts[k] > 0; });

  el.innerHTML = '<div class="page-head"><h1>Dashboard</h1><p class="muted">What needs you right now, ' + CG.esc(me.name) + '? You can see ' + m.visible_cases + ' case' + (m.visible_cases === 1 ? '' : 's') + '.</p></div>' +
    banner + kpis + '<div class="grid g-dash">' + trust + queueCard + attCard + '</div>' +
    '<details class="fold"><summary>Evaluation</summary><div class="fold-body"><p class="small muted">Blind numbers are scored as written; adjudicated numbers use the reviewed expectations.</p>' + evalHtml + '</div></details>' +
    '<details class="fold"><summary>Knowledge gap radar</summary><div class="fold-body">' + gap + '</div></details>' +
    '<div class="card" style="margin-top:16px"><div class="card-title"><h2>Pipeline</h2><span class="small muted">events recorded for the cases you can see</span></div>' + CG.pipeline(lit, -1, counts) + '</div>';

  CG.$$('tr.click', el).forEach(function (tr) { tr.onclick = function (ev) { if (ev.target.tagName !== 'A') window.location.href = 'case.html?case=' + encodeURIComponent(tr.dataset.id); }; });
})();
