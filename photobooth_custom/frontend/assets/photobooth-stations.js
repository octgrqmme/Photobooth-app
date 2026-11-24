(function () {
  "use strict";

  async function request(path, options) {
    var opts = options || {};
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 15000);
    try {
      var response = await fetch("/api/share" + path, {
        method: opts.method || "GET",
        cache: "no-store",
        signal: controller.signal,
        headers: Object.assign({ "X-PB-Station": "1", "Content-Type": "application/json" }, opts.headers),
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body)
      });
      var data = await response.json();
      if (!response.ok) {
        var error = new Error(typeof data.detail === "string" ? data.detail : "ทำรายการไม่สำเร็จ (" + response.status + ")");
        error.status = response.status;
        throw error;
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  async function authorize(role) {
    var hash = new URLSearchParams(location.hash.slice(1));
    if (hash.has("pair")) {
      var code = hash.get("pair");
      history.replaceState(null, "", location.pathname + location.search);
      await request("/stations/" + role + "/pair", { method: "POST", body: { code: code } });
    }
    return (await request("/stations/" + role + "/session")).paired;
  }

  window.pbStations = { request: request, authorize: authorize };
}());
