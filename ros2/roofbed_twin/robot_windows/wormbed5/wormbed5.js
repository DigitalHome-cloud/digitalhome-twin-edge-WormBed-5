// The gantry's robot window in Webots (ros2/roofbed_twin/roofbed_twin/robot_window.py). Webots serves this page
// and relays its messages over its own WebSocket, the same way Cyberbotics' RobotWindow.js does: a message to the
// robot is "robot:<name>:<text>", one from it is "robot:{"message":"...","name":"..."}". Kept here so the window
// needs nothing from the internet.
(function () {
  const T = {
    de: { lead: "Was Sie am Beet tun. Die Aufträge des Roboters bleiben auf der Seite der Edge, wo Sie sie bestätigen.",
      box: "An der Wurmkiste", lid: "Deckel", wrap: "Einpacken", tap: "Hahn des Sumpfs öffnen", bedding: "Trockene Einstreu (500 g)",
      moisten: "Wasser sprenkeln (0,5 l)", bed: "Am Beet", hopper: "Trichter füllen (1 kg)", tank: "Tank auffüllen",
      barrel: "Hahn der Regentonne öffnen", estop: "Not-Halt", release: "Not-Halt lösen", done: "Gerade getan", nothing: "noch nichts",
      link: "Wartet auf die Simulation…", open: "offen", closed: "zu", wrapped: "eingepackt", bare: "ausgepackt", night: "Nacht", day: "Tag" },
    fr: { lead: "Ce que vous faites au bac. Les tâches du robot restent sur la page de l'edge, où vous les confirmez.",
      box: "À la caisse à vers", lid: "Couvercle", wrap: "Emballage", tap: "Ouvrir le robinet du puisard", bedding: "Litière sèche (500 g)",
      moisten: "Arroser (0,5 l)", bed: "Au bac", hopper: "Remplir la trémie (1 kg)", tank: "Remplir la réserve",
      barrel: "Ouvrir le robinet du tonneau", estop: "Arrêt d'urgence", release: "Lever l'arrêt d'urgence", done: "Fait à l'instant", nothing: "rien encore",
      link: "En attente de la simulation…", open: "ouvert", closed: "fermé", wrapped: "emballée", bare: "déballée", night: "nuit", day: "jour" },
  };
  const EN = { open: "open", closed: "closed", wrapped: "wrapped", bare: "unwrapped", release: "Release e-stop", night: "night", day: "day" };
  const lang = (navigator.language || "en").slice(0, 2);
  const say = (k, fallback) => (T[lang] && T[lang][k]) || EN[k] || fallback || k;
  document.documentElement.lang = T[lang] ? lang : "en";
  document.querySelectorAll("[data-t]").forEach((el) => { const k = el.dataset.t; if (T[lang] && T[lang][k]) el.textContent = T[lang][k]; });
  const label = {};
  document.querySelectorAll("button[data-act]").forEach((b) => { const s = b.querySelector("[data-t]") || b; label[b.dataset.act] = s.textContent; });

  const name = decodeURI(new URLSearchParams(location.search).get("name") || "gantry");
  const at = location.href.includes("/~WEBOTS_HOME/") ? location.href.indexOf("/~WEBOTS_HOME/") + 1 : location.href.indexOf("/robot_windows/") + 1;
  const socket = new WebSocket(location.href.substring(0, at).replace("https://", "wss://").replace("http://", "ws://"));
  const waiting = [];
  const send = (text) => (socket.readyState === 1 ? socket.send(`robot:${name}:${text}`) : waiting.push(text));
  socket.onopen = () => { while (waiting.length) send(waiting.shift()); };
  socket.onmessage = (event) => {
    for (const line of String(event.data).split(/\r?\n/)) {
      if (!line.startsWith("robot:")) continue;
      let wrapper;
      try { wrapper = JSON.parse(line.slice(6)); }
      catch (_) {                                   // as RobotWindow.js reads it, should the quotes come unescaped
        const m = line.match('"message":"(.*)","name"'), r = line.match(',"name":"(.*)"}');
        if (!m || !r) continue;
        wrapper = { message: m[1].replace(/\\/g, ""), name: r[1] };
      }
      if (wrapper.name !== name) continue;
      try { show(JSON.parse(wrapper.message)); } catch (_) { /* not ours */ }
    }
  };
  send("init robot window");

  const $ = (id) => document.getElementById(id);
  function show(s) {
    $("link").hidden = true;
    $("clock").textContent = [s.clock, s.date, s.airC != null && `${s.airC} °C`, say(s.night ? "night" : "day")].filter(Boolean).join(" · ");
    const mark = (act, on, text) => { const b = document.querySelector(`[data-act="${act}"]`); b.classList.toggle("on", !!on); const em = b.querySelector("em"); if (em) em.textContent = text; };
    mark("lid", s.lidOpen, say(s.lidOpen ? "open" : "closed"));
    mark("wrap", s.insulated, say(s.insulated ? "wrapped" : "bare"));
    mark("tap", false, s.sumpL != null ? `${s.sumpL} l` : "");
    mark("hopper", false, `${s.hopperG} g`);
    mark("tank", false, `${s.tankL} / ${s.tankCap} l`);
    mark("barrel", false, s.barrelL != null ? `${s.barrelL} l` : "");
    $("estop").classList.toggle("on", !!s.stopped);
    $("estop").textContent = s.stopped ? say("release") : say("estop", "E-stop");
    $("trouble").hidden = !(s.trouble || []).length;
    $("trouble").textContent = (s.trouble || []).join(" · ");
    const acts = (s.acts || []).slice().reverse();
    if (acts.length) $("acts").innerHTML = acts.map((a) => `<li>${a.clock} · ${label[a.act] || a.act}</li>`).join("");
  }

  document.querySelectorAll("button[data-act]").forEach((b) =>
    b.addEventListener("click", () => send(JSON.stringify({ act: b.dataset.act }))));
})();
