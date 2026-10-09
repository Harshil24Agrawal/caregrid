/* Demo guide (demo mode only): one card per scenario, in demo order. Each button switches the demo login and either pre-fills New request with the
   exact sample text (not submitted) or opens the case / page. The texts come from the server (/api/demo/guide), the same ones the tests use. */
(async function () {
  var me = await CG.init('demo.html');
  var el = document.getElementById('content');
  if (!CG.config || !CG.config.demo_mode) { el.innerHTML = '<div class="page-head"><h1>Demo guide</h1></div><div class="card">' + CG.empty('The demo guide is only available in demo mode.') + '</div>'; return; }
  var g;
  try { g = await CG_API.get('/api/demo/guide'); } catch (e) { CG.fail(e); el.innerHTML = CG.empty('Could not load the demo guide.'); return; }
  var NAME = {}; CG.users.forEach(function (u) { NAME[u.id] = u.name; });

  el.innerHTML = '<div class="page-head"><div class="flex"><h1>Demo guide</h1><span class="right">' + (g.can_reset ? '<button class="btn" id="guide-reset" type="button">Reset demo</button>' : '') + '</span></div>' +
    '<p class="muted">The scenarios in demo order. Each button acts as the person shown, then fills New request (not submitted) or opens the case.</p></div>' +
    '<div class="grid g2" id="cards">' + g.cards.map(function (c, i) {
      return '<div class="card demo-card" data-key="' + CG.esc(c.key) + '"><h2>' + CG.esc(c.title) + '</h2><div class="talk">' + CG.esc(c.talk) + '</div><div class="flex">' + c.buttons.map(function (b, j) {
        return '<button class="btn' + (j === 0 ? ' primary' : '') + '" type="button" data-i="' + i + '" data-j="' + j + '">' + CG.esc(b.label) + '</button>'; }).join('') + '</div></div>';
    }).join('') + '</div>';

  function act(b, again) {
    if (CG.me.id !== b.act_as) {                       // switch the demo login, then come back to run the same button with that user's data
      window.localStorage.setItem('cg_user', b.act_as);
      window.location.href = 'demo.html?auto=' + encodeURIComponent(b.key);
      return;
    }
    if (b.kind === 'fill') {
      if (!b.text) { CG.toast('Nothing to fill for this user.', 'error'); return; }
      window.sessionStorage.setItem('cg_prefill', b.text);
      window.location.href = 'intake.html';
    } else if (b.kind === 'case') window.location.href = 'case.html?case=' + encodeURIComponent(b.case);
    else window.location.href = b.href;
  }
  var flat = {};
  g.cards.forEach(function (c, i) { c.buttons.forEach(function (b, j) { flat[i + ':' + j] = Object.assign({}, b, { key: i + ':' + j }); }); });
  CG.$$('#cards button').forEach(function (btn) { btn.onclick = function () { act(flat[btn.dataset.i + ':' + btn.dataset.j]); }; });
  var rs = document.getElementById('guide-reset');
  if (rs) rs.onclick = function () { var top = document.getElementById('reset-demo'); if (top) top.click(); };
  var auto = CG.q('auto');
  if (auto && flat[auto]) act(flat[auto]);
})();
