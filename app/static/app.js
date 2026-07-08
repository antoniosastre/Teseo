// Teseo — interactividad del panel.
(function () {
  "use strict";

  // --- Filas expandibles (destinos / orígenes) ---
  document.querySelectorAll("tr.row-main").forEach(function (row) {
    row.addEventListener("click", function (ev) {
      if (ev.target.closest("button, a, form, input")) {
        if (!ev.target.classList.contains("expander")) return;
      }
      var id = row.getAttribute("data-target");
      var detail = document.getElementById(id);
      if (!detail) return;
      var hidden = detail.hasAttribute("hidden");
      if (hidden) detail.removeAttribute("hidden"); else detail.setAttribute("hidden", "");
      var exp = row.querySelector(".expander");
      if (exp) exp.classList.toggle("open", hidden);
    });
  });

  // --- Probar conexión (destinos / hosts) ---
  document.querySelectorAll(".test-btn").forEach(function (btn) {
    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var url = btn.getAttribute("data-url");
      btn.textContent = "Probando…";
      fetch(url, { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          btn.textContent = "Probar";
          alert(d.message || (d.ok ? "OK" : "Error"));
        })
        .catch(function () { btn.textContent = "Probar"; alert("Error de red"); });
    });
  });

  // --- Alta inline de ubicación ---
  var addUbic = document.getElementById("add-ubicacion");
  if (addUbic) {
    addUbic.addEventListener("click", function () {
      var nombre = prompt("Nombre de la nueva ubicación física:");
      if (!nombre) return;
      var fd = new FormData();
      fd.append("nombre", nombre);
      fetch("/ubicaciones", { method: "POST", body: fd })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.error) { alert(d.error); return; }
          var sel = document.getElementById("ubicacion-select");
          var opt = document.createElement("option");
          opt.value = d.id; opt.textContent = d.nombre; opt.selected = true;
          sel.appendChild(opt);
        });
    });
  }

  // --- Examinar carpetas del host (formulario de tarea) ---
  var browseBtn = document.getElementById("browse-btn");
  if (browseBtn) {
    var browser = document.getElementById("browser");
    var loadDirs = function (path) {
      var hostId = document.getElementById("host_id").value;
      browser.removeAttribute("hidden");
      browser.innerHTML = "<div class='muted'>Cargando " + path + "…</div>";
      fetch("/origenes/host/" + hostId + "/carpetas?path=" + encodeURIComponent(path))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.error) { browser.innerHTML = "<div class='alert error'>" + d.error + "</div>"; return; }
          browser.innerHTML = "";
          var up = document.createElement("div");
          up.className = "dir"; up.textContent = "⬆ .. (subir)";
          up.onclick = function () {
            var parent = path.replace(/\/+$/, "").split("/").slice(0, -1).join("/") || "/";
            loadDirs(parent);
          };
          browser.appendChild(up);
          (d.dirs || []).forEach(function (dir) {
            var el = document.createElement("div");
            el.className = "dir"; el.textContent = "📁 " + dir;
            el.onclick = function () {
              document.getElementById("carpeta_origen").value = dir;
              loadDirs(dir);
            };
            browser.appendChild(el);
          });
        })
        .catch(function () { browser.innerHTML = "<div class='alert error'>Error de red</div>"; });
    };
    browseBtn.addEventListener("click", function () {
      var current = document.getElementById("carpeta_origen").value || "/";
      loadDirs(current);
    });
  }

  // --- Preview del comando rsync ---
  var previewBtn = document.getElementById("preview-btn");
  if (previewBtn) {
    previewBtn.addEventListener("click", function () {
      var fd = new FormData();
      fd.append("host_id", document.getElementById("host_id").value);
      fd.append("destino_id", document.getElementById("destino_id").value);
      fd.append("carpeta_origen", document.getElementById("carpeta_origen").value);
      fd.append("tipo", document.getElementById("tipo").value);
      fd.append("rsync_extra", document.getElementById("rsync_extra").value);
      fetch("/origenes/preview", { method: "POST", body: fd })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.command) document.getElementById("comando_rsync").value = d.command;
          else alert(d.error || "No se pudo generar");
        });
    });
  }

  // --- Opciones del conector en el alta de host (mostrar según el elegido) ---
  var conectorSel = document.getElementById("tipo_conector-select");
  if (conectorSel) {
    var toggleOpciones = function () {
      document.querySelectorAll(".opciones-conector").forEach(function (bloque) {
        var coincide = bloque.getAttribute("data-conector") === conectorSel.value;
        if (coincide) bloque.removeAttribute("hidden");
        else bloque.setAttribute("hidden", "");
      });
    };
    conectorSel.addEventListener("change", toggleOpciones);
    toggleOpciones();
  }

  // --- Estado en vivo por SSE (orígenes / destinos) ---
  if (document.querySelector("[data-tarea-bar], [data-host], [data-destino]")) {
    try {
      var es = new EventSource("/estado/stream");
      es.onmessage = function (ev) {
        var data = JSON.parse(ev.data);
        Object.keys(data.tareas || {}).forEach(function (id) {
          var t = data.tareas[id];
          var bar = document.querySelector("[data-tarea-bar='" + id + "']");
          if (bar) bar.style.width = t.porcentaje + "%";
          var pct = document.querySelector("[data-tarea-pct='" + id + "']");
          if (pct) pct.textContent = t.porcentaje + "%";
          var cancelando = t.estado === "en_progreso" && t.cancelando;
          var est = document.querySelector("[data-tarea-estado='" + id + "']");
          if (est) {
            est.textContent = cancelando ? "cancelando…" : t.estado;
            est.className = "badge estado-" + (cancelando ? "cancelando" : t.estado);
          }
          var cancel = document.querySelector("[data-tarea-cancel='" + id + "']");
          if (cancel) {
            // Visible solo si está en progreso Y aún no se ha pedido cancelar.
            if (t.estado === "en_progreso" && !t.cancelando) cancel.removeAttribute("hidden");
            else cancel.setAttribute("hidden", "");
          }
        });
        Object.keys(data.hosts || {}).forEach(function (id) {
          var h = data.hosts[id];
          var sem = document.querySelector("[data-host='" + id + "']");
          if (sem) sem.className = "semaforo s-" + h.semaforo;
        });
      };
    } catch (e) { /* SSE no disponible */ }
  }
})();
