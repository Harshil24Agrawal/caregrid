/* CareGrid web UI shell: header (provider, PHI masked, demo login, reset), 5-link nav, toasts, and presentation helpers.
   Presentation only: every number and decision on the pages comes from the API. */
(function () {
  var NAV = [['index.html', 'Dashboard'], ['intake.html', 'New request'], ['case.html', 'Cases'], ['knowledge.html', 'Knowledge'], ['audit.html', 'Audit']];

  // plain-language labels; the code stays in the tooltip
  var REASON = {
    MISSING_DATA: ['Missing information', 'amber'], POLICY_GAP: ['No policy covers this', 'amber'], POLICY_CONFLICT: ['Policies disagree', 'red'],
    CLINICAL: ['Medical question', 'red'], ACCOUNT_SPECIFIC: ['Account-specific request', 'amber'], SENSITIVE: ['Sensitive topic', 'red'],
    UNCLEAR_INTENT: ['Unclear request', 'amber'], IRREVERSIBLE_ACTION: ['Cannot be undone', 'red'], HIGH_RISK: ['High risk', 'red'],
    ACCESS_DENIED: ['Access denied', 'red'], LOW_CONFIDENCE: ['Low confidence', 'amber']
  };
  var STATE = { answered: ['Answered', 'green'], needs_info: ['Needs info', 'amber'], in_review: ['In review', 'amber'], escalated: ['Escalated', 'red'],
    approved: ['Approved', 'green'], actioned: ['Actioned', 'green'], notified: ['Notified', 'green'], rejected: ['Rejected', 'red'], closed: ['Closed', 'grey'],
    new: ['New', 'grey'], classified: ['Classified', 'grey'], ready: ['Ready', 'grey'], proposed: ['Proposed', 'grey'] };
  var RISK = { low: ['Low', 'grey'], medium: ['Medium', 'amber'], high: ['High', 'red'], critical: ['Critical', 'red'] };
  var EVENT = {
    request_received: 'Request received', classified: 'Request classified', context_assembled: 'Sources gathered', policy_identified: 'Policy found',
    precedent_identified: 'Past case found', rules_applied: 'Rules checked', proposal_generated: 'Answer drafted', citations_verified: 'Sources verified',
    confidence_scored: 'Confidence scored', routed: 'Routed', state_changed: 'Status changed', guard_blocked: 'Request blocked', auto_with_audit: 'Answered automatically',
    review_submitted: 'Decision made', review_denied: 'Decision refused', review_rejected: 'Decision not accepted', action_executed: 'Action carried out',
    precedent_saved: 'Saved as precedent', trust_updated: 'Trust ladder updated', communication_sent: 'Message sent', comms_note: 'Message note', pr_requested: 'Change proposed',
    pr_opened: 'Change request opened', pr_decided: 'Change request decided', pr_denied: 'Change request refused', pr_skipped: 'No change request needed',
    pr_rejected_request: 'Change request not accepted', llm_failure: 'Model unavailable', output_checked: 'Output cleaned', requester_asked: 'Asked the requester',
    pii_remasked: 'Personal data re-masked'
  };
  var STEPS = [['Guard', 'request_received'], ['Classify', 'classified'], ['Gather sources', 'context_assembled'], ['Check rules', 'rules_applied'], ['Draft', 'proposal_generated'],
    ['Verify sources', 'citations_verified'], ['Score', 'confidence_scored'], ['Route', 'routed']];

  var CG = window.CG = { users: [], config: null, me: null, teams: {}, REASON: REASON, STATE: STATE, STEPS: STEPS };

  // ---------------------------------------------------------------- helpers
  CG.esc = function (s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  };
  CG.human = function (s) { s = String(s || '').replace(/_/g, ' '); return s.charAt(0).toUpperCase() + s.slice(1); };
  CG.tag = function (text, kind, title) { return '<span class="tag ' + (kind || 'grey') + '"' + (title ? ' title="' + CG.esc(title) + '"' : '') + '>' + CG.esc(text) + '</span>'; };
  CG.stateTag = function (s) { var x = STATE[s] || [CG.human(s), 'grey']; return CG.tag(x[0], x[1], s); };
  CG.riskTag = function (r, short) { if (!r) return ''; var x = RISK[r] || [CG.human(r), 'grey']; return CG.tag(short ? x[0] : x[0] + ' risk', x[1], 'risk: ' + r); };
  CG.reasonLabel = function (code) { return (REASON[code] || [CG.human(code), 'grey'])[0]; };
  CG.reasonChips = function (codes) {
    return (codes || []).map(function (c) { var x = REASON[c] || [CG.human(c), 'grey']; return '<span class="chip ' + x[1] + '" title="' + CG.esc(c) + '">' + CG.esc(x[0]) + '</span>'; }).join('');
  };
  CG.canReset = function () { var c = CG.config; return !!(c && c.demo_mode && c.reset_roles.indexOf(CG.me.role) >= 0); };
  CG.isKnowledgeAdmin = function () { var c = CG.config; return !!(c && c.knowledge_admin_roles.indexOf(CG.me.role) >= 0); };
  CG.eventName = function (e) { return EVENT[e] || CG.human(e); };
  CG.team = function (id) { return CG.teams[id] || id || ''; };
  var TEAM_SHORT = { 'Compliance & Privacy': 'Compliance', 'Provider Enrollment': 'Enrollment', 'Senior Operations Review': 'Senior Ops', 'Operations Triage': 'Triage',
    'Clinical Review': 'Clinical', 'IT Service Desk': 'IT Desk', 'Utilization Management': 'Utilization', 'Claims Operations': 'Claims' };
  CG.teamShort = function (id) { var n = CG.team(id); return TEAM_SHORT[n] || n; };
  CG.what = function (type) {
    if (type === 'unknown') return 'Unclassified request';
    return CG.human(type).replace(/\bdme\b/i, 'DME').replace(/\bnpi\b/i, 'NPI');
  };
  CG.piiLabel = function (t) { t = String(t || ''); return t.length <= 4 ? t.toUpperCase() : CG.human(t.toLowerCase()); };
  CG.role = function (r) { return CG.human(r); };
  CG.age = function (h) {                                   // "5 min", "5 h 30 min", "1 d 6 h"
    if (h === null || h === undefined) return '';
    if (h < 1) return Math.max(1, Math.round(h * 60)) + ' min';
    if (h < 24) { var m = Math.round((h - Math.floor(h)) * 60); return Math.floor(h) + ' h' + (m ? ' ' + m + ' min' : ''); }
    var d = Math.floor(h / 24), r = Math.round(h - d * 24);
    return d + ' d' + (r ? ' ' + r + ' h' : '');
  };
  CG.userName = function (id) { var u = CG.users.filter(function (x) { return x.id === id; })[0]; return u ? u.name : id; };
  CG.llmName = function () {                                // friendly provider name from the config; model ids go in the tooltip
    var c = CG.config || {}, p = c.llm_provider, m = (c.models && c.models.light) || '';
    if (p === 'mock') return 'Mock (offline)';
    if (p === 'anthropic') return 'Claude';
    if (p === 'bedrock') return 'Bedrock';
    if (/gemini/i.test(m)) return 'Gemini';
    if (/claude/i.test(m)) return 'Claude';
    if (/gpt|^o\d/i.test(m)) return 'OpenAI';
    if (/llama/i.test(m)) return 'Llama';
    return p === 'openai_compat' ? 'OpenAI-compatible' : String(p);
  };
  var SHORT = { general_policy_question: 'Policy question', provider_address_change: 'Address change', provider_name_change: 'Name change', portal_access_reset: 'Portal reset',
    prior_auth_status: 'Prior auth', dme_equipment_request: 'DME request', claim_status_inquiry: 'Claim status', complaint_grievance: 'Complaint', unknown: 'Unclassified' };
  CG.shortWhat = function (type) { return SHORT[type] || CG.what(type); };
  // map internal notes to plain sentences; anything unknown is shown as written
  CG.plainNote = function (n) {
    var s = String(n || '');
    var m = s.match(/^(P-\d+) is stale \((KA-\d+) v(\d+)\)/);
    if (m) return 'Past case ' + m[1] + ' is out of date (it used ' + m[2] + ' v' + m[3] + ') and was not relied on.';
    var rules = [[/write-tier action/i, 'This action changes records, so a person must approve it.'], [/^llm_fallback/, 'The AI model was unavailable, so standard wording was used.'],
      [/^llm_failure/, 'The AI model had a problem, so standard wording was used.'], [/^llm_tier_downgrade/, 'A lighter AI model answered because the stronger one was unavailable.'],
      [/^llm_output_rejected|^llm_reject_reason/, 'The AI wording failed a safety check, so standard wording was used.'], [/prompt-injection/i, 'The request tried to override the assistant’s rules.'],
      [/input too long/i, 'The request was too long and was cut off.'], [/specific record.s personal details/i, 'The request asks for personal details of a specific record.'],
      [/may not view other people.s records/i, 'This role may not look at other people’s records.'], [/clinical question/i, 'Medical question: no advice is given; it goes to Clinical Review.'],
      [/sensitive content/i, 'Sensitive topic: a person handles it.']];
    for (var i = 0; i < rules.length; i++) if (rules[i][0].test(s)) return rules[i][1];
    return s;
  };
  CG.time = function (iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };
  CG.caseLink = function (id, label) { return '<a class="mono" href="case.html?case=' + encodeURIComponent(id) + '">' + CG.esc(label || id) + '</a>'; };
  CG.pageLink = function (id, version) { return 'knowledge.html?page=' + encodeURIComponent(id) + (version ? '&v=' + encodeURIComponent(version) : ''); };
  CG.citationChip = function (c) {
    return '<a class="chip" href="' + CG.pageLink(c.page_id, c.version) + '" title="' + CG.esc(c.title) + '">' + CG.esc(c.page_id + (c.version ? ' v' + c.version : '') + ' · ' + c.page_type) + '</a>';
  };
  CG.lock = function (r) {
    var msg = (r && r.message) || "ACCESS RESTRICTED: you don't have permission to view this information.";
    return '<div class="lock"><span aria-hidden="true">🔒</span><span>' + CG.esc(msg) + '</span></div>';
  };
  CG.isRestricted = function (v) { return !!(v && v.restricted); };
  CG.empty = function (text) { return '<p class="empty">' + CG.esc(text) + '</p>'; };
  CG.q = function (name) { return new URLSearchParams(window.location.search).get(name); };
  CG.$ = function (sel, root) { return (root || document).querySelector(sel); };
  CG.$$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  CG.loading = function (el, text) { el.innerHTML = '<p class="muted"><span class="spinner"></span> ' + CG.esc(text || 'Loading…') + '</p>'; };
  CG.band = function (conf) {
    if (!conf) return '';
    var kind = conf.band === 'high' ? 'green' : conf.band === 'medium' ? 'amber' : 'red';
    return '<span class="chip ' + kind + '" title="Confidence band: ' + CG.esc(conf.band) + '"><b>' + conf.score + '</b> · ' + CG.esc(CG.human(conf.band)) + '</span>';
  };
  // 5-part confidence bar
  CG.confidenceBar = function (conf) {                      // shown once: "95 · High", the 5-part bar and a legend
    if (!conf || !conf.breakdown) return '';
    var parts = [['policy', 'Policy', 30], ['precedent', 'Past cases', 25], ['fields', 'Details', 20], ['clarity', 'Clarity', 15], ['no_conflict', 'No conflict', 10]];
    var segs = parts.map(function (p, i) {
      var v = conf.breakdown[p[0]] || 0;
      return '<span class="s' + i + '" style="width:' + Math.max(v, 0) + '%" title="' + p[1] + ' ' + v + ' of ' + p[2] + '"></span>';
    }).join('');
    var legend = parts.map(function (p, i) { return '<span class="lg"><i class="sw s' + i + '"></i>' + p[1] + '</span>'; }).join('');
    return '<div>' + CG.band(conf) + '</div><div class="seg-bar" role="img" aria-label="Confidence parts: ' + CG.esc(parts.map(function (p) { return p[1] + ' ' + (conf.breakdown[p[0]] || 0) + ' of ' + p[2]; }).join(', ')) + '">' + segs + '</div>' +
      '<div class="legend small muted">' + legend + '</div>' + (conf.capped_at_medium ? '<div class="callout amber">Capped at Medium: the policies disagree.</div>' : '');
  };
  // pipeline chips: `lit` is a list of event names that happened; `running` is an index being animated; `counts` optional
  CG.pipeline = function (lit, running, counts, tiers) {   // `tiers`: llm_tiers_used, shown on the Draft step
    var tier = tiers && tiers.length ? (tiers.indexOf('strong') >= 0 ? 'strong' : 'light') : '';
    return '<div class="pipeline" aria-label="Pipeline steps">' + STEPS.map(function (s, i) {
      var on = lit && lit.indexOf(s[1]) >= 0;
      var cls = on ? 'lit' : (running === i ? 'run' : '');
      var n = counts && counts[s[1]] !== undefined ? ' <b class="mono">' + counts[s[1]] + '</b>' : '';
      var extra = s[1] === 'proposal_generated' && on && tier ? ' · ' + tier : '';
      return (i ? '<span class="arrow" aria-hidden="true">›</span>' : '') + '<span class="step ' + cls + '" title="' + CG.esc(s[1]) + '">' + (on ? '✓ ' : '') + CG.esc(s[0] + extra) + n + '</span>';
    }).join('') + '</div>';
  };
  CG.diffHtml = function (diff) {
    if (!diff) return '<p class="empty">No text change (structured change only).</p>';
    return '<pre class="diff">' + diff.split('\n').map(function (l) {
      var cls = l.indexOf('+++') === 0 || l.indexOf('---') === 0 || l[0] === '@' ? 'diff-hunk' : l[0] === '+' ? 'diff-add' : l[0] === '-' ? 'diff-del' : '';
      return '<div class="' + cls + '">' + CG.esc(l || ' ') + '</div>';
    }).join('') + '</pre>';
  };

  // ---------------------------------------------------------------- toasts, modal
  CG.toast = function (msg, kind) {
    var root = document.getElementById('toasts');
    if (!root) return;
    var el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, kind === 'error' ? 7000 : 4500);
  };
  CG.fail = function (err) { CG.toast((err && err.status === 403 ? 'Not allowed: ' : '') + (err && err.message ? err.message : 'Something went wrong.'), 'error'); };
  CG.confirm = function (title, text, okLabel) {
    return new Promise(function (resolve) {
      var root = document.getElementById('modal-root');
      root.innerHTML = '<div class="backdrop"><div class="dialog" role="dialog" aria-modal="true" aria-label="' + CG.esc(title) + '"><h3>' + CG.esc(title) + '</h3><p class="muted" style="margin:8px 0 16px">' +
        CG.esc(text) + '</p><div class="flex" style="justify-content:flex-end"><button class="btn" id="m-cancel" type="button">Cancel</button><button class="btn primary" id="m-ok" type="button">' +
        CG.esc(okLabel || 'Confirm') + '</button></div></div></div>';
      function done(v) { root.innerHTML = ''; resolve(v); }
      document.getElementById('m-cancel').onclick = function () { done(false); };
      document.getElementById('m-ok').onclick = function () { done(true); };
      document.getElementById('m-ok').focus();
    });
  };

  // ---------------------------------------------------------------- header
  function render(active) {
    var me = CG.me, cfg = CG.config, llm = cfg ? cfg.llm_provider : '?';
    var top = document.createElement('header');
    top.className = 'topbar';
    top.innerHTML = '<div class="topbar-in"><a class="brand" href="index.html">CareGrid</a><span class="tagline">Healthcare Operations Second Brain</span><div class="pills">' +
      '<span class="pill" title="' + CG.esc(cfg ? 'light: ' + cfg.models.light + ' / strong: ' + cfg.models.strong : '') + '"><span class="dot ' + (llm === 'mock' ? 'green' : 'blue') + '"></span>LLM: ' + CG.esc(CG.llmName()) + '</span>' +
      '<span class="pill" id="phi-pill" title="Masked tokens (names, ids, phones, e-mails...) in the requests you can see. Values were never stored."><span class="dot green"></span>PHI masked: <b id="phi-n">…</b></span>' +
      '<button class="pill" id="user-btn" type="button" aria-haspopup="true" title="Demo login: the acting user is sent as a header (not real authentication)"><span class="dot grey"></span>Demo login: ' +
      CG.esc(me.name + ' · ' + CG.role(me.role)) + ' ▾</button>' +
      (CG.canReset() ? '<button class="pill" id="reset-demo" type="button">Reset demo</button>' : '') + '</div></div>';
    var nav = document.createElement('nav');
    nav.className = 'navstrip';
    nav.setAttribute('aria-label', 'Main');
    nav.innerHTML = '<div class="navstrip-in">' + NAV.map(function (n) { return '<a href="' + n[0] + '"' + (n[0] === active ? ' class="on" aria-current="page"' : '') + '>' + n[1] + '</a>'; }).join('') + '</div>';
    document.body.insertBefore(nav, document.body.firstChild);
    document.body.insertBefore(top, document.body.firstChild);

    document.getElementById('user-btn').onclick = function (ev) {
      ev.stopPropagation();
      var old = document.getElementById('user-menu');
      if (old) { old.remove(); return; }
      var menu = document.createElement('div');
      menu.id = 'user-menu'; menu.className = 'menu';
      menu.innerHTML = CG.users.map(function (u) {
        return '<button type="button" data-uid="' + CG.esc(u.id) + '" class="' + (u.id === me.id ? 'on' : '') + '"><span class="dot grey"></span><span><b>' + CG.esc(u.name) + '</b><br><span class="small muted">' +
          CG.esc(CG.role(u.role) + (u.team ? ' · ' + CG.team(u.team) : '')) + '</span></span></button>';
      }).join('');
      document.querySelector('.pills').appendChild(menu);
      CG.$$('button', menu).forEach(function (b) { b.onclick = function () { CG_API.setUser(b.getAttribute('data-uid')); window.location.reload(); }; });
    };
    document.addEventListener('click', function () { var m = document.getElementById('user-menu'); if (m) m.remove(); });
    var resetBtn = document.getElementById('reset-demo');
    if (resetBtn) resetBtn.onclick = async function () {
      var ok = await CG.confirm('Reset the demo?', 'This wipes the database and learned precedents, recompiles the Second Brain and re-seeds the demo cases. It cannot be undone.', 'Reset demo');
      if (!ok) return;
      try { CG.toast('Resetting…'); await CG_API.post('/api/reset'); CG.toast('Demo reset.', 'ok'); setTimeout(function () { window.location.href = 'index.html'; }, 500); } catch (e) { CG.fail(e); }
    };
    // PHI masked count: the personal-data types recorded when each visible request arrived
    CG_API.get('/api/metrics').then(function (r) {
      document.getElementById('phi-n').textContent = r.phi_masked;
    }).catch(function () { var p = document.getElementById('phi-pill'); if (p) p.style.display = 'none'; });
  }

  /* CG.init('case.html') resolves once users, config, team names and the acting user are loaded and the header is drawn. */
  CG.init = async function (active) {
    var root = document.getElementById('content');
    ['toasts', 'modal-root'].forEach(function (id) { if (!document.getElementById(id)) { var d = document.createElement('div'); d.id = id; document.body.appendChild(d); } });
    try {
      var r = await Promise.all([CG_API.get('/api/users'), CG_API.get('/api/config')]);
      CG.users = r[0]; CG.config = r[1];
    } catch (e) {
      root.innerHTML = '<div class="card"><h2>The CareGrid API is not reachable</h2><p class="muted" style="margin-top:6px">Start it with <code>python -m caregrid.cli serve</code> and open http://127.0.0.1:8000/</p></div>';
      throw e;
    }
    var uid = CG_API.getUser();
    CG.me = CG.users.filter(function (u) { return u.id === uid; })[0] || CG.users[0];
    CG_API.setUser(CG.me.id);
    try { (await CG_API.get('/api/pages?type=team')).forEach(function (p) { CG.teams[p.id] = p.title; }); } catch (e) { /* team ids are shown instead */ }
    render(active);
    return CG.me;
  };
})();
