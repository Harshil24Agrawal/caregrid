/* Audit log: /api/audit with filters. The server returns only events for cases the acting user may see. */
(async function () {
  var me = await CG.init('audit.html', 'Audit Log');
  var el = document.getElementById('content');
  var HIGHLIGHT = { guard_blocked: 'chip-red', auto_with_audit: 'chip-teal', pii_remasked: 'chip-amber', review_submitted: 'chip-blue', precedent_saved: 'chip-purple',
    communication_sent: 'chip-purple', pr_opened: 'chip-blue', pr_decided: 'chip-green', llm_failure: 'chip-amber', review_denied: 'chip-red', pr_denied: 'chip-red' };

  el.innerHTML = '<div class="mb-4"><div class="eyebrow">Governance</div><h1 class="font-headline-xl text-headline-xl">Audit log</h1>' +
    '<p class="text-body-md text-slate-600">Every important step is written to the audit log. Details hold ids, codes and counts, never raw text. ' +
    (me.role === 'auditor' ? 'As the auditor you see every case (read-only).' : 'You see events for the cases visible to your role.') + '</p></div>' +
    '<div class="card mb-4"><div class="flex flex-wrap gap-3 items-end">' +
    '<label class="text-body-sm">Case<input id="f-case" type="text" placeholder="REQ-0001" class="block border border-slate-200 rounded-lg text-sm"/></label>' +
    '<label class="text-body-sm">Event<select id="f-event" class="block border border-slate-200 rounded-lg text-sm"><option value="">All</option></select></label>' +
    '<label class="text-body-sm">Actor<select id="f-actor" class="block border border-slate-200 rounded-lg text-sm"><option value="">All</option>' +
    CG.users.map(function (u) { return '<option value="' + CG.esc(u.id) + '">' + CG.esc(u.name + ' (' + u.id + ')') + '</option>'; }).join('') + '</select></label>' +
    '<button id="apply" class="btn btn-primary" type="button">Apply</button><button id="reset-f" class="btn" type="button">Reset filters</button></div></div>' +
    '<div id="tiles" class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4"></div><div id="table" class="card"></div>';

  var eventsSeen = false;
  async function load() {
    var params = new URLSearchParams();
    ['case', 'event', 'actor'].forEach(function (k) { var v = document.getElementById('f-' + k).value.trim(); if (v) params.set(k, v); });
    var box = document.getElementById('table');
    CG.loading(box, 'Loading events…');
    try {
      var r = await CG_API.get('/api/audit' + (params.toString() ? '?' + params : ''));
      if (!eventsSeen) {
        document.getElementById('f-event').innerHTML = '<option value="">All</option>' + r.event_types.map(function (t) { return '<option>' + CG.esc(t) + '</option>'; }).join('');
        eventsSeen = true;
      }
      var count = function (n) { return r.events.filter(function (e) { return e.event === n; }).length; };
      document.getElementById('tiles').innerHTML = [['Events shown', r.events.length], ['guard_blocked', count('guard_blocked')], ['review_submitted', count('review_submitted')],
        ['precedent_saved', count('precedent_saved')], ['communication_sent', count('communication_sent')]].map(function (t) {
        return '<div class="tile"><div class="eyebrow">' + CG.esc(t[0]) + '</div><div class="num">' + t[1] + '</div></div>';
      }).join('');
      box.innerHTML = r.events.length ? '<div class="overflow-x-auto"><table class="data"><thead><tr><th>Time</th><th>Case</th><th>Event</th><th>Actor</th><th>Role</th><th></th></tr></thead><tbody>' +
        r.events.map(function (e, i) {
          return '<tr><td class="mono text-xs">' + CG.esc(CG.time(e.ts)) + '</td><td>' + (e.case_id ? CG.caseLink(e.case_id) : '<span class="text-slate-400">system</span>') + '</td><td>' +
            CG.chip(e.event, HIGHLIGHT[e.event] || 'chip-slate') + '</td><td class="mono text-xs">' + CG.esc(e.actor_id) + '</td><td class="text-xs">' + CG.esc(e.actor_role) +
            '</td><td><button class="btn btn-sm" type="button" data-i="' + i + '">Details</button></td></tr><tr id="d' + i + '" style="display:none"><td colspan="6"><pre class="mono text-xs whitespace-pre-wrap break-all bg-slate-50 p-3 rounded-lg">' +
            CG.esc(JSON.stringify(e.details, null, 2)) + '</pre></td></tr>';
        }).join('') + '</tbody></table></div>' : CG.empty('No events match.');
      CG.$$('button[data-i]', box).forEach(function (b) { b.onclick = function () { var row = document.getElementById('d' + b.dataset.i); row.style.display = row.style.display === 'none' ? '' : 'none'; }; });
    } catch (e) { CG.fail(e); box.innerHTML = CG.empty('Could not load the audit log.'); }
  }
  document.getElementById('apply').onclick = load;
  document.getElementById('reset-f').onclick = function () { ['case', 'event', 'actor'].forEach(function (k) { document.getElementById('f-' + k).value = ''; }); load(); };
  load();
})();
