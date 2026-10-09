/* Dashboard: one question - what needs me right now? The banner depends on the role. Data: /api/metrics, /api/cases, /api/audit,
   and for knowledge admins /api/lint and /api/prs. */
(async function () {
  var me = await CG.init('index.html');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading the dashboard…');
  var admin = CG.isKnowledgeAdmin();
  var m, cases, audit, lint = [], prs = [], dash;
  try {
    var r = await Promise.all([CG_API.get('/api/metrics'), CG_API.get('/api/cases'), CG_API.get('/api/audit?limit=2000'),
      admin ? CG_API.get('/api/lint') : Promise.resolve([]), admin ? CG_API.get('/api/prs?status=open') : Promise.resolve([]), CG_API.get('/api/dashboard')]);
    m = r[0]; cases = r[1]; audit = r[2]; lint = r[3]; prs = r[4]; dash = r[5];
  } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load the dashboard.'); return; }

  var c = m.counts, RANK = { critical: 4, high: 3, medium: 2, low: 1 }, role = me.role;
  var queue = m.queue.slice().sort(function (a, b) { return (RANK[b.risk] || 0) - (RANK[a.risk] || 0) || b.age_hours - a.age_hours; });
  var openHigh = cases.filter(function (x) { return (x.risk === 'high' || x.risk === 'critical') && ['in_review', 'needs_info', 'escalated'].indexOf(x.state) >= 0; }).length;
  var plural = function (n, one, many) { return n + ' ' + (n === 1 ? one : many); };

  // ------------------------------------------------------------ banner: the single most important action for this role (numbers come from /api/dashboard)
  function banner() {
    var bn = dash.banner;
    return '<div class="banner ' + bn.tone + '" id="banner" data-count="' + bn.count + '"><div class="grow">' + CG.esc(bn.text) + '</div><a class="btn primary" id="banner-go" href="' + CG.esc(bn.href) + '">' + CG.esc(bn.button) + '</a></div>';
  }

  function kpi(label, n, cap) { return '<div class="kpi"><div class="label">' + CG.esc(label) + '</div><div class="num">' + CG.esc(n) + '</div><div class="cap">' + CG.esc(cap) + '</div></div>'; }
  // tiles: each is a link to the list it counts (numbers come from /api/dashboard: the length of that very list)
  var kpis = '<div class="grid g' + dash.tiles.length + '" style="margin-bottom:16px">' + dash.tiles.map(function (t) {
    return '<a class="kpi" data-tile="' + CG.esc(t.key) + '" data-count="' + t.count + '" href="' + CG.esc(t.href) + '"><div class="label">' + CG.esc(t.label) + '</div><div class="num">' + t.count + '</div><div class="cap">' + CG.esc(t.cap) + '</div></a>'; }).join('') + '</div>';

  // ------------------------------------------------------------ trust ladder: one line per type, thin progress bar
  var T = m.trust_thresholds;
  var trust = '<div class="card"><div class="card-title"><h2>Trust ladder</h2></div>' + m.trust.map(function (t) {
    var dot = t.level === 2 ? 'green' : t.level === 1 ? 'amber' : 'grey', pct, cap;
    if (t.level === 0) { pct = Math.min(100, 100 * t.consecutive_agreements / T.l1_streak); cap = t.consecutive_agreements + (t.consecutive_agreements === 1 ? ' approval' : ' approvals') + ' in a row (' + T.l1_streak + ' needed)'; }
    else if (t.level === 1) { pct = Math.min(100, 100 * t.total_reviews / T.l2_reviews); cap = t.total_reviews + ' reviews (' + T.l2_reviews + ' for Level 2)'; }
    else { pct = 100; cap = 'Answers automatically, with audit'; }
    var agree = t.agreement_pct === null ? '' : t.agreement_pct + '% agree';
    return '<div class="trow"><div class="l1"><span class="dot ' + dot + '"></span><span class="name" title="' + CG.esc(CG.what(t.request_type)) + '">' + CG.esc(CG.shortWhat(t.request_type)) + '</span><span class="small muted">Level ' + t.level + ' ' + CG.esc(t.label) + '</span></div>' +
      '<div class="bar' + (pct >= 100 ? ' done' : '') + '"><i style="width:' + pct.toFixed(0) + '%"></i></div><div class="l3"><span>' + CG.esc(cap) + '</span><span>' + CG.esc(agree) + '</span></div></div>';
  }).join('') + '</div>';

  // ------------------------------------------------------------ my queue (max 8, no wrapping)
  var rows = queue.slice(0, 8).map(function (q) {
    return '<tr class="click" data-id="' + CG.esc(q.id) + '"><td class="nw">' + CG.caseLink(q.id) + '</td><td class="ellip" title="' + CG.esc(q.summary) + '"><div class="what">' + CG.esc(CG.shortWhat(q.request_type)) + '</div><div class="subline">' + CG.esc(q.summary) + '</div></td><td class="nw">' + CG.stateTag(q.state) + '</td><td class="nw">' + CG.riskTag(q.risk, true) +
      '</td><td class="nw small" title="' + CG.esc(CG.team(q.team)) + '">' + CG.esc(CG.teamShort(q.team)) + '</td><td class="nw small">' + CG.esc(CG.age(q.age_hours)) + '</td></tr>';
  }).join('');
  var queueCard = '<div class="card"><div class="card-title"><h2>My queue</h2><a class="small" id="see-all" href="' + CG.esc(dash.queue.href) + '">' + 'See all ' + dash.queue.count + ' →' + '</a></div>' +
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

  var counts = {};
  audit.events.forEach(function (e) { counts[e.event] = (counts[e.event] || 0) + 1; });
  var lit = CG.STEPS.map(function (s) { return s[1]; }).filter(function (k) { return counts[k] > 0; });

  el.innerHTML = '<div class="page-head"><h1>Dashboard</h1><p class="muted">What needs you right now, ' + CG.esc(me.name) + '? You can see ' + m.visible_cases + ' case' + (m.visible_cases === 1 ? '' : 's') + '.</p></div>' +
    banner() + kpis + '<div class="grid g-dash">' + trust + queueCard + attCard + '</div>' +
    '<div class="card" style="margin-top:16px"><div class="card-title"><h2>Pipeline</h2><span class="small muted">events recorded for the cases you can see</span></div>' + CG.pipeline(lit, -1, counts) + '</div>';

  CG.$$('tr.click', el).forEach(function (tr) { tr.onclick = function (ev) { if (ev.target.tagName !== 'A') window.location.href = 'case.html?case=' + encodeURIComponent(tr.dataset.id); }; });
})();
