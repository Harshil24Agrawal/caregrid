/* Approval: handoff packet and decision form. Permissions and reasons come from case.actions (server-side can_approve); nothing is decided here. */
(async function () {
  var me = await CG.init('approval.html', 'Approval (Handoff)');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading the review queue…');
  var all;
  try { all = await CG_API.get('/api/cases'); } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load cases.'); return; }
  var queue = all.filter(function (c) { return ['in_review', 'needs_info', 'escalated'].indexOf(c.state) >= 0; });
  var current = CG.q('case') || (queue[0] && queue[0].id);
  var detail = null;
  var lastResult = null;

  el.innerHTML = '<div class="mb-4"><div class="eyebrow">Human-governed workflow</div><h1 class="font-headline-xl text-headline-xl">Approval and handoff</h1>' +
    '<p class="text-body-md text-slate-600">AI prepares the decision. You own it. The server decides who may approve.</p></div>' +
    '<div class="grid grid-cols-1 xl:grid-cols-4 gap-4"><aside class="card xl:col-span-1"><h2 class="font-headline-sm text-headline-sm mb-2">Review queue (' + queue.length + ')</h2><div id="queue"></div></aside>' +
    '<section class="xl:col-span-3" id="packet"></section></div>';

  function drawQueue() {
    var box = document.getElementById('queue');
    box.innerHTML = queue.length ? queue.map(function (c) {
      return '<a href="approval.html?case=' + encodeURIComponent(c.id) + '" data-id="' + CG.esc(c.id) + '" class="block p-2 rounded-xl border mb-2 ' + (c.id === current ? 'border-slate-900 bg-white' : 'border-slate-200 hover:bg-white') + '">' +
        '<div class="mono text-xs font-semibold">' + CG.esc(c.id) + '</div><div class="text-body-sm">' + CG.esc(c.request_type) + '</div><div class="mt-1">' + CG.stateChip(c.state) + CG.riskChip(c.risk) + '</div></a>';
    }).join('') : CG.empty('Nothing is waiting for a decision.');
    CG.$$('#queue a').forEach(function (a) { a.onclick = function (ev) { ev.preventDefault(); current = a.dataset.id; window.history.replaceState(null, '', 'approval.html?case=' + encodeURIComponent(current)); lastResult = null; drawQueue(); load(); }; });
  }
  drawQueue();
  if (current) load(); else document.getElementById('packet').innerHTML = '<div class="card">' + CG.empty('Pick a case from the queue, or open one from the Case page.') + '</div>';

  async function load() {
    var box = document.getElementById('packet');
    CG.loading(box, 'Loading ' + current + '…');
    try { detail = await CG_API.get('/api/cases/' + encodeURIComponent(current)); } catch (e) { box.innerHTML = '<div class="card">' + CG.restricted() + '</div>'; CG.fail(e); return; }
    draw();
  }

  function packet(c) {
    var r = c.reviewer, p = c.proposal;
    if (CG.isRestricted(r)) return '<div class="card mb-4"><h2 class="font-headline-md text-headline-md mb-2">Handoff packet</h2>' + CG.restricted(r) + '</div>';
    var checked = r.required_fields.filter(function (f) { return r.missing_fields.indexOf(f) < 0 && !(f in r.invalid_fields); });
    var missing = r.missing_fields.map(function (f) { return f + ': missing'; }).concat(Object.keys(r.invalid_fields).map(function (f) { return f + ': ' + r.invalid_fields[f]; }));
    var conf = r.confidence;
    return '<div class="card mb-4"><div class="flex flex-wrap items-center gap-2 mb-1"><span class="mono font-semibold">' + CG.esc(c.id) + '</span><span>·</span><span class="mono text-xs">' + CG.esc(c.request_type) + '</span>' +
      CG.stateChip(c.state) + CG.riskChip(c.risk) + CG.chip('Trust ' + CG.trustText(c.trust), 'chip-slate') + '<span class="text-body-sm text-slate-500">' + CG.esc(CG.age(c.age_hours)) + ' in queue</span></div>' +
      '<div class="mb-3">' + CG.reasonChips(c.reason_codes) + '</div><h2 class="font-headline-md text-headline-md mb-2">Handoff packet</h2>' +
      '<div class="grid grid-cols-1 md:grid-cols-2 gap-4"><div><div class="eyebrow mb-1">Summary</div><p class="whitespace-pre-wrap text-body-sm">' + CG.esc(r.summary_for_reviewer) + '</p>' +
      '<div class="eyebrow mt-3 mb-1">Checked</div>' + (checked.length ? checked.map(function (f) { return '<div class="text-body-sm">✓ ' + CG.esc(f) + '</div>'; }).join('') : CG.empty('Nothing checked yet.')) +
      '<div class="eyebrow mt-3 mb-1">Missing or invalid</div>' + (missing.length ? missing.map(function (f) { return '<div class="text-body-sm" style="color:#b91c1c">✗ ' + CG.esc(f) + '</div>'; }).join('') : '<div class="text-body-sm text-slate-500">none</div>') + '</div>' +
      '<div><div class="eyebrow mb-1">Proposed</div><div class="mb-2">' + (p ? CG.chip(p.decision_code, 'chip-dark') + ' → <b>' + CG.esc(p.route_team || c.assigned_team || '-') + '</b>' : '-') + '</div>' +
      '<div class="eyebrow mb-1">Confidence</div>' + (conf ? CG.confidenceBar(conf) : '-') +
      '<div class="eyebrow mt-3 mb-1">Conflicts</div>' + (r.conflicts.length ? r.conflicts.map(function (x) { return '<div class="p-2 rounded-lg mb-1" style="background:#fef2f2;color:#991b1b">' + CG.esc(x) + '</div>'; }).join('') : '<div class="text-body-sm text-slate-500">none</div>') +
      (r.notes.length ? '<div class="eyebrow mt-3 mb-1">Notes</div>' + r.notes.map(function (x) { return '<div class="text-body-sm text-slate-600">ℹ ' + CG.esc(x) + '</div>'; }).join('') : '') + '</div></div>' +
      '<div class="eyebrow mt-3 mb-1">Sources</div>' + (p && p.citations.length ? p.citations.map(CG.citationChip).join('') : CG.empty('No verified sources.')) + '</div>';
  }

  var ACTIONS = [['approve', 'Approve'], ['edit_approve', 'Edit & approve'], ['reject', 'Reject'], ['escalate', 'Escalate'], ['ask_requester', 'Ask requester']];

  function form(c) {
    var a = c.actions;
    if (!a.decidable) return '<div class="card">' + CG.empty('This case is ' + c.state + ': nothing to decide.') + '</div>';
    var policies = c.proposal ? c.proposal.citations.filter(function (x) { return x.page_type === 'policy'; }) : [];
    var gate = a.approve.allowed ? '' : '<div class="restricted-box mb-3"><span class="material-symbols-outlined">lock</span><span>Approve, edit, reject and escalate are disabled: ' + CG.esc(a.approve.reason) + '</span></div>';
    var askGate = a.ask.allowed ? '' : '<div class="restricted-box mb-3"><span class="material-symbols-outlined">lock</span><span>Asking the requester is disabled: ' + CG.esc(a.ask.reason) + '</span></div>';
    return '<div class="card"><h2 class="font-headline-md text-headline-md mb-3">Decision</h2>' + gate + (a.approve.allowed ? '' : askGate) +
      '<div class="flex flex-wrap gap-2 mb-3" id="actions">' + ACTIONS.map(function (x, i) {
        var allowed = x[0] === 'ask_requester' ? a.ask.allowed : a.approve.allowed;
        return '<label class="btn ' + (allowed ? '' : 'opacity-50') + '" style="cursor:' + (allowed ? 'pointer' : 'not-allowed') + '"><input type="radio" name="action" value="' + x[0] + '" ' + (allowed ? '' : 'disabled') + (i === 0 && a.approve.allowed ? ' checked' : '') + '/> ' + x[1] + '</label>';
      }).join('') + '</div>' +
      '<div id="edit-wrap" class="mb-3" style="display:none"><label class="eyebrow">Edited answer</label><textarea id="edited" rows="5" class="w-full border border-slate-200 rounded-xl text-sm">' + CG.esc(c.proposal ? c.proposal.answer_text : '') + '</textarea></div>' +
      '<div class="mb-3"><label class="eyebrow" id="note-label">Note</label><textarea id="note" rows="3" maxlength="2000" class="w-full border border-slate-200 rounded-xl text-sm" placeholder="Reason for the decision (masked before it is stored)"></textarea></div>' +
      '<div class="flex flex-wrap gap-4 mb-3"><label class="text-body-sm"><input type="checkbox" id="prec" checked/> Save as precedent</label>' +
      '<label class="text-body-sm"><input type="checkbox" id="pr" ' + (policies.length ? '' : 'disabled') + '/> Propose a Knowledge PR from this decision' + (policies.length ? '' : ' (no policy cited)') + '</label></div>' +
      '<div id="pr-wrap" class="mb-3 p-3 rounded-xl border border-slate-200" style="display:none"><div class="flex flex-wrap gap-4 items-center"><label class="text-body-sm">Policy to change <select id="pr-target" class="border border-slate-200 rounded-lg text-sm">' +
      policies.map(function (x) { return '<option>' + CG.esc(x.page_id) + '</option>'; }).join('') + '</select></label><label class="text-body-sm"><input type="checkbox" id="pr-retire"/> Retire this policy (no replacement)</label></div>' +
      '<p class="text-body-sm text-slate-500 mt-1">The note above becomes the PR note. Retire is a structured change that only a knowledge owner can approve.</p></div>' +
      '<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3"><label class="text-body-sm">Official contact email<input id="email" type="text" class="w-full border border-slate-200 rounded-lg text-sm" placeholder="desk@clinic.example"/></label>' +
      '<label class="text-body-sm">Official contact phone<input id="phone" type="text" class="w-full border border-slate-200 rounded-lg text-sm" placeholder="+91 ..."/></label></div>' +
      '<div class="flex flex-wrap gap-4 mb-4"><span class="eyebrow">Notify via</span>' + ['email', 'whatsapp', 'sms', 'portal'].map(function (ch) {
        return '<label class="text-body-sm"><input type="checkbox" class="chan" value="' + ch + '"/> ' + ch + (ch === 'whatsapp' || ch === 'sms' ? ' (simulated)' : '') + '</label>';
      }).join('') + '</div><div class="flex items-center gap-3"><button class="btn btn-primary" id="go" type="button">Submit decision</button><span id="go-state" class="text-body-sm text-slate-500"></span></div></div>';
  }

  function resultCard() {
    if (!lastResult) return '';
    var r = lastResult.result;
    return '<div class="card mb-4" style="border-color:#a7f3d0;background:#f0fdf4"><h2 class="font-headline-md text-headline-md mb-2">Decision recorded</h2>' +
      '<div class="mb-2"><span class="eyebrow">State path</span><div>' + r.state_path.map(function (s) { return CG.stateChip(s); }).join(' → ') + '</div></div>' +
      '<div class="mb-2"><span class="eyebrow">Precedent</span><div class="mono">' + CG.esc(r.precedent_id || 'none saved') + '</div></div>' +
      '<div class="mb-2"><span class="eyebrow">Trust</span><div class="text-body-sm">' + (r.trust ? CG.esc(r.trust.request_type + ': level ' + r.trust.level_before + ' → ' + r.trust.level_after + ', streak ' + r.trust.streak_before + ' → ' + r.trust.streak_after + ' (' + (r.trust.agreed ? 'agreed' : 'override') + ')') : 'unchanged') + '</div></div>' +
      (r.pr_id ? '<div class="mb-2"><span class="eyebrow">Knowledge PR opened</span><div><a class="mono text-secondary hover:underline" href="knowledge.html?tab=prs">' + CG.esc(r.pr_id) + '</a></div></div>' : '') +
      (r.communications.length ? '<div><span class="eyebrow">Communications</span><div>' + r.communications.map(function (m) { return CG.chip(m.channel + ' · ' + m.status, 'chip-purple'); }).join('') + ' <a class="text-secondary hover:underline text-body-sm" href="comms.html">see all</a></div></div>' : '') + '</div>';
  }

  function draw() {
    var c = detail, box = document.getElementById('packet');
    box.innerHTML = resultCard() + packet(c) + form(c);
    if (!c.actions.decidable) return;
    function sync() {
      var act = (document.querySelector('input[name=action]:checked') || {}).value;
      document.getElementById('edit-wrap').style.display = act === 'edit_approve' ? '' : 'none';
      document.getElementById('note-label').textContent = act === 'ask_requester' ? 'Question to the requester (used when the case has none of its own)' : 'Note';
      var ask = act === 'ask_requester';
      document.getElementById('prec').disabled = ask; document.getElementById('pr').disabled = ask || !c.proposal.citations.some(function (x) { return x.page_type === 'policy'; });
      document.getElementById('pr-wrap').style.display = document.getElementById('pr').checked && !document.getElementById('pr').disabled ? '' : 'none';
      var allowed = ask ? c.actions.ask.allowed : c.actions.approve.allowed;
      document.getElementById('go').disabled = !allowed || !act;
      document.getElementById('go-state').textContent = allowed ? '' : (ask ? c.actions.ask.reason : c.actions.approve.reason);
    }
    CG.$$('input[name=action], #pr').forEach(function (i) { i.onchange = sync; });
    sync();
    document.getElementById('go').onclick = async function () {
      var act = document.querySelector('input[name=action]:checked').value;
      var body = { action: act, note: document.getElementById('note').value, save_as_precedent: document.getElementById('prec').checked && act !== 'ask_requester',
        propose_pr: document.getElementById('pr').checked && !document.getElementById('pr').disabled, contact_email: document.getElementById('email').value || null,
        contact_phone: document.getElementById('phone').value || null, channels: CG.$$('.chan').filter(function (x) { return x.checked; }).map(function (x) { return x.value; }), meta_changes: {} };
      if (act === 'edit_approve') body.edited_answer = document.getElementById('edited').value;
      if (body.propose_pr) {
        body.meta_changes.target_page = document.getElementById('pr-target').value;
        if (document.getElementById('pr-retire').checked) body.meta_changes.retire = true;
      }
      var go = document.getElementById('go');
      go.disabled = true;
      try {
        lastResult = await CG_API.post('/api/cases/' + encodeURIComponent(c.id) + '/decision', body);
        detail = lastResult.case;
        CG.toast('Decision recorded for ' + c.id + '.', 'ok');
        queue = (await CG_API.get('/api/cases')).filter(function (x) { return ['in_review', 'needs_info', 'escalated'].indexOf(x.state) >= 0; });
        drawQueue();
        draw();
      } catch (e) { CG.fail(e); go.disabled = false; }
    };
  }
})();
