/* CareGrid web UI: the ONLY way pages get data is through this module, which calls the HTTP API. No data and no business logic here.
   The acting user id (demo auth) is a UI preference and may be kept in localStorage; if storage is unavailable it lives in memory. */
(function () {
  var KEY = 'cg_user';
  var memory = null;

  function getUser() {
    try { return window.localStorage.getItem(KEY) || memory || 'U1'; } catch (e) { return memory || 'U1'; }
  }
  function setUser(id) {
    memory = id;
    try { window.localStorage.setItem(KEY, id); } catch (e) { /* storage blocked: keep it in memory */ }
  }

  async function call(method, path, body) {
    var headers = { 'X-CareGrid-User': getUser() };
    var opts = { method: method, headers: headers };
    if (body !== undefined) { headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    var res = await fetch(path, opts);
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var detail = data && data.detail;
      var msg = typeof detail === 'string' ? detail : (detail ? 'Invalid request.' : res.statusText);
      var err = new Error(msg);
      err.status = res.status;
      err.restricted = !!(data && data.restricted);
      throw err;
    }
    return data;
  }

  window.CG_API = {
    get: function (path) { return call('GET', path); },
    post: function (path, body) { return call('POST', path, body === undefined ? {} : body); },
    getUser: getUser,
    setUser: setUser
  };
})();
