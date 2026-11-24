(function () {
  "use strict";

  var operator = document.documentElement.dataset.pbView === "operator";
  var latest = null;
  var busy = false;
  var polling = false;
  var mediaToCode = new Map();
  var ticketEntry = null;
  var ticketRoundName = "";
  var renderedListSignature = "";
  var renderedCategoriesSignature = "";
  var lastCallAtByCode = Object.create(null);
  var callCooldownMs = 15000;

  function statusLabel(status) {
    return ({ waiting: "รอเรียก", shooting: "กำลังเรียก", ready: "เสร็จสิ้น", printing: "กำลังพิมพ์", printed: "พิมพ์เสร็จ" })[status] || status;
  }

  function showMessage(message) {
    var el = document.getElementById("pb-gq-message");
    if (el) el.textContent = message || "";
    if (message && typeof window.pbShowToast === "function") window.pbShowToast(message, 3500);
  }

  function makeIdempotencyKey(prefix) {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return prefix + "-" + window.crypto.randomUUID();
    return prefix + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  async function request(path, method, payload, extraHeaders) {
    var headers = Object.assign({}, payload ? { "Content-Type": "application/json" } : {}, extraHeaders || {});
    var response = await fetch(path, { method: method, cache: "no-store", headers: headers, body: payload ? JSON.stringify(payload) : undefined });
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.detail || "ดำเนินการไม่สำเร็จ");
    return data;
  }

  function post(path) { return request(path, "POST"); }

  function makeButton(label, action, disabled) {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.disabled = !!disabled;
    button.addEventListener("click", action);
    return button;
  }

  function ticketMessage(message) {
    var output = document.getElementById("pb-ticket-message");
    if (output) output.textContent = message || "";
  }

  function ticketShape(ctx, x, y, width, height, radius) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  }

  function ticketText(ctx, value, x, y, maxWidth, size, weight, color, align) {
    ctx.fillStyle = color;
    ctx.textAlign = align || "left";
    var fontSize = size;
    do {
      ctx.font = weight + " " + fontSize + "px -apple-system, BlinkMacSystemFont, Thonburi, Arial, sans-serif";
      fontSize -= 2;
    } while (ctx.measureText(String(value)).width > maxWidth && fontSize >= 24);
    ctx.fillText(String(value), x, y);
  }

  function drawTicket(entry, data) {
    var canvas = document.getElementById("pb-queue-ticket-canvas");
    if (!canvas || !entry) return;
    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    var width = canvas.width;
    var height = canvas.height;
    ctx.fillStyle = "#f8f2ea";
    ctx.fillRect(0, 0, width, height);
    ctx.save();
    ticketShape(ctx, 28, 28, width - 56, height - 56, 54);
    ctx.clip();
    ctx.fillStyle = "#181319";
    ctx.fillRect(28, 28, width - 56, height - 56);
    var glow = ctx.createRadialGradient(450, 670, 15, 450, 850, 800);
    glow.addColorStop(0, "#713e4b");
    glow.addColorStop(.58, "#352530");
    glow.addColorStop(1, "#18151d");
    ctx.fillStyle = glow;
    ctx.fillRect(28, 440, width - 56, 720);
    var footer = ctx.createLinearGradient(100, 1110, 800, 1460);
    footer.addColorStop(0, "#563141");
    footer.addColorStop(.62, "#554057");
    footer.addColorStop(1, "#746279");
    ctx.fillStyle = footer;
    ctx.fillRect(28, 1135, width - 56, 345);
    ctx.strokeStyle = "#d0af8c39";
    ctx.lineWidth = 2;
    for (var i = 0; i < 4; i++) {
      ctx.beginPath();
      ctx.moveTo(28 + i * 22, 250);
      ctx.bezierCurveTo(110 + i * 11, 550, 95 + i * 12, 850, 28 + i * 22, 1080);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(872 - i * 22, 250);
      ctx.bezierCurveTo(790 - i * 11, 550, 805 - i * 12, 850, 872 - i * 22, 1080);
      ctx.stroke();
    }
    var spotlight = ctx.createRadialGradient(450, 635, 30, 450, 635, 420);
    spotlight.addColorStop(0, "#f6caa71c");
    spotlight.addColorStop(1, "#f6caa700");
    ctx.fillStyle = spotlight;
    ctx.fillRect(85, 350, 730, 620);
    ticketText(ctx, "THE LAST TAKE · SCILYWOOD", 450, 112, 700, 35, "800", "#f4ddbd", "center");
    ticketText(ctx, "OFFICIAL PHOTOBOOTH QUEUE", 450, 165, 700, 22, "700", "#b8abb7", "center");
    ctx.strokeStyle = "#b8916f88";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(90, 207); ctx.lineTo(810, 207); ctx.stroke();
    ctx.strokeStyle = "#d2b18a60";
    ctx.lineWidth = 2;
    ticketShape(ctx, 48, 48, width - 96, height - 96, 40);
    ctx.stroke();
    var issued = new Date(entry.created_at);
    var dateText = Number.isNaN(issued.getTime()) ? "—" : new Intl.DateTimeFormat("th-TH", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(issued);
    var ticketPrefix = entry.prefix || String(entry.code || "").replace(/\d+$/, "");
    var category = (data.categories || []).find(function (item) { return item.prefix === ticketPrefix; });
    ticketText(ctx, "ออกบัตร", 96, 257, 170, 23, "700", "#b6a7af");
    ticketText(ctx, dateText, 96, 298, 450, 29, "700", "#ffffff");
    ticketText(ctx, "จำนวน", 700, 257, 110, 23, "700", "#b6a7af", "right");
    ticketText(ctx, (entry.people_count || 1) + " คน", 700, 298, 160, 34, "800", "#ffffff", "right");
    ctx.strokeStyle = "#b8916f66";
    ctx.beginPath(); ctx.moveTo(90, 337); ctx.lineTo(810, 337); ctx.stroke();
    ticketText(ctx, "YOUR PHOTO PASS", 450, 477, 700, 32, "800", "#f0c99d", "center");
    ticketText(ctx, entry.code, 450, 710, 730, 188, "900", "#ffffff", "center");
    ticketText(ctx, category ? category.name : ticketPrefix, 450, 791, 700, 38, "700", "#f5d4c4", "center");
    ticketText(ctx, "โปรดเก็บบัตรนี้ไว้และรอเรียกหมายเลขบนจอคิว", 450, 923, 730, 28, "600", "#f2e3de", "center");
    ticketText(ctx, "ออกคิวโดยเจ้าหน้าที่ · ไม่ใช่ใบรับภาพพิมพ์", 450, 975, 730, 23, "500", "#d2b5bf", "center");
    ctx.setLineDash([5, 17]);
    ctx.strokeStyle = "#f4e8e0aa";
    ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(90, 1128); ctx.lineTo(810, 1128); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#f8f2ea";
    ctx.beginPath(); ctx.arc(28, 1128, 30, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(872, 1128, 30, 0, Math.PI * 2); ctx.fill();
    ticketText(ctx, "ROUND / รอบงาน", 100, 1235, 600, 24, "800", "#dfd3e6");
    ticketText(ctx, ticketRoundName || "—", 100, 1302, 690, 42, "800", "#ffffff");
    ticketText(ctx, "TICKET ID  " + String(entry.round_id || data.round_id || "").slice(0, 8).toUpperCase() + " · " + entry.code, 100, 1394, 700, 24, "700", "#e2d8e7");
    ctx.restore();
  }

  function closeTicket() {
    var modal = document.getElementById("pb-queue-ticket-modal");
    if (modal) modal.classList.add("pb-hidden");
    if (modal) modal.classList.remove("is-photo-mode");
    var expand = document.getElementById("pb-ticket-expand");
    if (expand) expand.textContent = "⛶ ขยายตั๋วให้ถ่ายชัด";
    ticketEntry = null;
  }

  function openTicket(entry, data) {
    var modal = document.getElementById("pb-queue-ticket-modal");
    if (!modal) return;
    ticketEntry = entry;
    ticketRoundName = data.round_name || "รอบปัจจุบัน";
    modal.classList.remove("is-photo-mode");
    document.getElementById("pb-ticket-expand").textContent = "⛶ ขยายตั๋วให้ถ่ายชัด";
    drawTicket(entry, data);
    ticketMessage("ถ่ายหน้าจอเก็บได้ทันที · ไฟล์ PNG ใช้ส่ง AirDrop ได้");
    modal.classList.remove("pb-hidden");
    document.getElementById("pb-ticket-close").focus();
  }

  function ticketDataUrl() {
    var canvas = document.getElementById("pb-queue-ticket-canvas");
    return canvas.toDataURL("image/png");
  }

  function ticketFilename() {
    return "photobooth-queue-" + (ticketEntry ? ticketEntry.code : "ticket") + ".png";
  }

  function ticketFile(dataUrl) {
    var raw = atob(dataUrl.split(",")[1]);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return new File([bytes], ticketFilename(), { type: "image/png" });
  }

  function renderPanel(data) {
    var active = document.getElementById("pb-gq-active");
    var next = document.getElementById("pb-gq-next");
    var nextStart = document.getElementById("pb-gq-next-start");
    var nextSkip = document.getElementById("pb-gq-next-skip");
    var issueNext = document.getElementById("pb-gq-issue-next");
    var list = document.getElementById("pb-gq-list");
    var create = document.getElementById("pb-gq-create");
    var activeDone = document.getElementById("pb-gq-active-done");
    var activeNext = document.getElementById("pb-gq-active-next");
    if (!list) return;
    if (active) active.textContent = data.active_code || "—";
    var activeTicket = document.getElementById("pb-gq-active-ticket");
    if (activeTicket) activeTicket.hidden = !data.active_code;
    var ipad = document.getElementById("pb-ipad-chooser");
    if (ipad) ipad.hidden = false;
    var prefixSelect = document.getElementById("pb-gq-prefix");
    if (prefixSelect) {
      var categories = data.categories || [{ prefix: "SCI", name: "นักศึกษา" }];
      var catSignature = JSON.stringify(categories);
      if (catSignature !== renderedCategoriesSignature) {
        renderedCategoriesSignature = catSignature;
        var selected = prefixSelect.value || "SCI";
        prefixSelect.replaceChildren();
        categories.forEach(function (category) {
          var option = document.createElement("option");
          option.value = category.prefix;
          option.textContent = category.prefix + " · " + category.name;
          prefixSelect.appendChild(option);
        });
        prefixSelect.value = categories.some(function (item) { return item.prefix === selected; }) ? selected : "SCI";
      }
    }
    var prefix = prefixSelect ? prefixSelect.value : "SCI";
    if (issueNext) issueNext.textContent = prefix + String((data.next_numbers || {})[prefix] || 1).padStart(2, "0");
    var nextWaiting = data.entries.find(function (entry) { return entry.status === "waiting"; });
    if (next) next.textContent = nextWaiting ? nextWaiting.code : "—";
    if (activeDone) {
      activeDone.hidden = !data.active_code;
      activeDone.disabled = busy;
      activeDone.dataset.code = data.active_code || "";
    }
    if (activeNext) {
      activeNext.hidden = !(data.active_code && nextWaiting);
      activeNext.disabled = busy;
      activeNext.textContent = nextWaiting ? "เสร็จ & เรียก " + nextWaiting.code + " ➔" : "เสร็จ & เรียกถัดไป ➔";
    }
    if (nextStart) {
      nextStart.hidden = !nextWaiting || !!data.active_code;
      nextStart.disabled = busy || !!data.active_code;
      nextStart.dataset.code = nextWaiting ? nextWaiting.code : "";
      nextStart.textContent = nextWaiting ? "เรียกเข้าถ่าย " + nextWaiting.code : "เรียกคิว";
    }
    if (nextSkip) {
      var waitingCount = data.entries.filter(function (entry) { return entry.status === "waiting"; }).length;
      nextSkip.hidden = !nextWaiting;
      nextSkip.disabled = busy || waitingCount < 2;
      nextSkip.title = waitingCount < 2 ? "ต้องมีอย่างน้อย 2 คิวรอเรียก" : "ย้ายคิวนี้ไปท้ายแถว โดยยังใช้หมายเลขเดิม";
      nextSkip.dataset.code = nextWaiting ? nextWaiting.code : "";
      nextSkip.textContent = nextWaiting ? "ข้าม " + nextWaiting.code + " ↴" : "ข้ามคิว ↴";
    }
    if (create) create.disabled = busy;
    var entries = data.entries.filter(function (entry) { return entry.status === "waiting"; }).slice(0, 12);
    var activeEntry = data.entries.find(function (entry) { return entry.code === data.active_code; });
    if (activeEntry && activeEntry.status === "shooting") entries.unshift(activeEntry);
    var signature = JSON.stringify([entries, data.active_code, data.call_event && data.call_event.code, data.round_name, data.categories, busy]);
    if (signature === renderedListSignature) return;
    renderedListSignature = signature;
    var previousScrollTop = list.scrollTop;
    list.replaceChildren();
    if (!entries.length) {
      var empty = document.createElement("div");
      empty.className = "pb-gq-empty";
      empty.textContent = "ยังไม่มีคิวรอเรียก";
      list.appendChild(empty);
    }
    entries.forEach(function (entry) {
      var row = document.createElement("div");
      row.className = "pb-gq-row";
      row.dataset.status = entry.status;
      var label = document.createElement("span");
      var strong = document.createElement("strong");
      strong.textContent = entry.code;
      var small = document.createElement("small");
      small.textContent = statusLabel(entry.status) + " • " + (entry.people_count || 1) + " คน";
      label.append(strong, small);
      row.appendChild(label);
      var actions = document.createElement("div");
      actions.className = "pb-gq-actions";
      var ticket = makeButton("ตั๋วคิว", function () { openTicket(entry, data); }, false);
      ticket.className = "pb-gq-ticket";
      actions.appendChild(ticket);
      if (entry.status === "waiting" || entry.status === "shooting") {
        var called = data.call_event && data.call_event.code === entry.code;
        var call = makeButton(called ? "♫ เรียกซ้ำ" : "♫ เรียกเสียง", function () { callQueue(entry.code); }, busy);
        call.className = "pb-gq-call";
        actions.appendChild(call);
      }
      if (entry.status === "shooting") {
        var doneBtn = makeButton("✓ เสร็จคิว", function () { completeQueue(entry.code); }, busy);
        doneBtn.className = "pb-gq-complete";
        actions.appendChild(doneBtn);
      } else if (entry.status === "waiting") {
        actions.appendChild(makeButton("เรียกเข้าถ่าย", function () { startQueue(entry.code); }, busy || !!data.active_code));
      }
      if (entry.status === "waiting" || entry.status === "shooting") {
        var cancel = makeButton("ยกเลิก", function () { cancelQueue(entry.code); }, busy);
        cancel.className = "pb-gq-cancel";
        actions.appendChild(cancel);
      }
      row.appendChild(actions);
      list.appendChild(row);
    });
    list.scrollTop = previousScrollTop;
  }

  function renderGuest(_data) {
    var badge = document.getElementById("pb-guest-active-code");
    if (!badge) return;
    badge.hidden = true;
    badge.textContent = "";
  }

  function render(data) {
    latest = data;
    if (ticketEntry && !document.getElementById("pb-queue-ticket-modal").classList.contains("pb-hidden")) {
      var currentTicket = (data.entries || []).find(function (entry) { return entry.code === ticketEntry.code && entry.round_id === ticketEntry.round_id; });
      if (!currentTicket || currentTicket.status === "canceled") {
        closeTicket();
        showMessage("ตั๋วคิวนี้ถูกยกเลิกหรือสิ้นสุดรอบงานแล้ว");
      }
    }
    if (typeof window.pbUpdateOperatorStatus === "function") window.pbUpdateOperatorStatus({ queue: data });
    mediaToCode = new Map();
    Object.entries(data.media_queues || {}).forEach(function (pair) {
      mediaToCode.set(pair[0], pair[1]);
    });
    renderGuest(data);
    if (operator) renderPanel(data);
    window.pbDecorateGuestQueueGallery();
    if (window.currentGalleryItemId) window.pbShowGalleryQueueCode(window.currentGalleryItemId);
  }

  async function refresh() {
    if (polling) return;
    polling = true;
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 6000);
    try {
      var response = await fetch("/api/share/guest-queue", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("โหลดข้อมูลคิวไม่สำเร็จ");
      render(await response.json());
    } catch (error) {
      if (operator) {
        showMessage(error.name === "AbortError" ? "โหลดคิวช้า กำลังลองใหม่" : "โหลดข้อมูลคิวไม่สำเร็จ กำลังลองใหม่");
        if (typeof window.pbUpdateOperatorStatus === "function") window.pbUpdateOperatorStatus({ queueError: true });
      }
    } finally {
      clearTimeout(timeout);
      polling = false;
    }
  }

  async function startQueue(code) {
    if (busy) return;
    busy = true;
    showMessage("กำลังเรียกคิว " + code + " ขึ้นจอ… กรุณารอสักครู่");
    var warning = setTimeout(function () { showMessage("ยังรอคำตอบจากเว็บ · กรุณาอย่ากดซ้ำ"); }, 7000);
    try {
      await post("/api/share/guest-queue/" + encodeURIComponent(code) + "/start");
      showMessage("เรียกคิว " + code + " ขึ้นจอแล้ว");
      await refresh();
    } catch (error) { showMessage(error.message); }
    finally { clearTimeout(warning); busy = false; if (latest) renderPanel(latest); }
  }

  async function completeQueue(code) {
    if (busy || !code) return;
    busy = true;
    showMessage("กำลังจบคิว " + code + "…");
    try {
      await post("/api/share/guest-queue/" + encodeURIComponent(code) + "/complete");
      showMessage("เสร็จสิ้นคิว " + code + " แล้ว");
      await refresh();
    } catch (error) { showMessage(error.message); }
    finally { busy = false; if (latest) renderPanel(latest); }
  }

  async function advanceQueue() {
    if (busy) return;
    busy = true;
    showMessage("กำลังจบคิวและเรียกคิวถัดไป…");
    try {
      var res = await post("/api/share/guest-queue/advance");
      if (res.completed_code && res.active_code) showMessage("เสร็จคิว " + res.completed_code + " · เรียกคิว " + res.active_code + " แล้ว");
      else if (res.active_code) showMessage("เรียกคิว " + res.active_code + " ขึ้นจอแล้ว");
      else if (res.completed_code) showMessage("เสร็จคิว " + res.completed_code + " แล้ว · ไม่มีคิวรอถัดไป");
      await refresh();
    } catch (error) { showMessage(error.message); }
    finally { busy = false; if (latest) renderPanel(latest); }
  }

  async function callQueue(code) {
    if (busy) return;
    var previousCall = lastCallAtByCode[code] || 0;
    try { previousCall = Math.max(previousCall, Number(window.localStorage.getItem("pb-last-queue-call-" + code)) || 0); } catch (_error) {}
    var secondsLeft = Math.ceil((callCooldownMs - (Date.now() - previousCall)) / 1000);
    if (secondsLeft > 0) {
      showMessage("เพิ่งเรียกคิว " + code + " แล้ว · รออีก " + secondsLeft + " วินาทีก่อนเรียกซ้ำ");
      return;
    }
    busy = true;
    try {
      await post("/api/share/guest-queue/" + encodeURIComponent(code) + "/call");
      lastCallAtByCode[code] = Date.now();
      try { window.localStorage.setItem("pb-last-queue-call-" + code, String(lastCallAtByCode[code])); } catch (_error) {}
      showMessage("ส่งคำสั่งเรียกคิว " + code + " แล้ว — ตรวจว่าจอคิวเปิดเสียงอยู่");
      await refresh();
    } catch (error) { showMessage(error.message); }
    finally { busy = false; if (latest) renderPanel(latest); }
  }

  async function skipQueue(code) {
    if (busy || !window.confirm("ข้ามคิว " + code + " ไปท้ายแถวใช่หรือไม่? หมายเลขคิวจะยังใช้ได้")) return;
    busy = true;
    showMessage("กำลังย้ายคิว " + code + " ไปท้ายแถว…");
    try {
      await post("/api/share/guest-queue/" + encodeURIComponent(code) + "/skip");
      await refresh();
      showMessage("ข้ามคิว " + code + " แล้ว · คิวนี้อยู่ท้ายแถวและเรียกได้ภายหลัง");
    } catch (error) { showMessage(error.message); }
    finally { busy = false; if (latest) renderPanel(latest); }
  }

  async function cancelQueue(code) {
    if (busy || !window.confirm("ยกเลิกคิว " + code + " ใช่หรือไม่? เลขนี้จะไม่ถูกนำกลับมาใช้ซ้ำ")) return;
    busy = true;
    try {
      await request("/api/share/guest-queue/" + encodeURIComponent(code), "DELETE");
      showMessage("ยกเลิกคิว " + code + " แล้ว");
      await refresh();
    } catch (error) { showMessage(error.message); }
    finally { busy = false; if (latest) renderPanel(latest); }
  }

  window.pbDecorateGuestQueueGallery = function () {
    document.querySelectorAll(".pb-gallery-card[data-media-id]").forEach(function (card) {
      var code = mediaToCode.get(card.dataset.mediaId);
      var badge = card.querySelector(".pb-gallery-guest-code");
      if (!code) {
        if (badge) badge.remove();
        return;
      }
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "pb-gallery-guest-code";
        card.querySelector(".pb-gallery-card-thumb").appendChild(badge);
      }
      badge.classList.remove("pb-gallery-guest-code-unknown");
      badge.textContent = "คิว " + code.code;
    });
  };

  window.pbShowGalleryQueueCode = function (id) {
    var badge = document.getElementById("pb-gallery-queue-code");
    if (!badge) return;
    var code = mediaToCode.get(id);
    if (!code) {
      badge.hidden = true;
      badge.textContent = "";
      badge.classList.remove("pb-gallery-queue-code-unknown");
      return;
    }
    badge.hidden = false;
    badge.classList.remove("pb-gallery-queue-code-unknown");
    badge.textContent = "คิวผู้ถ่าย " + code.code + " · " + code.people_count + " คน · " + code.round_name;
  };

  var create = document.getElementById("pb-gq-create");
  if (operator && create) create.addEventListener("click", async function () {
    if (busy) return;
    busy = true;
    create.disabled = true;
    showMessage("กำลังออกบัตรคิว… กรุณารอสักครู่");
    var warning = setTimeout(function () { showMessage("ยังรอคำตอบจากเว็บ · กรุณาอย่ากดออกบัตรซ้ำ"); }, 7000);
    try {
      var peopleCount = Number(document.getElementById("pb-gq-people").value);
      if (!Number.isInteger(peopleCount) || peopleCount < 1 || peopleCount > 999) throw new Error("กรุณาระบุจำนวนคน 1–999");
      var entry = await request("/api/share/guest-queue", "POST", { prefix: document.getElementById("pb-gq-prefix").value, people_count: peopleCount }, { "Idempotency-Key": makeIdempotencyKey("gq-create") });
      showMessage("ออกคิว " + entry.code + " แล้ว");
      await refresh();
      openTicket(entry, latest && latest.round_id === entry.round_id ? latest : { round_name: "รอบปัจจุบัน", categories: [] });
    } catch (error) { showMessage(error.message); }
    finally { clearTimeout(warning); busy = false; create.disabled = false; if (latest) renderPanel(latest); }
  });

  var prefixSelect = document.getElementById("pb-gq-prefix");
  if (prefixSelect) prefixSelect.addEventListener("change", function () { if (latest) renderPanel(latest); });

  var activeTicket = document.getElementById("pb-gq-active-ticket");
  if (operator && activeTicket) activeTicket.addEventListener("click", function () {
    if (!latest || !latest.active_code) return;
    var entry = (latest.entries || []).find(function (item) { return item.code === latest.active_code; });
    if (entry) openTicket(entry, latest);
  });

  var activeDone = document.getElementById("pb-gq-active-done");
  if (operator && activeDone) activeDone.addEventListener("click", function () {
    if (!latest || !latest.active_code) return;
    completeQueue(latest.active_code);
  });

  var activeNext = document.getElementById("pb-gq-active-next");
  if (operator && activeNext) activeNext.addEventListener("click", function () {
    if (!latest || !latest.active_code) return;
    advanceQueue();
  });

  var nextStart = document.getElementById("pb-gq-next-start");
  if (operator && nextStart) nextStart.addEventListener("click", function () {
    if (!latest || latest.active_code) return;
    var nextWaiting = (latest.entries || []).find(function (entry) { return entry.status === "waiting"; });
    if (nextWaiting && nextWaiting.code === nextStart.dataset.code) startQueue(nextWaiting.code);
  });
  var nextSkip = document.getElementById("pb-gq-next-skip");
  if (operator && nextSkip) nextSkip.addEventListener("click", function () {
    if (!latest) return;
    var nextWaiting = (latest.entries || []).find(function (entry) { return entry.status === "waiting"; });
    if (nextWaiting && nextWaiting.code === nextSkip.dataset.code) skipQueue(nextWaiting.code);
  });

  var ticketModal = document.getElementById("pb-queue-ticket-modal");
  if (operator && ticketModal) {
    ticketModal.querySelector("[data-pb-ticket-close]").addEventListener("click", closeTicket);
    document.getElementById("pb-ticket-close").addEventListener("click", closeTicket);
    document.getElementById("pb-ticket-expand").addEventListener("click", function () {
      var enlarged = ticketModal.classList.toggle("is-photo-mode");
      this.textContent = enlarged ? "ย่อกลับ" : "⛶ ขยายตั๋วให้ถ่ายชัด";
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || ticketModal.classList.contains("pb-hidden")) return;
      if (ticketModal.classList.contains("is-photo-mode")) document.getElementById("pb-ticket-expand").click();
      else closeTicket();
    });
    document.getElementById("pb-ticket-download").addEventListener("click", function () {
      if (!ticketEntry) return;
      var link = document.createElement("a");
      link.href = ticketDataUrl();
      link.download = ticketFilename();
      document.body.appendChild(link);
      link.click();
      link.remove();
      ticketMessage("บันทึกไฟล์ PNG แล้ว");
    });
    document.getElementById("pb-ticket-airdrop").addEventListener("click", async function () {
      if (!ticketEntry) return;
      var button = this;
      button.disabled = true;
      try {
        var dataUrl = ticketDataUrl();
        var file = ticketFile(dataUrl);
        if (navigator.canShare && navigator.canShare({ files: [file] })) {
          try {
            await navigator.share({ files: [file], title: "บัตรคิว " + ticketEntry.code });
            ticketMessage("เปิดเมนูแชร์แล้ว");
            return;
          } catch (error) {
            if (error.name === "AbortError") return;
          }
        }
        await request("/api/share/guest-queue/" + encodeURIComponent(ticketEntry.code) + "/ticket-airdrop", "POST", { image_data: dataUrl });
        ticketMessage("เปิด AirDrop และแสดงไฟล์ใน Finder บน Mac แล้ว · เลือกผู้รับเพื่อส่ง");
      } catch (error) { ticketMessage(error.message + " · กดบันทึก PNG แล้วส่งจาก Finder ได้"); }
      finally { button.disabled = false; }
    });
  }

  if (operator) {
    refresh();
    setInterval(function () { if (!document.hidden) refresh(); }, 3000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
  } else {
    renderGuest(null);
  }
})();
