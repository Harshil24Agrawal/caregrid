/* Dashboard: tiles, my queue, trust ladder, gap radar, queue ageing, cost split, scorecard. Everything comes from /api/metrics, /api/cases, /api/scorecard. */
(async function () {
  var me = await CG.init('index.html', 'Dashboard');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading the dashboard…');
  var m, cases, sc;
  try {
    var r = await Promise.all([CG_API.get('/api/metrics'), CG_API.get('/api/cases'), CG_API.get('/api/scorecard')]);
    m = r[0]; cases = r[1]; sc = r[2];
  } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load the dashboard.'); return; }

  var c = m.counts;
  var openHigh = cases.filter(function (x) { return (x.risk === 'high' || x.risk === 'critical') && ['in_review', 'needs_info', 'escalated'].indexOf(x.state) >= 0; }).length;
  function tile(label, value, sub, icon) {
    return '<div class="tile"><div class="flex items-center justify-between"><span class="eyebrow">' + CG.esc(label) + '</span><span class="material-symbols-outlined text-slate-400">' + icon +
      '</span></div><div class="num mt-1">' + CG.esc(value) + '</div><div class="text-body-sm text-slate-500 mt-1">' + CG.esc(sub || '') + '</div></div>';
  }

  var queueRows = m.queue.map(function (q) {
    return '<tr data-state="' + CG.esc(q.state) + '" data-risk="' + CG.esc(q.risk) + '" data-team="' + CG.esc(q.team) + '">' +
      '<td>' + CG.caseLink(q.id) + '</td><td class="mono text-xs">' + CG.esc(q.request_type) + '</td><td>' + CG.stateChip(q.state) + '</td><td>' + CG.riskChip(q.risk) +
      '</td><td>' + CG.bandChip(q.band, q.score) + '</td><td class="mono text-xs">' + CG.esc(q.team || '') + '</td><td class="mono text-xs whitespace-nowrap">' + CG.esc(CG.age(q.age_hours)) +
      '</td><td>' + CG.reasonChips(q.reason_codes) + '</td><td><a class="btn btn-sm" href="case.html?case=' + encodeURIComponent(q.id) + '">Open</a></td></tr>';
  }).join('');

  var states = Array.from(new Set(m.queue.map(function (q) { return q.state; })));
  var teams = Array.from(new Set(m.queue.map(function (q) { return q.team; }).filter(Boolean)));
  var sel = function (id, label, opts) {
    return '<label class="text-body-sm text-slate-600">' + label + ' <select id="' + id + '" class="border border-slate-200 rounded-lg text-sm"><option value="">All</option>' +
      opts.map(function (o) { return '<option>' + CG.esc(o) + '</option>'; }).join('') + '</select></label>';
  };

  var trustRows = m.trust.map(function (t) {
    return '<tr><td class="mono text-xs">' + CG.esc(t.request_type) + '</td><td>' + CG.chip(t.level + ' ' + t.label, t.level === 2 ? 'chip-green' : t.level === 1 ? 'chip-blue' : 'chip-slate') +
      '</td><td class="mono text-xs">' + t.consecutive_agreements + ' / ' + m.trust_thresholds.l1_streak + '</td><td class="mono text-xs">' +
      (t.agreement_pct === null ? '-' : t.agreement_pct + '%') + ' (' + t.total_reviews + ' reviews)</td></tr>';
  }).join('');

  var gapRows = m.gap_radar.map(function (g) {
    var hot = g.reason_code === 'POLICY_GAP' && g.count >= 3;
    return '<tr class="' + (hot ? 'bg-amber-50' : '') + '"><td class="mono text-xs">' + CG.esc(g.request_type) + '</td><td>' + CG.chip(g.reason_code, 'chip-slate') +
      '</td><td>' + CG.esc(g.topic || '') + '</td><td class="mono">' + g.count + '</td><td class="mono">' + g.avg_hours_in_queue + ' h</td><td class="mono">' + g.est_hours_saved + ' h</td></tr>';
  }).join('');

  var maxHrs = Math.max.apply(null, [1].concat(m.queue_aging.map(function (a) { return a.max_hours; })));
  var agingRows = m.queue_aging.map(function (a) {
    return '<tr><td>' + CG.stateChip(a.state) + '</td><td class="mono text-xs">' + CG.esc(a.team) + '</td><td class="mono">' + a.count + '</td><td class="mono">' + a.avg_hours +
      ' h</td><td style="width:35%"><div style="height:8px;border-radius:9999px;background:#e2e8f0"><div style="height:8px;border-radius:9999px;background:#0f172a;width:' +
      Math.max(2, Math.round(100 * a.max_hours / maxHrs)) + '%"></div></div><span class="mono text-xs">max ' + a.max_hours + ' h</span></td></tr>';
  }).join('');

  var cs = m.cost_split;
  var costHtml = cs.requests === 0 ? CG.empty('No pipeline requests yet.') :
    '<div class="seg-bar"><div class="seg" style="flex:' + Math.max(cs.no_llm_pct, 0.5) + ';background:#94a3b8">no LLM ' + cs.no_llm_pct + '%</div><div class="seg" style="flex:' +
    Math.max(cs.light_only_pct, 0.5) + ';background:#0d9488">light ' + cs.light_only_pct + '%</div><div class="seg" style="flex:' + Math.max(cs.light_and_strong_pct, 0.5) +
    ';background:#4f46e5">light + strong ' + cs.light_and_strong_pct + '%</div></div><p class="text-body-sm text-slate-500 mt-2">' + cs.requests + ' pipeline requests visible to you (seeded history excluded).</p>';

  function pct(v) { return v.value === null ? 'n/a' : v.value.toFixed(0) + '%'; }
  function scoreTiles(card, label) {
    if (!card) return '';
    var mm = card.metrics;
    var rows = [['Type accuracy', mm.request_type_accuracy], ['Routing first-time-right', mm.routing_first_time_right], ['Missing-field recall', mm.missing_field_recall],
      ['Citation validity', mm.citation_validity], ['Safety pass', mm.safety_pass_rate], ['Correct abstention', mm.correct_abstention_rate]];
    return '<h3 class="font-headline-sm text-headline-sm mt-3 mb-2">' + CG.esc(label) + ' <span class="text-body-sm text-slate-500">' + CG.esc(card.rows + ' rows · ' + card.llm_provider + ' · ' + card.file) + '</span></h3>' +
      '<div class="grid grid-cols-2 md:grid-cols-6 gap-3">' + rows.map(function (x) {
        return '<div class="tile"><div class="eyebrow">' + CG.esc(x[0]) + '</div><div class="num">' + pct(x[1]) + '</div><div class="text-body-sm text-slate-500">' + x[1].num + '/' + x[1].den + '</div></div>';
      }).join('') + '</div>';
  }
  var heldBlocks = '';
  ['mock', 'env'].forEach(function (k) {
    var card = sc.heldout[k];
    if (!card) return;
    heldBlocks += scoreTiles({ metrics: card.metrics, rows: card.rows, llm_provider: card.llm_provider, file: card.file }, 'Held-out, blind (as written) - ' + k);
    if (card.adjudicated) heldBlocks += scoreTiles({ metrics: card.adjudicated.metrics, rows: card.rows, llm_provider: card.llm_provider, file: card.adjudicated.file }, 'Held-out, adjudicated - ' + k);
  });

  el.innerHTML =
    '<div class="flex flex-wrap items-end justify-between gap-3 mb-4"><div><div class="eyebrow">Overview</div><h1 class="font-headline-xl text-headline-xl">Operational Intelligence Dashboard</h1>' +
    '<p class="text-body-md text-slate-600">AI prepares the decision. Humans own the decision. Workflows execute the approved action.</p></div>' +
    '<a class="btn btn-primary" href="intake.html"><span class="material-symbols-outlined text-base">add_circle</span>New request</a></div>' +
    '<div class="card mb-4 flex items-center gap-2 text-body-sm"><span class="material-symbols-outlined text-slate-500">verified_user</span>Viewing as <b>' + CG.esc(me.name) + '</b> (' +
    CG.esc(me.role + (me.team ? ' · ' + me.team : '')) + '). ' + m.visible_cases + ' case(s) are visible to your role.</div>' +
    '<div class="grid grid-cols-2 md:grid-cols-6 gap-3 mb-4">' +
    tile('Open', c.open, c.total + ' cases in total', 'inbox') + tile('Needs info', c.needs_info, 'waiting for the requester', 'pending_actions') +
    tile('In review', c.awaiting_review, 'pending approval', 'rule') + tile('High / critical', openHigh, 'open cases', 'crisis_alert') +
    tile('Escalated', c.escalated, 'to a senior', 'flag') + tile('Auto-answered', c.auto_answered, 'with audit', 'smart_toy') + '</div>' +
    '<section class="card mb-4"><div class="flex flex-wrap items-center justify-between gap-2 mb-3"><h2 class="font-headline-md text-headline-md">My queue <span class="text-body-sm text-slate-500">(' + m.queue.length +
    ')</span></h2><div class="flex gap-3">' + sel('f-state', 'State', states) + sel('f-risk', 'Risk', ['low', 'medium', 'high', 'critical']) + sel('f-team', 'Team', teams) + '</div></div>' +
    (m.queue.length ? '<div class="overflow-x-auto"><table class="data"><thead><tr><th>Case</th><th>Type</th><th>State</th><th>Risk</th><th>Confidence</th><th>Team</th><th>Age</th><th>Reasons</th><th></th></tr></thead><tbody id="queue-body">' +
      queueRows + '</tbody></table></div>' : CG.empty('Nothing is waiting for you.')) + '</section>' +
    '<div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4"><section class="card"><h2 class="font-headline-md text-headline-md mb-1">Trust ladder</h2><p class="text-body-sm text-slate-500 mb-3">Level 1 after ' +
    m.trust_thresholds.l1_streak + ' consecutive human agreements.</p><div class="overflow-x-auto"><table class="data"><thead><tr><th>Request type</th><th>Level</th><th>Streak</th><th>Agreement</th></tr></thead><tbody>' +
    trustRows + '</tbody></table></div></section>' +
    '<section class="card"><h2 class="font-headline-md text-headline-md mb-1">Knowledge gap radar</h2><p class="text-body-sm text-slate-500 mb-3">Recurring escalations that point at a missing or conflicting article.</p>' +
    (m.gap_radar.length ? '<div class="overflow-x-auto"><table class="data"><thead><tr><th>Type</th><th>Reason</th><th>Topic</th><th>Count</th><th>Avg in queue</th><th>Est. saved</th></tr></thead><tbody>' + gapRows + '</tbody></table></div>' : CG.empty('No recurring gaps.')) +
    '</section></div>' +
    '<div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4"><section class="card"><h2 class="font-headline-md text-headline-md mb-3">Queue ageing</h2>' +
    (m.queue_aging.length ? '<table class="data"><thead><tr><th>State</th><th>Team</th><th>Cases</th><th>Avg</th><th>Oldest</th></tr></thead><tbody>' + agingRows + '</tbody></table>' : CG.empty('Empty queue.')) + '</section>' +
    '<section class="card"><h2 class="font-headline-md text-headline-md mb-3">Cost split</h2>' + costHtml + '</section></div>' +
    '<section class="card"><h2 class="font-headline-md text-headline-md mb-1">Evaluation scorecard</h2><p class="text-body-sm text-slate-500">Regression gate on the mock; held-out rows are the real test.</p>' +
    (sc.main ? scoreTiles(sc.main, 'Main set') : CG.empty('No scorecard yet: run python -m caregrid.cli eval.')) + heldBlocks + '</section>';

  function applyFilters() {
    var f = { state: document.getElementById('f-state'), risk: document.getElementById('f-risk'), team: document.getElementById('f-team') };
    if (!f.state) return;
    CG.$$('#queue-body tr').forEach(function (tr) {
      var ok = (!f.state.value || tr.dataset.state === f.state.value) && (!f.risk.value || tr.dataset.risk === f.risk.value) && (!f.team.value || tr.dataset.team === f.team.value);
      tr.style.display = ok ? '' : 'none';
    });
  }
  ['f-state', 'f-risk', 'f-team'].forEach(function (id) { var e = document.getElementById(id); if (e) e.onchange = applyFilters; });
})();
