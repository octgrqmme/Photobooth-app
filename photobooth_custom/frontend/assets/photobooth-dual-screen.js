(function () {
  "use strict";

  var operator = document.documentElement.dataset.pbView === "operator";
  document.title = operator ? "Photobooth | จอควบคุม" : "Photobooth | จอถ่าย";

  if (operator) {
    var queueColumn = document.getElementById("pb-queue-column");
    var studioColumn = document.getElementById("pb-studio-column");
    var queuePanel = document.getElementById("pb-guest-queue-panel");
    var studioSidebar = document.getElementById("photobooth-sidebar");
    if (queueColumn && studioColumn && queuePanel && studioSidebar) {
      queueColumn.appendChild(queuePanel);
      var heading = document.createElement("div");
      heading.className = "pb-zone-heading";
      heading.innerHTML = '<strong>กล้องและชุดภาพ</strong><small>ภาพสดจาก Canon</small>';
      studioColumn.appendChild(heading);
      studioColumn.appendChild(document.getElementById("pb-camera-engine-container"));
      studioColumn.appendChild(document.getElementById("pb-top-banner"));
      studioColumn.appendChild(studioSidebar);
    }
    var menus = [document.getElementById("pb-operator-status"), document.getElementById("pb-screen-menu"), document.getElementById("pb-print-island")];
    menus.forEach(function (menu) {
      menu.addEventListener("toggle", function () {
        if (menu.open) menus.forEach(function (other) { if (other !== menu) other.open = false; });
      });
    });
    document.addEventListener("click", function (event) {
      menus.forEach(function (menu) { if (!menu.contains(event.target)) menu.open = false; });
    });
  }

  var islandState = { camera: null, active: null, waiting: 0, printing: false, printCount: 0, queueError: false, printError: false };
  function renderIsland() {
    if (!operator) return;
    var title = document.getElementById("pb-island-title");
    var island = document.getElementById("pb-operator-status");
    island.dataset.state = islandState.queueError || islandState.printError || islandState.camera === false ? "warning" : islandState.printing ? "busy" : "ready";
    title.textContent = islandState.queueError ? "คิวไม่เชื่อมต่อ" : islandState.camera === false ? "กล้องออฟไลน์" : islandState.printError ? "ตรวจคิวพิมพ์" : islandState.printing ? "กำลังพิมพ์" : islandState.active ? "กำลังเรียกคิว " + islandState.active : islandState.camera === null ? "กำลังตรวจสอบระบบ" : "พร้อมใช้งาน";
  }

  var latestPrintQueue = null;
  var latestGuestQueue = null;
  function printIslandItem(host, code, detail, stage) {
    var item = document.createElement("div");
    item.className = "pb-print-island-item";
    item.dataset.stage = stage;
    var heading = document.createElement("div");
    var strong = document.createElement("strong");
    strong.textContent = code;
    var small = document.createElement("small");
    small.textContent = detail;
    heading.append(strong, small);
    var bar = document.createElement("span");
    bar.className = "pb-print-island-track";
    bar.setAttribute("aria-hidden", "true");
    item.append(heading, bar);
    host.appendChild(item);
  }

  function renderPrintIsland() {
    if (!operator) return;
    var activeHost = document.getElementById("pb-print-island-active");
    var waitingHost = document.getElementById("pb-print-island-waiting");
    var doneHost = document.getElementById("pb-print-island-done");
    if (!activeHost || !waitingHost || !doneHost) return;
    activeHost.replaceChildren();
    waitingHost.replaceChildren();
    doneHost.replaceChildren();
    var print = latestPrintQueue || {};
    var jobs = print.queue || [];
    var activeJobs = jobs.filter(function (job) { return job.is_active; });
    activeJobs.forEach(function (job) {
      printIslandItem(activeHost, job.queue_code ? "คิว " + job.queue_code : "งาน #" + job.job_id, job.printer_name || "เครื่องพิมพ์กำลังทำงาน", "active");
    });
    if (!activeJobs.length) activeHost.textContent = islandState.printError ? "ตรวจการเชื่อมต่อเครื่องพิมพ์" : "ยังไม่มีงานกำลังพิมพ์";
    var waiting = jobs.filter(function (job) { return !job.is_active; });
    waiting.slice(0, 5).forEach(function (job) {
      var printer = (print.printers || []).find(function (entry) { return entry.name === job.printer_name; });
      printIslandItem(waitingHost, job.queue_code ? "คิว " + job.queue_code : "งาน #" + job.job_id, (printer && !printer.online ? "เครื่องไม่พร้อม · " : "รอพิมพ์ · ") + (job.printer_name || job.job_id), "waiting");
    });
    if (waiting.length > 5) printIslandItem(waitingHost, "อีก " + (waiting.length - 5) + " งาน", "เปิดหน้าจัดการเพื่อดูทั้งหมด", "waiting");
    if (!waiting.length) waitingHost.textContent = "ไม่มีงานรอพิมพ์";
    var completed = [];
    (latestGuestQueue && latestGuestQueue.entries || []).forEach(function (entry) {
      (entry.print_jobs || []).forEach(function (job) {
        if (job.state === "printed") completed.push({ code: entry.code, job: job });
      });
    });
    completed.sort(function (a, b) { return (b.job.completed_at || b.job.submitted_at || "").localeCompare(a.job.completed_at || a.job.submitted_at || ""); });
    completed.slice(0, 3).forEach(function (item) { printIslandItem(doneHost, "คิว " + item.code, (item.job.copies || 1) + " ใบ · " + (item.job.printer || "รับภาพได้"), "done"); });
    if (!completed.length) doneHost.textContent = "ดูสถานะงานพิมพ์ในหน้าจัดการคิวพิมพ์";
    document.getElementById("pb-print-island-updated").textContent = "อัปเดต " + new Date().toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  window.pbUpdateOperatorStatus = function (update) {
    if (!operator) return;
    update = update || {};
    if (update.queue) {
      var queue = update.queue;
      latestGuestQueue = queue;
      var waiting = (queue.entries || []).filter(function (entry) { return entry.status === "waiting"; }).length;
      islandState.active = queue.active_code || null;
      islandState.waiting = waiting;
      islandState.queueError = false;
      document.getElementById("pb-status-active").textContent = queue.active_code || "—";
      document.getElementById("pb-status-waiting").textContent = waiting + " คิว";
      var sync = document.getElementById("pb-status-sync");
      sync.textContent = "คิวอัปเดต " + new Date().toLocaleTimeString("th-TH");
      sync.parentNode.classList.remove("is-error");
    }
    if (update.queueError) {
      islandState.queueError = true;
      document.getElementById("pb-status-sync").textContent = "เชื่อมต่อคิวไม่ได้";
      document.querySelector(".pb-status-sync").classList.add("is-error");
    }
    if (update.print) {
      latestPrintQueue = update.print;
      var count = Number(update.print.queue_count) || 0;
      islandState.printCount = count;
      islandState.printing = !!update.print.is_printing;
      islandState.printError = false;
      document.getElementById("pb-status-print").textContent = count + " งาน";
      document.getElementById("pb-header-print-count").textContent = count;
      document.querySelector(".pb-status-print").dataset.state = update.print.is_printing ? "printing" : "idle";
    }
    if (update.printError) {
      islandState.printError = true;
      document.getElementById("pb-status-print").textContent = "ตรวจสอบ";
      document.querySelector(".pb-status-print").dataset.state = "error";
    }
    if (Object.prototype.hasOwnProperty.call(update, "camera")) {
      var connected = update.camera === true;
      islandState.camera = connected;
      document.getElementById("pb-status-camera").textContent = connected ? "พร้อมใช้งาน" : "ยังไม่เชื่อมต่อ";
      document.querySelector(".pb-status-camera").dataset.state = connected ? "online" : "offline";
    }
    renderIsland();
    if (update.queue || update.print || update.printError) renderPrintIsland();
  };

  window.pbOpenDisplayWindow = function (view) {
    var target = view === "operator" ? "operator" : "guest";
    var url = "/?view=" + target + "#/";
    var opened = window.open(url, "photobooth-" + target, "width=1440,height=900,resizable=yes,scrollbars=yes");
    if (opened) opened.focus();
    else if (typeof window.pbShowToast === "function") window.pbShowToast("Safari ไม่เปิดหน้าต่างใหม่ กรุณาเปิด " + url + " ในหน้าต่าง Safari ใหม่", 6000);
  };

  var queueBoardWindow = null;
  window.pbEnsureQueueBoard = function () {
    if (!queueBoardWindow || queueBoardWindow.closed) {
      queueBoardWindow = window.open("/queue-board.html", "photobooth-queue-board", "width=1280,height=800,resizable=yes,scrollbars=yes");
      if (!queueBoardWindow && typeof window.pbShowToast === "function") window.pbShowToast("เบราว์เซอร์บล็อกจอคิว กรุณาอนุญาตหน้าต่างใหม่", 4500);
    }
    return queueBoardWindow;
  };
  window.pbOpenQueueBoard = function () {
    var opened = window.pbEnsureQueueBoard();
    if (opened) opened.focus();
  };

  if (!operator) {
    var chooserButton = document.createElement("button");
    chooserButton.id = "pb-guest-template-button";
    chooserButton.type = "button";
    chooserButton.textContent = "เลือกกรอบรูป";
    chooserButton.addEventListener("click", function () {
      var overlay = document.getElementById("pb-guest-template-overlay");
      var frame = overlay.querySelector("iframe");
      frame.src = "/template-chooser.html";
      overlay.hidden = false;
    });
    document.body.appendChild(chooserButton);
    var chooserOverlay = document.createElement("div");
    chooserOverlay.id = "pb-guest-template-overlay";
    chooserOverlay.hidden = true;
    chooserOverlay.innerHTML = '<button type="button" aria-label="ปิดหน้าเลือกเทมเพลต">× ปิดและกลับไปจอถ่าย</button><iframe title="เลือกกรอบรูป" loading="lazy"></iframe>';
    chooserOverlay.querySelector("button").addEventListener("click", function () {
      chooserOverlay.hidden = true;
      var frame = chooserOverlay.querySelector("iframe");
      if (frame) frame.src = "about:blank";
    });
    document.body.appendChild(chooserOverlay);
    return;
  }

  // The gallery lives in this window; it never navigates or changes the
  // customer-facing preview in the second window.
  if (typeof window.pbSetGalleryFilter === "function") {
    window.pbSetGalleryFilter("collage");
    document.getElementById("pb-gallery-modal").classList.remove("pb-hidden");
  }

  var lastVisibleCollageId = null;
  var galleryCheckInFlight = false;
  function refreshCompletedPhotos() {
    if (document.hidden || galleryCheckInFlight || document.getElementById("pb-gallery-grid-view").style.display === "none") return;
    galleryCheckInFlight = true;
    fetch("/api/mediacollection/?limit=8", { cache: "no-store" })
      .then(function (response) { if (!response.ok) throw new Error("gallery"); return response.json(); })
      .then(function (items) {
        var newest = items.find(function (item) { return item.media_type === "collage" && item.show_in_gallery !== false; });
        var id = newest ? newest.id : null;
        if (id !== lastVisibleCollageId) {
          lastVisibleCollageId = id;
          if (typeof window.pbRenderGalleryGrid === "function") window.pbRenderGalleryGrid();
        }
      })
      .catch(function () {})
      .finally(function () { galleryCheckInFlight = false; });
  }
  refreshCompletedPhotos();
  setInterval(refreshCompletedPhotos, 4000);
})();
