(function () {
  "use strict";
  const root = document.getElementById("sh");
  if (!root) return;
  const LIVE_URL = root.dataset.liveUrl, REM_URL = root.dataset.remUrl;
  const IN_SEASON = !!root.dataset.season, REMINDERS = !!root.dataset.reminders;
  const csrf = (root.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
  const geo = JSON.parse(document.getElementById("sh-geo").textContent);
  const $ = (id) => document.getElementById(id);

  // Sun Peaks is fixed UTC-7; format times there whatever the browser thinks.
  function hm(d) {
    const t = new Date(d.getTime() - 7 * 3600e3);
    const h = t.getUTCHours(), m = t.getUTCMinutes();
    return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  }
  function rel(ms) {
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return "now";
    if (mins < 60) return `in ${mins} min`;
    const h = Math.floor(mins / 60), m = mins % 60;
    return m ? `in ${h} h ${m} min` : `in ${h} h`;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- map -------------------------------------------------------------
  const route = geo.route;
  const map = L.map("sh-map", { zoomControl: true, attributionControl: true, zoomSnap: 0.25 });
  map.attributionControl.setPrefix(false);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
  }).addTo(map);
  L.polyline(route, { color: "#7cc4ff", weight: 7, opacity: 0.85 }).addTo(map);
  geo.stops.forEach((s) => {
    const code = s.id;
    const ours = code === geo.our;
    const name = ours ? `${code} ${s.name} · our stop` : `${code === "VDL" ? "" : code + " "}${s.name}`;
    const side = !ours && s.label === "left" ? " left" : "";
    const label = ours || s.label ? `<span class="sh-slab${side}">${esc(name)}</span>` : "";
    const size = ours ? 30 : 16;
    L.marker([s.lat, s.lon], {
      zIndexOffset: ours ? 500 : 0,
      icon: L.divIcon({
        className: "", html: `<div class="${ours ? "sh-our" : "sh-stop"}">${label}</div>`,
        iconSize: [size, size], iconAnchor: [size / 2, size / 2],
      }),
    }).bindTooltip(esc(name)).addTo(map);
  });
  // Labels hang off to the right of their stops; leave room for them.
  const mw = document.getElementById("sh-map").clientWidth;
  map.fitBounds(L.latLngBounds(route), {
    paddingTopLeft: [Math.round(mw * 0.03), 70],
    paddingBottomRight: [Math.min(220, Math.round(mw * 0.18)), 50],
  });

  // ---- live bus --------------------------------------------------------
  const buses = new Map();
  function busIcon(stale) {
    return L.divIcon({
      className: "", html: `<div class="sh-bus${stale ? " stale" : ""}"><i class="fas fa-bus-simple"></i></div>`,
      iconSize: [46, 46], iconAnchor: [23, 23],
    });
  }
  function ago(s) {
    if (s < 60) return `${Math.max(1, Math.round(s))} s ago`;
    return `${Math.round(s / 60)} min ago`;
  }
  function setLive(title, sub, off) {
    $("sh-lt").textContent = title;
    $("sh-ls").textContent = sub || "";
    $("sh-live").classList.toggle("off", !!off);
  }
  async function pollLive() {
    let data;
    try {
      const r = await fetch(LIVE_URL, { cache: "no-store" });
      data = await r.json();
    } catch (e) {
      data = { ok: false, buses: [] };
    }
    const seen = new Set();
    (data.buses || []).forEach((b, i) => {
      const key = b.id || String(i);
      seen.add(key);
      const ll = [b.lat, b.lon];
      if (buses.has(key)) {
        buses.get(key).setLatLng(ll).setIcon(busIcon(data.stale));
      } else {
        buses.set(key, L.marker(ll, { zIndexOffset: 1000, icon: busIcon(data.stale) }).addTo(map));
      }
    });
    for (const [key, m] of buses) if (!seen.has(key)) { m.remove(); buses.delete(key); }

    const b = (data.buses || [])[0];
    if (b) {
      const where = b.at_our_stop ? "At Lookout Ridge now" : (b.near ? `Near ${b.near}` : "On the East loop");
      setLive(data.buses.length > 1 ? `${data.buses.length} shuttles running` : "East shuttle",
              `${where} · updated ${ago(b.age_s)}`, data.stale);
    } else if (!data.ok) {
      setLive("Live location unavailable", "The timetable still applies", true);
    } else {
      setLive("No shuttle on the road right now", "Live location shows when a bus is running", true);
    }
  }
  if (IN_SEASON) {
    pollLive();
    setInterval(pollLive, 10000);
  }

  // ---- tabs + relative times ------------------------------------------
  root.querySelectorAll(".sh-tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      root.querySelectorAll(".sh-tabs button").forEach((b) => b.classList.toggle("on", b === btn));
      root.querySelectorAll(".sh-list").forEach((l) => { l.hidden = l.dataset.list !== btn.dataset.tab; });
    });
  });
  function tick() {
    const now = Date.now();
    let reload = false;
    root.querySelectorAll("[data-t]").forEach((el) => {
      const ms = new Date(el.dataset.t).getTime() - now;
      if (el.dataset.next && el.textContent.trim() === "") return; // tomorrow
      if (ms < -60000) { reload = true; return; }
      const first = el.firstChild;
      if (first && first.nodeType === Node.TEXT_NODE) first.textContent = rel(ms);
      else el.textContent = rel(ms);
    });
    refreshLeads();
    if (reload) location.reload();
  }
  setInterval(tick, 30000);

  // ---- reminders -------------------------------------------------------
  if (!REMINDERS) return;
  let reminders = [];
  let current = null; // {dep, label}
  let lead = 10;

  const findRem = (iso) => {
    const t = new Date(iso).getTime();
    return reminders.find((r) => new Date(r.departure).getTime() === t);
  };

  function paint() {
    root.querySelectorAll("[data-dep]").forEach((btn) => {
      const on = !!findRem(btn.dataset.dep);
      btn.classList.toggle("on", on);
      const i = btn.querySelector("i");
      if (i) i.className = `${on || btn.classList.contains("bell") ? "fas" : "far"} fa-bell`;
      if (btn.classList.contains("bell")) {
        btn.lastChild.textContent = on ? "Reminder set" : "Remind me";
      }
    });
    $("sh-rems").innerHTML = reminders.map((r) => {
      const dep = new Date(r.departure), fire = new Date(r.fire_at);
      return `<div class="sh-rem"><i class="fas fa-bell"></i><div><div>${hm(dep)} shuttle · ${r.lead_min} min heads-up</div>` +
        `<div class="s">Dining room speaker at ${hm(fire)}</div></div>` +
        `<button type="button" class="x" data-cancel="${esc(r.id)}">Cancel</button></div>`;
    }).join("");
  }

  async function api(method, body) {
    const opts = { method, cache: "no-store", headers: { "X-CSRFToken": csrf } };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(REM_URL, opts);
    let data = {};
    try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) throw new Error(data.error || "Couldn't reach the reminder service.");
    reminders = data.reminders || [];
    paint();
  }
  const load = () => api("GET").catch(() => {});
  load();
  setInterval(load, 60000);

  const ov = $("sh-ov");
  function sayText(n) {
    return `“Heads up! The shuttle to the Village reaches Lookout Ridge in about ${n} minutes. Time to put your boots on!”`;
  }
  function refreshLeads() {
    if (!current || ov.hidden) return;
    const dep = new Date(current.dep).getTime(), now = Date.now();
    let firstOk = null;
    ov.querySelectorAll("[data-lead]").forEach((b) => {
      const n = +b.dataset.lead, at = dep - n * 60000;
      b.querySelector("small").textContent = hm(new Date(at));
      b.disabled = at <= now + 30000;
      if (!b.disabled && firstOk === null) firstOk = n;
    });
    const btn = ov.querySelector(`[data-lead="${lead}"]`);
    if (!btn || btn.disabled) {
      const ok = [...ov.querySelectorAll("[data-lead]")].filter((b) => !b.disabled).map((b) => +b.dataset.lead);
      lead = ok.length ? Math.max(...ok.filter((n) => n <= 10), ok[0]) : null;
    }
    ov.querySelectorAll("[data-lead]").forEach((b) => b.classList.toggle("on", +b.dataset.lead === lead));
    $("sh-say").textContent = lead ? sayText(lead) : "Too late for a heads-up on this one.";
    $("sh-ok").disabled = !lead;
  }
  function open(dep, label) {
    current = { dep, label };
    const existing = findRem(dep);
    lead = existing ? existing.lead_min : 10;
    $("sh-dt").textContent = label;
    $("sh-err").textContent = "";
    $("sh-remove").hidden = !existing;
    $("sh-ok").textContent = existing ? "Update reminder" : "Set reminder";
    ov.hidden = false;
    refreshLeads();
  }
  const close = () => { ov.hidden = true; current = null; };

  root.addEventListener("click", (e) => {
    const bell = e.target.closest("[data-dep]");
    if (bell) return open(bell.dataset.dep, bell.dataset.label);
    const x = e.target.closest("[data-cancel]");
    if (x) {
      x.disabled = true;
      api("POST", { action: "cancel", id: x.dataset.cancel }).catch(() => { x.disabled = false; });
    }
  });
  ov.querySelectorAll("[data-lead]").forEach((b) => b.addEventListener("click", () => {
    if (b.disabled) return;
    lead = +b.dataset.lead;
    refreshLeads();
  }));
  $("sh-close").addEventListener("click", close);
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });

  async function submit(body, btn) {
    btn.disabled = true;
    $("sh-err").textContent = "";
    try {
      await api("POST", body);
      close();
    } catch (err) {
      $("sh-err").textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  }
  $("sh-ok").addEventListener("click", () => {
    if (current && lead) submit({ action: "add", departure: current.dep, lead_min: lead }, $("sh-ok"));
  });
  $("sh-remove").addEventListener("click", () => {
    const r = current && findRem(current.dep);
    if (r) submit({ action: "cancel", id: r.id }, $("sh-remove"));
  });
})();
