/* Dashboard: one question - what needs me right now? The banner depends on the role. Data: /api/metrics, /api/cases, /api/scorecard, /api/audit,
   and for knowledge admins /api/lint and /api/prs. */
(async function () {
  var me = await CG.init('index.html');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading the dashboard…');
  var admin = CG.isKnowledgeAdmin();
  var m, cases, sc, audit, lint = [], prs = [];
  try {
    var r = await Promise.all([CG_API.get('/api/metrics'), CG_API.get('/api/cases'), CG_API.get('/api/scorecard'), CG_API.get('/api/audit?limit=2000'),
      admin ? CG_API.get('/api/lint') : Promise.resolve([]), admin ? CG_API.get('/api/prs?status=open') : Promise.resolve([])]);
    m = r[0]; cases = r[1]; sc = r[2]; audit = r[3]; lint = r[4]; prs = r[5];
  } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load the dashboard.'); return; }

  var c = m.counts, RANK = { critical: 4, high: 3, medium: 2, low: 1 }, role = me.role;
  var queue = m.queue.slice().sort(function (a, b) { return (RANK[b.risk] || 0) - (RANK[a.risk] || 0) || b.age_hours - a.age_hours; });
  var openHigh = cases.filter(function (x) { return (x.risk === 'high' || x.risk === 'critical') && ['in_review', 'needs_info', 'escalated'].indexOf(x.state) >= 0; }).length;
  var plural = function (n, one, many) { return n + ' ' + (n === 1 ? one : many); };

  // ------------------------------------------------------------ role-aware attention banner
  function banner() {
    function b(kind, html, btnHref, btnText) { return '<div class="banner ' + kind + '"><div class="grow">' + html + '</div><a class="btn primary" href="' + btnHref + '">' + CG.esc(btnText) + '</a></div>'; }
    if (role === 'ops_employee') {
      var withRev = cases.filter(function (x) { return x.state === 'in_review' || x.state === 'escalated'; }).length;
      var need = cases.filter(function (x) { return x.state === 'needs_info'; }).length;
      if (!withRev && !need) return b('green', '<b>You have no open requests.</b> Submit one to see it flow through.', 'intake.html', 'New request');
      return b(need ? 'amber' : 'green', '<b>Your requests:</b> ' + withRev + ' with reviewers, ' + need + ' need more details from you.', need ? 'case.html?state=needs_info' : 'case.html', need ? 'See requests needing details' : 'See my requests');
    }
    if (role === 'knowledge_owner') {
      var conflicts = lint.filter(function (x) { return x.code === 'CONTRADICTION'; }).length, gaps = lint.filter(function (x) { return x.code === 'ESCALATION_HOTSPOT'; }).length;
      var txt = '<b>' + plural(conflicts, 'open policy conflict', 'open policy conflicts') + ', ' + plural(gaps, 'knowledge gap', 'knowledge gaps') + ' and ' + plural(prs.length, 'policy update', 'policy updates') + ' waiting.</b>';
      if (!conflicts && !gaps && !prs.length) return b('green', '<b>The Second Brain is in good shape.</b> No conflicts, gaps or policy updates waiting.', 'knowledge.html', 'Open Knowledge');
      return b(conflicts ? 'red' : 'amber', txt, prs.length ? 'knowledge.html?tab=prs' : 'knowledge.html?tab=lint', prs.length ? 'Review policy updates' : 'See what needs attention');
    }
    if (role === 'auditor') {
      var count = function (n) { return audit.events.filter(function (e) { return e.event === n; }).length; };
      var blocked = count('guard_blocked'), denied = count('review_denied') + count('pr_denied') + count('reset_denied');
      if (!blocked && !denied) return b('green', '<b>No blocked or denied events recently.</b>', 'audit.html', 'Open audit');
      return b('amber', '<b>' + plural(blocked, 'blocked request', 'blocked requests') + ' and ' + plural(denied, 'denial', 'denials') + ' in the recent log.</b>', blocked ? 'audit.html?event=guard_blocked' : 'audit.html', 'See blocked requests');
    }
    // approvers: only cases they can approve
    var mine = queue.filter(function (q) { return q.can_approve; });
    if (!mine.length) {
      return queue.length ? b('green', '<b>Nothing for you to approve.</b> ' + plural(queue.length, 'case is', 'cases are') + ' with other reviewers or waiting for the requester.', 'case.html', 'See all cases') :
        b('green', '<b>Nothing is waiting for you.</b> New requests that need a person will show up here.', 'intake.html', 'New request');
    }
    var top = mine[0], conf = mine.filter(function (q) { return q.reason_codes.indexOf('POLICY_CONFLICT') >= 0; }).length;
    return b((RANK[top.risk] || 0) >= 3 || conf ? 'red' : 'amber', '<b>' + plural(mine.length, 'case is', 'cases are') + ' waiting for you to approve.</b> Highest risk: ' + CG.esc(top.id) + ' (' + CG.esc(top.risk) + ' risk, ' +
      CG.esc(CG.shortWhat(top.request_type).toLowerCase()) + ')' + (conf ? '. ' + plural(conf, 'case has', 'cases have') + ' policies that disagree' : '') + '.', 'case.html?case=' + encodeURIComponent(top.id) + '&decide=1', 'Review ' + top.id);
  }

  function kpi(label, n, cap) { return '<div class="kpi"><div class="label">' + CG.esc(label) + '</div><div class="num">' + CG.esc(n) + '</div><div class="cap">' + CG.esc(cap) + '</div></div>'; }
  var kpis = '<div class="grid g4" style="margin-bottom:16px">' + kpi('Waiting for review', c.awaiting_review + c.escalated, 'a person must decide') + kpi('Needs info', c.needs_info, 'waiting for the requester') +
    kpi('High or critical risk', openHigh, 'open cases') + kpi('Answered automatically', c.auto_answered, 'no person needed') + '</div>';

  // ------------------------------------------------------------ trust ladder: one line per type, thin progress bar
  var T = m.trust_thresholds;
  var trust = '<div class="card"><div class="card-title"><h2>Trust ladder</h2></div>' + m.trust.map(function (t) {
    var dot = t.level === 2 ? 'green' : t.level === 1 ? 'amber' : 'grey', pct, cap;
    if (t.level === 0) { pct = Math.min(100, 100 * t.consecutive_agreements / T.l1_streak); cap = t.consecutive_agreements + (t.consecutive_agreements === 1 ? ' approval' : ' approvals') + ' in a row (' + T.l1_streak + ' needed)'; }
    else if (t.level === 1) { pct = Math.min(100, 100 * t.total_reviews / T.l2_reviews); cap = t.total_reviews + ' reviews (' + T.l2_reviews + ' for Level 2)'; }
    else { pct = 100; cap = 'Answers automatically, with audit'; }
    var agree = t.agreement_pct === null ? '' : t.agreement_pct + '% agree';
    return '<div class="trow"><div class="l1"><span class="dot ' + dot + '"></span><span class="name" title="' + CG.esc(t.request_type) + '">' + CG.esc(CG.what(t.request_type)) + '</span><span class="small muted">Level ' + t.level + ' ' + CG.esc(t.label) + '</span></div>' +
      '<div class="bar' + (pct >= 100 ? ' done' : '') + '"><i style="width:' + pct.toFixed(0) + '%"></i></div><div class="l3"><span>' + CG.esc(cap) + '</span><span>' + CG.esc(agree) + '</span></div></div>';
  }).join('') + '</div>';

  // ------------------------------------------------------------ my queue (max 8, no wrapping)
  var rows = queue.slice(0, 8).map(function (q) {
    return '<tr class="click" data-id="' + CG.esc(q.id) + '"><td class="nw">' + CG.caseLink(q.id) + '</td><td class="nw" title="' + CG.esc(CG.what(q.request_type)) + '">' + CG.esc(CG.shortWhat(q.request_type)) + '</td><td class="nw">' + CG.stateTag(q.state) + '</td><td class="nw">' + CG.riskTag(q.risk, true) +
      '</td><td class="nw small" title="' + CG.esc(CG.team(q.team)) + '">' + CG.esc(CG.teamShort(q.team)) + '</td><td class="nw small">' + CG.esc(CG.age(q.age_hours)) + '</td></tr>';
  }).join('');
  var queueCard = '<div class="card"><div class="card-title"><h2>My queue</h2><a class="small" href="case.html">' + (queue.length > 8 ? 'See all ' + queue.length : 'All cases') + '</a></div>' +
    (queue.length ? '<div class="tablewrap"><table class="compact"><thead><tr><th>Case</th><th>What</th><th>State</th><th>Risk</th><th>Team</th><th>Age</th></tr></thead><tbody>' + rows + '</tbody></table></div>' :
      CG.empty('Nothing is waiting. Submit a request from New request to see it flow through.')) + '</div>';

  // ------------------------------------------------------------ needs attention: knowledge problems + the oldest waiting case
  var LINT = { CONTRADICTION: ['Conflict', 'red'], EXPIRED_LINKED: ['Expired', 'amber'], STALE_PRECEDENT: ['Stale', 'grey'], ESCALATION_HOTSPOT: ['Gap', 'amber'], ORPHAN: ['Unused', 'grey'], MISSING_TEAM: ['Team', 'amber'], PII_LEAK: ['Privacy', 'red'] };
  function lintSentence(x) {                                  // short plain sentences; the full finding is on the Knowledge page
    var ids = x.page_ids, topic = (x.message.match(/about '([^']+)'/) || [])[1], n = (x.message.match(/^(\d+)/) || [])[1];
    if (x.code === 'CONTRADICTION') return ids.join(' and ') + ' disagree on the same rule, so answers about it are capped at Medium confidence.';
    if (x.code === 'ESCALATION_HOTSPOT') return (n ? n + ' requests' : 'Many requests') + (topic ? ' about \u2018' + topic + '\u2019' : '') + ' have no approved policy.';
    if (x.code === 'EXPIRED_LINKED') return ids[0] + ' still links to ' + (ids[1] || 'an expired page') + ', which is expired.';
    if (x.code === 'STALE_PRECEDENT') return 'Past case ' + ids[0] + ' relies on an old policy version and is not used.';
    return x.message;
  }
  var cards = lint.filter(function (x) { return x.severity !== 'info'; }).sort(function (a, b) { return (a.severity === 'error' ? 0 : 1) - (b.severity === 'error' ? 0 : 1); }).slice(0, 4).map(function (x) {
    var k = LINT[x.code] || [CG.human(x.severity), 'grey'];
    var shown = x.page_ids.slice(0, 2).join(' \u00b7 ') + (x.page_ids.length > 2 ? ' +' + (x.page_ids.length - 2) : '');
    return '<a class="att ' + k[1] + '" href="' + CG.pageLink(x.page_ids[0]) + '"><div class="top">' + CG.tag(k[0], k[1]) + '<span class="small faint">' + CG.esc(shown) + '</span></div><div class="small">' + CG.esc(lintSentence(x)) + '</div></a>';
  });
  var oldest = m.queue.slice().sort(function (a, b) { return b.age_hours - a.age_hours; })[0];
  if (oldest) cards.push('<a class="att blue" href="case.html?case=' + encodeURIComponent(oldest.id) + '"><div class="top">' + CG.tag('Oldest waiting', 'blue') + '<span class="small faint">' + CG.esc(CG.age(oldest.age_hours)) + '</span></div>' +
    '<div class="small"><b>' + CG.esc(oldest.id) + '</b> · ' + CG.esc(CG.shortWhat(oldest.request_type)) + ' · ' + CG.esc(CG.human(oldest.state)) + '</div></a>');
  var attCard = '<div class="card"><div class="card-title"><h2>Needs attention</h2></div>' + (cards.length ? cards.join('') : CG.empty('Nothing needs attention.')) + '</div>';

  // ------------------------------------------------------------ collapsed sections
  function pct(v) { return v && v.value !== null && v.value !== undefined ? Math.round(v.value) + '%' : 'n/a'; }
  function evalCard(label, blind, adj, meta) {
    function stat(name, k) {
      return '<div><div class="label">' + name + '</div><div class="bigstat">' + pct(blind[k]) + '</div>' + (adj ? '<div class="small muted">adjudicated ' + pct(adj[k]) + '</div>' : '<div class="small muted">&nbsp;</div>') + '</div>';
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
    return '<tr><td>' + CG.esc(CG.what(g.request_type)) + ' <span class="small muted">' + CG.esc(g.topic || '') + '</span></td><td>' + g.count + '</td><td>' + g.est_hours_saved + ' h</td></tr>'; }).join('') + '</tbody></table></div>' : CG.empty('No recurring knowledge gaps.');

  var counts = {};
  audit.events.forEach(function (e) { counts[e.event] = (counts[e.event] || 0) + 1; });
  var lit = CG.STEPS.map(function (s) { return s[1]; }).filter(function (k) { return counts[k] > 0; });

  el.innerHTML = '<div class="page-head"><h1>Dashboard</h1><p class="muted">What needs you right now, ' + CG.esc(me.name) + '? You can see ' + m.visible_cases + ' case' + (m.visible_cases === 1 ? '' : 's') + '.</p></div>' +
    banner() + kpis + '<div class="grid g-dash">' + trust + queueCard + attCard + '</div>' +
    '<details class="fold"><summary>Evaluation</summary><div class="fold-body"><p class="small muted">Blind numbers are scored as written; adjudicated numbers use the reviewed expectations.</p>' + evalHtml + '</div></details>' +
    '<details class="fold"><summary>Knowledge gap radar</summary><div class="fold-body">' + gap + '</div></details>' +
    '<div class="card" style="margin-top:16px"><div class="card-title"><h2>Pipeline</h2><span class="small muted">events recorded for the cases you can see</span></div>' + CG.pipeline(lit, -1, counts) + '</div>';

  CG.$$('tr.click', el).forEach(function (tr) { tr.onclick = function (ev) { if (ev.target.tagName !== 'A') window.location.href = 'case.html?case=' + encodeURIComponent(tr.dataset.id); }; });
})();
