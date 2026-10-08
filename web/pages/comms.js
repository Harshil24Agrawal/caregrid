/* Communications: /api/comms. Messages and recipients are what the backend stored, shown through the output guard for the acting user. */
(async function () {
  var me = await CG.init('comms.html', 'Communications');
  var el = document.getElementById('content');
  CG.loading(el, 'Loading communications…');
  var rows;
  try { rows = await CG_API.get('/api/comms'); } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load communications.'); return; }
  var sel = null;

  el.innerHTML = '<div class="mb-4"><div class="eyebrow">Outbound</div><h1 class="font-headline-xl text-headline-xl">Communications</h1>' +
    '<p class="text-body-md text-slate-600">Messages sent when a reviewer approves a case. E-mail is simulated in this build; WhatsApp and SMS are always simulated. Nothing leaves the system.</p></div>' +
    '<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4" id="tiles"></div>' +
    '<div class="grid grid-cols-1 xl:grid-cols-3 gap-4"><section class="card xl:col-span-2"><div class="flex gap-3 mb-3 items-end"><label class="text-body-sm">Channel <select id="f-ch" class="block border border-slate-200 rounded-lg text-sm"><option value="">All</option><option>email</option><option>whatsapp</option><option>sms</option><option>portal</option></select></label></div><div id="list"></div></section>' +
    '<section class="card"><h2 class="font-headline-sm text-headline-sm mb-2">Message</h2><div id="inspect">' + CG.empty('Select a message.') + '</div></section></div>';

  function statusChip(r) { return r.simulated ? CG.chip('simulated', 'chip-amber') : CG.chip(r.status, r.status === 'failed' ? 'chip-red' : 'chip-green'); }
  function draw() {
    var ch = document.getElementById('f-ch').value;
    var shown = rows.filter(function (r) { return !ch || r.channel === ch; });
    var count = function (c) { return rows.filter(function (r) { return r.channel === c; }).length; };
    document.getElementById('tiles').innerHTML = [['Total', rows.length], ['Email', count('email')], ['WhatsApp', count('whatsapp')], ['SMS / portal', count('sms') + count('portal')]].map(function (t) {
      return '<div class="tile"><div class="eyebrow">' + t[0] + '</div><div class="num">' + t[1] + '</div></div>';
    }).join('');
    document.getElementById('list').innerHTML = shown.length ? '<div class="overflow-x-auto"><table class="data"><thead><tr><th>Case</th><th>Channel</th><th>Recipient</th><th>Status</th><th>Time</th></tr></thead><tbody>' +
      shown.map(function (r, i) {
        return '<tr class="cursor-pointer" data-i="' + i + '"><td>' + CG.caseLink(r.case_id) + '</td><td>' + CG.esc(r.channel) + '</td><td class="mono text-xs">' + CG.esc(r.recipient) + '</td><td>' + statusChip(r) +
          '</td><td class="mono text-xs">' + CG.esc(CG.time(r.ts)) + '</td></tr>';
      }).join('') + '</tbody></table></div>' : CG.empty('No communications yet. Approve a case with a channel selected to create one.');
    CG.$$('#list tr[data-i]').forEach(function (tr) {
      tr.onclick = function () {
        var r = shown[+tr.dataset.i];
        document.getElementById('inspect').innerHTML = '<div class="mb-2">' + CG.esc(r.channel) + ' ' + statusChip(r) + '</div><div class="eyebrow">Recipient</div><div class="mono text-sm mb-2">' + CG.esc(r.recipient) +
          '</div><div class="eyebrow">Message</div><div class="p-3 rounded-xl bg-slate-50 whitespace-pre-wrap text-sm">' + CG.esc(r.message) + '</div>' +
          (r.simulated ? '<p class="text-body-sm text-slate-500 mt-2">Simulated: recorded in the database, not delivered.</p>' : '');
      };
    });
  }
  document.getElementById('f-ch').onchange = draw;
  draw();
})();
