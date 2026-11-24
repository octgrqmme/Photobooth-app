(function () {
  "use strict";

  var app = {
    templates: [],
    hiddenBuiltinTemplates: [],
    activeTemplateId: "theater",
    chooserUrl: "",
    chooserQrUrl: "",
    printers: [],
    printerProfiles: [],
    activePrinterProfileId: null,
    paperSizes: [],
    studio: {
      collageId: null,
      sources: [],
      mode: "captured",
      objectUrls: [],
      cropIndex: 0,
      templateManuallySelected: false,
      templateId: "theater",
      title: "THE LAST TAKE • SCI-LYWOOD",
      subtitle: "เทคสุดท้าย ก่อนบายเธอ • 27 กันยายน 2569",
      result: null
    },
    editor: {
      id: null,
      name: "เทมเพลตใหม่",
      canvasWidth: 1200,
      canvasHeight: 1800,
      printLayout: "full",
      slots: [],
      rightSlots: [],
      selectedSlot: 0,
      selectedSide: "left",
      backgroundData: null,
      backgroundUrl: null,
      backgroundRemoved: false,
      overlayData: null,
      overlayUrl: null,
      overlayRemoved: false,
      dragging: null
    },
    imageCache: {}
  };

  function toast(message, duration) {
    if (typeof window.pbShowToast === "function") window.pbShowToast(message, duration || 3200);
  }

  async function api(url, options) {
    var response = await fetch(url, options);
    if (!response.ok) {
      var message = "คำขอไม่สำเร็จ (HTTP " + response.status + ")";
      try {
        var body = await response.clone().json();
        message = body.detail || body.error || message;
      } catch (_error) {
        try { message = (await response.clone().text()) || message; } catch (_ignored) {}
      }
      var error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
    });
  }

  function safeId(value) {
    return String(value == null ? "" : value).replace(/[^a-zA-Z0-9_-]/g, "");
  }

  function loadImage(url) {
    if (!url) return Promise.resolve(null);
    var skipCache = url.indexOf("data:") === 0 || url.indexOf("blob:") === 0;
    if (!skipCache && app.imageCache[url]) return app.imageCache[url];
    var promise = new Promise(function (resolve, reject) {
      var image = new Image();
      image.crossOrigin = "anonymous";
      var timeout = setTimeout(function () { finish(new Error("โหลดภาพนานเกินไป กรุณาลองเปิดหน้าจัดเทมเพลตอีกครั้ง")); }, 10000);
      function finish(error) {
        clearTimeout(timeout);
        image.onload = null;
        image.onerror = null;
        if (error) {
          if (!skipCache) delete app.imageCache[url];
          reject(error);
        } else {
          resolve(image);
        }
      }
      image.onload = function () { finish(null); };
      image.onerror = function () { finish(new Error("โหลดภาพไม่สำเร็จ")); };
      image.src = url;
    });
    if (!skipCache) app.imageCache[url] = promise;
    return promise;
  }

  function roundedRect(context, x, y, width, height, radius) {
    context.beginPath();
    context.moveTo(x + radius, y);
    context.arcTo(x + width, y, x + width, y + height, radius);
    context.arcTo(x + width, y + height, x, y + height, radius);
    context.arcTo(x, y + height, x, y, radius);
    context.arcTo(x, y, x + width, y, radius);
    context.closePath();
  }

  function drawCover(context, image, slot, border, crop) {
    crop = crop || {};
    var zoom = Math.max(1, Math.min(3, (Number(crop.zoom) || 100) / 100));
    var scale = Math.max(slot.width / image.naturalWidth, slot.height / image.naturalHeight) * zoom;
    var sourceWidth = slot.width / scale;
    var sourceHeight = slot.height / scale;
    var panX = Math.max(-100, Math.min(100, Number(crop.x) || 0));
    var panY = Math.max(-100, Math.min(100, Number(crop.y) || 0));
    var sourceX = (image.naturalWidth - sourceWidth) * (panX + 100) / 200;
    var sourceY = (image.naturalHeight - sourceHeight) * (panY + 100) / 200;
    context.save();
    roundedRect(context, slot.x, slot.y, slot.width, slot.height, 18);
    context.clip();
    context.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight, slot.x, slot.y, slot.width, slot.height);
    context.restore();
    if (border) {
      context.save();
      context.strokeStyle = "rgba(255,224,148,.95)";
      context.lineWidth = 6;
      roundedRect(context, slot.x, slot.y, slot.width, slot.height, 18);
      context.stroke();
      context.restore();
    }
  }

  function templateById(id) {
    return app.templates.find(function (template) { return template.id === id; });
  }

  function applyCaptureCount(count) {
    count = Math.max(1, Math.min(6, parseInt(count, 10) || 3));
    window.pbActiveCaptureCount = count;
    if (typeof window.pbEnsureSlotCount === "function") window.pbEnsureSlotCount(count);
    var badge = document.getElementById("pb-progress-badge");
    var progress = document.getElementById("pb-progress-sub");
    if (!badge || /กดชัตเตอร์เพื่อเริ่ม|กำลังโหลด/.test(badge.textContent || "")) {
      if (progress) progress.textContent = "เทมเพลต " + count + " รูปต่อชุด";
      var bannerText = document.getElementById("pb-banner-text");
      if (bannerText) bannerText.textContent = "สตูดิโอพร้อมแล้ว — กดปุ่มชัตเตอร์ที่กล้องเพื่อเริ่มถ่ายรูปที่ 1 (1/" + count + ")";
    }
  }

  function makeKeyboardClickable(element, label) {
    if (!element || element.dataset.pbKeyboardReady) return;
    element.dataset.pbKeyboardReady = "true";
    element.setAttribute("role", "button");
    element.setAttribute("tabindex", "0");
    if (label && !element.getAttribute("aria-label")) element.setAttribute("aria-label", label);
    element.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        element.click();
      }
    });
  }

  function improveAccessibility(root) {
    var scope = root || document;
    if (scope.matches && scope.matches(".pb-gallery-btn, .pb-print-queue-btn, .pb-slot, .pb-gallery-card")) {
      makeKeyboardClickable(scope, scope.getAttribute("title") || "เปิดรายการ");
    }
    scope.querySelectorAll(".pb-gallery-btn, .pb-print-queue-btn, .pb-slot, .pb-gallery-card").forEach(function (element) {
      makeKeyboardClickable(element, element.getAttribute("title") || "เปิดรายการ");
    });
    scope.querySelectorAll(".pb-modal").forEach(function (element) {
      element.setAttribute("role", "dialog");
      element.setAttribute("aria-modal", "true");
    });
  }

  function translateCameraNotifications(root) {
    var scope = root || document;
    scope.querySelectorAll('.q-notification__message, [class*="notification"]').forEach(function (element) {
      if (element.childElementCount === 0 && /Error while streaming the live preview/i.test(element.textContent || "")) {
        element.textContent = "ไม่พบภาพจากกล้อง Canon ระบบกำลังลองเชื่อมต่อใหม่ — โปรดตรวจสาย USB และสถานะกล้อง";
      }
    });
  }

  function installCameraHealth() {
    var stream = document.getElementById("pb-camera-stream");
    if (!stream) return;
    var panel = document.createElement("div");
    panel.id = "pb-camera-health";
    panel.className = "pb-camera-health";
    panel.setAttribute("role", "status");
    panel.innerHTML = '<div class="pb-camera-health-card"><strong>ยังไม่พบภาพจากกล้อง Canon</strong><p id="pb-camera-health-detail">ตรวจสาย USB, เปิดกล้อง และตั้งโหมดถ่ายภาพ ระบบจะเชื่อมต่อใหม่อัตโนมัติ</p></div>';
    stream.parentNode.appendChild(panel);
    var firstCheckAt = Date.now();
    var cameraCheckInFlight = false;

    async function checkCamera() {
      if (cameraCheckInFlight) return;
      cameraCheckInFlight = true;
      try {
        var health = await api("/api/share/camera-health?ts=" + Date.now());
        var connected = health.connected === true;
        var detected = (health.detected || []).filter(function (camera) { return camera.supported; });
        var detail = document.getElementById("pb-camera-health-detail");
        window.pbCameraConnected = connected;
        if (typeof window.pbUpdateOperatorStatus === "function") window.pbUpdateOperatorStatus({ camera: connected });
        if (connected) {
          document.body.classList.remove("pb-camera-offline");
          panel.classList.remove("is-visible");
          if (!window.pbApprovalImageActive && !stream.getAttribute("src")) stream.src = "/api/aquisition/stream.mjpg?t=" + Date.now();
        } else if (Date.now() - firstCheckAt > 3000) {
          document.body.classList.add("pb-camera-offline");
          panel.classList.add("is-visible");
          if (detail) detail.textContent = detected.length ? "พบ " + detected[0].model + " แต่ backend ยังเริ่มทำงานไม่สำเร็จ ลองปิด–เปิดกล้องใหม่" : "ตรวจสาย USB, เปิดกล้อง Canon และปิด EOS Utility หรือแอปอื่นที่กำลังจับกล้อง";
        }
        if (!connected && stream.getAttribute("src")) stream.removeAttribute("src");
      } catch (_error) {
        window.pbCameraConnected = false;
        if (typeof window.pbUpdateOperatorStatus === "function") window.pbUpdateOperatorStatus({ camera: false });
        if (stream.getAttribute("src")) stream.removeAttribute("src");
        if (Date.now() - firstCheckAt > 3000) panel.classList.add("is-visible");
      } finally {
        cameraCheckInFlight = false;
      }
    }
    checkCamera();
    window.setInterval(function () { if (!document.hidden) checkCamera(); }, 5000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) checkCamera(); });
    checkCamera();
  }

  function injectControls() {
    var sidebar = document.getElementById("photobooth-sidebar");
    var slots = sidebar && sidebar.querySelector(".pb-slots");
    if (sidebar && slots) {
      var chooser = document.createElement("div");
      chooser.className = "pb-capture-template-control";
      chooser.innerHTML = '<label for="pb-active-template-select">กรอบและจำนวนภาพ</label><div><select id="pb-active-template-select" aria-label="เลือกเทมเพลตก่อนถ่าย"></select><button type="button" onclick="pbOpenHardware()" title="จัดการอุปกรณ์และเทมเพลต" aria-label="จัดการอุปกรณ์และเทมเพลต">⚙</button></div>';
      sidebar.insertBefore(chooser, slots);
      document.getElementById("pb-active-template-select").addEventListener("change", function (event) {
        activateTemplate(event.target.value);
      });
    }
    var hardwareButton = document.createElement("button");
    hardwareButton.id = "pb-hardware-btn";
    hardwareButton.type = "button";
    hardwareButton.className = "pb-hardware-btn";
    hardwareButton.setAttribute("aria-label", "ตั้งค่ากล้อง เทมเพลต และเครื่องพิมพ์");
    hardwareButton.innerHTML = "⚙ <span>ตั้งค่าอุปกรณ์</span>";
    hardwareButton.onclick = window.pbOpenHardware;
    document.body.appendChild(hardwareButton);
  }

  function createHardwareModal() {
    var modal = document.createElement("div");
    modal.id = "pb-hardware-modal";
    modal.className = "pb-modal pb-hidden";
    modal.setAttribute("aria-labelledby", "pb-hardware-heading");
    modal.innerHTML = '<div class="pb-modal-overlay" onclick="pbCloseHardware()"></div><div class="pb-modal-box pb-hardware-box">' +
      '<div class="pb-modal-header"><h3 id="pb-hardware-heading">กล้อง • เทมเพลต • เครื่องพิมพ์</h3><button class="pb-modal-close" type="button" onclick="pbCloseHardware()" aria-label="ปิด">×</button></div>' +
      '<div class="pb-hardware-tabs"><button type="button" data-panel="camera">กล้อง Canon</button><button type="button" data-panel="templates">เทมเพลต</button><button type="button" data-panel="printers">เครื่องพิมพ์</button><button type="button" data-panel="preflight">ตรวจความพร้อม</button></div>' +
      '<div class="pb-hardware-content"><section id="pb-hardware-camera"></section><section id="pb-hardware-templates" hidden></section><section id="pb-hardware-printers" hidden></section><section id="pb-hardware-preflight" hidden></section></div></div>';
    document.body.appendChild(modal);
    modal.querySelectorAll(".pb-hardware-tabs button").forEach(function (button, index) {
      if (index === 0) button.classList.add("is-active");
      button.addEventListener("click", function () {
        modal.querySelectorAll(".pb-hardware-tabs button").forEach(function (item) { item.classList.toggle("is-active", item === button); });
        ["camera", "templates", "printers", "preflight"].forEach(function (name) {
          document.getElementById("pb-hardware-" + name).hidden = name !== button.dataset.panel;
        });
      });
    });
  }

  async function loadTemplates() {
    var data = await api("/api/share/templates");
    app.templates = data.templates || [];
    app.hiddenBuiltinTemplates = data.hidden_builtin_templates || [];
    app.activeTemplateId = data.active_template_id || (app.templates[0] && app.templates[0].id);
    app.chooserUrl = data.chooser_url || "";
    app.chooserQrUrl = data.chooser_qr_url || "";
    var active = templateById(app.activeTemplateId);
    if (active) {
      applyCaptureCount(active.capture_count);
    }
    renderTemplateChooser();
    renderTemplateManager();
  }

  function renderTemplateChooser() {
    var select = document.getElementById("pb-active-template-select");
    if (!select) return;
    select.innerHTML = app.templates.map(function (template) {
      return '<option value="' + safeId(template.id) + '" ' + (template.id === app.activeTemplateId ? "selected" : "") + '>' + escapeHtml(template.name) + " • " + template.capture_count + " รูป</option>";
    }).join("");
  }

  async function activateTemplate(templateId) {
    try {
      var result = await api("/api/share/templates/" + encodeURIComponent(templateId) + "/activate", { method: "POST" });
      app.activeTemplateId = result.active_template_id;
      applyCaptureCount(result.capture_count);
      renderTemplateChooser();
      renderTemplateManager();
      toast("เลือกเทมเพลตแล้ว — ระบบจะถ่าย " + result.capture_count + " รูป");
      return result;
    } catch (error) {
      renderTemplateChooser();
      toast(error.message, 4200);
      return null;
    }
  }

  function templateThumb(template) {
    if (template.background_url) return '<img src="' + escapeHtml(template.background_url) + '" alt="">';
    return '<div class="pb-template-empty-thumb"><strong>' + template.capture_count + '</strong><span>ช่องภาพ</span></div>';
  }

  function renderTemplateManager() {
    var section = document.getElementById("pb-hardware-templates");
    if (!section) return;
    section.innerHTML = '<div class="pb-section-heading"><div><strong>เทมเพลตถ่ายภาพ</strong><p>ปรับชื่อ ขนาด ตำแหน่งช่อง และภาพกรอบได้ · กรอบมาตรฐานให้ทำสำเนาก่อนแก้</p></div><button type="button" onclick="pbNewTemplate()">+ สร้างเทมเพลต</button></div>' +
      '<div class="pb-chooser-access"><img src="' + escapeHtml(app.chooserQrUrl) + '" alt="QR สำหรับเปิดหน้าเลือกเทมเพลตบน iPad"><div><strong>ให้ผู้ถ่ายเลือกกรอบบน iPad</strong><p>เจ้าหน้าที่กดเริ่มถ่ายคิวก่อน แล้วใช้ iPad ที่ต่อ Wi-Fi เดียวกับ Mac สแกน QR นี้และเลือกกรอบก่อนกดชัตเตอร์แรก</p><a href="' + escapeHtml(app.chooserUrl) + '" target="_blank" rel="noopener">' + escapeHtml(app.chooserUrl) + '</a></div></div>' +
      '<div class="pb-manage-grid">' +
      app.templates.map(function (template) {
        var id = safeId(template.id);
        return '<article class="pb-manage-card ' + (template.id === app.activeTemplateId ? "is-active" : "") + '">' + templateThumb(template) + '<div><strong>' + escapeHtml(template.name) + '</strong><span>' + template.capture_count + ' ช่องภาพ' + (template.id === app.activeTemplateId ? ' · กำลังใช้' : '') + '</span></div><div class="pb-card-actions"><button type="button" onclick="pbActivateTemplate(\'' + id + '\')">เลือกใช้</button><button type="button" onclick="pbEditTemplate(\'' + id + '\')">' + (template.builtin ? "ทำสำเนาเพื่อแก้" : "แก้ไข") + '</button><button class="danger" type="button" onclick="pbDeleteTemplate(\'' + id + '\')">' + (template.builtin ? "ซ่อน" : "ลบ") + '</button></div></article>';
      }).join("") + '</div>' +
      (app.hiddenBuiltinTemplates.length ? '<details class="pb-hidden-templates"><summary>กรอบมาตรฐานที่ซ่อนไว้ (' + app.hiddenBuiltinTemplates.length + ')</summary><div class="pb-manage-grid">' + app.hiddenBuiltinTemplates.map(function (template) { var id = safeId(template.id); return '<article class="pb-manage-card">' + templateThumb(template) + '<div><strong>' + escapeHtml(template.name) + '</strong><span>ซ่อนจากตัวเลือก</span></div><div class="pb-card-actions"><button type="button" onclick="pbRestoreTemplate(\'' + id + '\')">นำกลับมาใช้</button></div></article>'; }).join("") + '</div></details>' : '');
  }

  window.pbActivateTemplate = activateTemplate;

  window.pbDeleteTemplate = async function (id) {
    var template = templateById(id);
    if (!template) return;
    if (!confirm(template.builtin ? "ซ่อนกรอบมาตรฐานนี้จากตัวเลือก? สามารถนำกลับมาได้ภายหลัง" : "ลบเทมเพลตนี้และไฟล์กรอบที่อัปโหลดไว้? ภาพถ่ายที่สร้างแล้วจะไม่ถูกลบ")) return;
    try {
      await api("/api/share/templates/" + encodeURIComponent(id), { method: "DELETE" });
      await loadTemplates();
      toast(template.builtin ? "ซ่อนกรอบมาตรฐานแล้ว" : "ลบเทมเพลตแล้ว");
    } catch (error) { toast(error.message, 4200); }
  };

  window.pbRestoreTemplate = async function (id) {
    try {
      await api("/api/share/templates/" + encodeURIComponent(id) + "/restore", { method: "POST" });
      await loadTemplates();
      toast("นำกรอบมาตรฐานกลับมาแล้ว");
    } catch (error) { toast(error.message, 4200); }
  };

  function defaultSlots(count, width, height) {
    var twoUp = app.editor.printLayout === "two_up";
    var effectiveWidth = twoUp ? width / 2 : width;
    var columns = twoUp || count === 1 ? 1 : 2;
    var rows = Math.ceil(count / columns);
    var gap = twoUp ? 32 : 50;
    var marginX = twoUp ? 35 : 60;
    var marginY = 90;
    var slotWidth = Math.floor((effectiveWidth - marginX * 2 - gap * (columns - 1)) / columns);
    var slotHeight = Math.floor((height - marginY * 2 - gap * (rows - 1) - 160) / rows);
    return Array.from({ length: count }, function (_value, index) {
      return { x: marginX + (index % columns) * (slotWidth + gap), y: marginY + Math.floor(index / columns) * (slotHeight + gap), width: slotWidth, height: slotHeight };
    });
  }

  function mirroredSlots(slots, width) {
    return slots.map(function (slot) { return Object.assign({}, slot, { x: slot.x + width / 2 }); });
  }

  function selectedEditorSlots() {
    return app.editor.printLayout === "two_up" && app.editor.selectedSide === "right" ? app.editor.rightSlots : app.editor.slots;
  }

  function createTemplateEditor() {
    var modal = document.createElement("div");
    modal.id = "pb-template-editor-modal";
    modal.className = "pb-modal pb-hidden";
    modal.innerHTML = '<div class="pb-modal-overlay" onclick="pbCloseTemplateEditor()"></div><div class="pb-modal-box pb-editor-box">' +
      '<div class="pb-modal-header"><h3>ตัวแก้ไขเทมเพลต</h3><button class="pb-modal-close" type="button" onclick="pbCloseTemplateEditor()" aria-label="ปิด">×</button></div>' +
      '<div class="pb-editor-layout"><div class="pb-editor-controls">' +
      '<label>รูปแบบกระดาษและไฟล์ดาวน์โหลด<select id="pb-editor-layout"><option value="full">ภาพชุดเดียว — ดาวน์โหลดเต็มภาพ</option><option value="two_up">สองชุดซ้าย–ขวา — ดาวน์โหลดครึ่งซ้าย 2×6</option></select></label>' +
      '<label>ขนาดกระดาษ<select id="pb-editor-paper"><option value="portrait">4×6 แนวตั้ง (1200×1800)</option><option value="landscape">4×6 แนวนอน (1800×1200)</option><option value="custom">กำหนดขนาดเอง</option></select></label>' +
      '<label>ชื่อเทมเพลต<input id="pb-editor-name" maxlength="80"></label><div class="pb-inline-fields"><label>กว้าง<input id="pb-editor-width" type="number" min="600" max="2400"></label><label>สูง<input id="pb-editor-height" type="number" min="900" max="3600"></label></div>' +
      '<label>จำนวนช่องภาพ<select id="pb-editor-count">' + [1,2,3,4,5,6].map(function (n) { return '<option value="' + n + '">' + n + ' ภาพ</option>'; }).join("") + '</select></label>' +
      '<label>ภาพพื้นหลัง PNG/JPG<input id="pb-editor-background" type="file" accept="image/png,image/jpeg"></label><button class="pb-clear-asset" type="button" onclick="pbClearTemplateAsset(\'background\')">นำภาพพื้นหลังออก</button><label>ภาพซ้อนหน้า PNG โปร่งใส<input id="pb-editor-overlay" type="file" accept="image/png,image/jpeg"></label><button class="pb-clear-asset" type="button" onclick="pbClearTemplateAsset(\'overlay\')">นำภาพซ้อนหน้าออก</button>' +
      '<div class="pb-slot-inspector"><strong>ช่องที่ <span id="pb-editor-slot-number">1</span> <span id="pb-editor-slot-side"></span></strong><div id="pb-editor-side-switch" class="pb-editor-side-switch" hidden><button type="button" data-side="left" onclick="pbSelectTemplateSide(\'left\')">ฝั่งซ้าย</button><button type="button" data-side="right" onclick="pbSelectTemplateSide(\'right\')">ฝั่งขวา</button></div><div class="pb-inline-fields"><label>X<input id="pb-slot-x" type="number" min="0"></label><label>Y<input id="pb-slot-y" type="number" min="0"></label></div><div class="pb-inline-fields"><label>กว้าง<input id="pb-slot-width" type="number" min="40"></label><label>สูง<input id="pb-slot-height" type="number" min="40"></label></div></div>' +
      '<p class="pb-editor-hint">คลิกหรือลากช่องภาพแต่ละฝั่งบนตัวอย่างเพื่อจัดตำแหน่งแยกกัน · X วัดจากขอบซ้ายของกระดาษ 4×6 ทั้งแผ่น</p><div class="pb-template-actions"><button type="button" class="pb-btn pb-btn-gallery" onclick="pbCloseTemplateEditor()">ยกเลิก</button><button id="pb-editor-save" type="button" class="pb-btn pb-btn-keep" onclick="pbSaveTemplateDefinition()">บันทึกเทมเพลต</button></div></div>' +
      '<div class="pb-template-preview"><canvas id="pb-editor-canvas" width="1200" height="1800" aria-label="ตัวอย่างและพื้นที่จัดช่องภาพ"></canvas></div></div></div>';
    document.body.appendChild(modal);

    ["name", "width", "height"].forEach(function (field) {
      document.getElementById("pb-editor-" + field).addEventListener("input", updateEditorFromFields);
    });
    document.getElementById("pb-editor-count").addEventListener("change", function (event) {
      var count = parseInt(event.target.value, 10);
      app.editor.slots = defaultSlots(count, app.editor.canvasWidth, app.editor.canvasHeight);
      app.editor.rightSlots = app.editor.printLayout === "two_up" ? mirroredSlots(app.editor.slots, app.editor.canvasWidth) : [];
      app.editor.selectedSlot = 0;
      updateSlotFields();
      renderEditor();
    });
    document.getElementById("pb-editor-layout").addEventListener("change", function (event) {
      app.editor.printLayout = event.target.value;
      if (app.editor.printLayout === "two_up") {
        app.editor.canvasWidth = 1200;
        app.editor.canvasHeight = 1800;
        document.getElementById("pb-editor-width").value = 1200;
        document.getElementById("pb-editor-height").value = 1800;
        document.getElementById("pb-editor-paper").value = "portrait";
        if (app.editor.slots.length > 3) {
          app.editor.slots = app.editor.slots.slice(0, 3);
          document.getElementById("pb-editor-count").value = 3;
        }
      }
      app.editor.slots = defaultSlots(app.editor.slots.length, app.editor.canvasWidth, app.editor.canvasHeight);
      app.editor.rightSlots = app.editor.printLayout === "two_up" ? mirroredSlots(app.editor.slots, app.editor.canvasWidth) : [];
      app.editor.selectedSlot = 0;
      app.editor.selectedSide = "left";
      document.getElementById("pb-editor-canvas").width = app.editor.canvasWidth;
      document.getElementById("pb-editor-canvas").height = app.editor.canvasHeight;
      syncEditorLayoutControls();
      updateSlotFields();
      renderEditor();
    });
    document.getElementById("pb-editor-paper").addEventListener("change", function (event) {
      if (event.target.value === "custom") return;
      if (app.editor.printLayout === "two_up" && event.target.value === "landscape") {
        event.target.value = "portrait";
        toast("สตริปคู่ใช้กระดาษ 4×6 แนวตั้งเท่านั้น");
        return;
      }
      app.editor.canvasWidth = event.target.value === "landscape" ? 1800 : 1200;
      app.editor.canvasHeight = event.target.value === "landscape" ? 1200 : 1800;
      document.getElementById("pb-editor-width").value = app.editor.canvasWidth;
      document.getElementById("pb-editor-height").value = app.editor.canvasHeight;
      document.getElementById("pb-editor-canvas").width = app.editor.canvasWidth;
      document.getElementById("pb-editor-canvas").height = app.editor.canvasHeight;
      app.editor.slots = defaultSlots(app.editor.slots.length, app.editor.canvasWidth, app.editor.canvasHeight);
      app.editor.rightSlots = [];
      updateSlotFields();
      renderEditor();
    });
    ["x", "y", "width", "height"].forEach(function (field) {
      document.getElementById("pb-slot-" + field).addEventListener("input", updateSlotFromFields);
    });
    document.getElementById("pb-editor-background").addEventListener("change", function (event) { readEditorFile(event, "background"); });
    document.getElementById("pb-editor-overlay").addEventListener("change", function (event) { readEditorFile(event, "overlay"); });
    var canvas = document.getElementById("pb-editor-canvas");
    canvas.addEventListener("pointerdown", editorPointerDown);
    canvas.addEventListener("pointermove", editorPointerMove);
    canvas.addEventListener("pointerup", function () { app.editor.dragging = null; });
    canvas.addEventListener("pointercancel", function () { app.editor.dragging = null; });
    canvas.addEventListener("lostpointercapture", function () { app.editor.dragging = null; });
    window.addEventListener("pointerup", function () { app.editor.dragging = null; });
  }

  async function imageUrlToData(url) {
    if (!url) return null;
    var response = await fetch(url);
    if (!response.ok) throw new Error("โหลดภาพไม่สำเร็จ (HTTP " + response.status + ")");
    var blob = await response.blob();
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  window.pbNewTemplate = function () {
    app.editor = { id: null, name: "เทมเพลตใหม่", canvasWidth: 1200, canvasHeight: 1800, printLayout: "full", slots: [], rightSlots: [], selectedSlot: 0, selectedSide: "left", backgroundData: null, backgroundUrl: null, backgroundRemoved: false, overlayData: null, overlayUrl: null, overlayRemoved: false, dragging: null };
    app.editor.slots = defaultSlots(3, 1200, 1800);
    openTemplateEditor();
  };

  window.pbEditTemplate = async function (id) {
    var template = templateById(id);
    if (!template) return;
    try {
      var backgroundData = template.builtin ? await imageUrlToData(template.background_url) : null;
      var overlayData = template.builtin ? await imageUrlToData(template.overlay_url) : null;
      app.editor = {
        id: template.builtin ? null : template.id,
        name: template.builtin ? template.name + " (สำเนา)" : template.name,
        canvasWidth: template.canvas_width,
        canvasHeight: template.canvas_height,
        printLayout: template.print_layout || "full",
        slots: template.slots.map(function (slot) { return Object.assign({}, slot); }),
        rightSlots: template.print_layout === "two_up" ? (template.right_slots || mirroredSlots(template.slots, template.canvas_width)).map(function (slot) { return Object.assign({}, slot); }) : [],
        selectedSlot: 0,
        selectedSide: "left",
        backgroundData: backgroundData,
        backgroundUrl: template.background_url,
        backgroundRemoved: false,
        overlayData: overlayData,
        overlayUrl: template.overlay_url,
        overlayRemoved: false,
        dragging: null
      };
      openTemplateEditor();
    } catch (error) {
      toast((error && error.message) || "โหลดภาพเทมเพลตไม่สำเร็จ", 4200);
    }
  };

  function openTemplateEditor() {
    document.getElementById("pb-template-editor-modal").classList.remove("pb-hidden");
    document.getElementById("pb-editor-name").value = app.editor.name;
    document.getElementById("pb-editor-layout").value = app.editor.printLayout;
    document.getElementById("pb-editor-paper").value = app.editor.canvasWidth === 1200 && app.editor.canvasHeight === 1800 ? "portrait" : app.editor.canvasWidth === 1800 && app.editor.canvasHeight === 1200 ? "landscape" : "custom";
    document.getElementById("pb-editor-width").value = app.editor.canvasWidth;
    document.getElementById("pb-editor-height").value = app.editor.canvasHeight;
    document.getElementById("pb-editor-count").value = app.editor.slots.length;
    syncEditorLayoutControls();
    document.getElementById("pb-editor-canvas").width = app.editor.canvasWidth;
    document.getElementById("pb-editor-canvas").height = app.editor.canvasHeight;
    updateSlotFields();
    renderEditor();
  }

  window.pbCloseTemplateEditor = function () { document.getElementById("pb-template-editor-modal").classList.add("pb-hidden"); };

  function syncEditorLayoutControls() {
    var twoUp = app.editor.printLayout === "two_up";
    ["pb-editor-width", "pb-editor-height", "pb-editor-paper"].forEach(function (id) { document.getElementById(id).disabled = twoUp; });
    document.querySelectorAll("#pb-editor-count option").forEach(function (option) { option.disabled = twoUp && Number(option.value) > 3; });
    document.getElementById("pb-editor-side-switch").hidden = !twoUp;
    document.querySelectorAll("#pb-editor-side-switch button").forEach(function (button) { button.classList.toggle("is-active", button.dataset.side === app.editor.selectedSide); });
  }

  window.pbSelectTemplateSide = function (side) {
    if (side !== "left" && side !== "right") return;
    if (side === "right" && app.editor.printLayout !== "two_up") return;
    app.editor.selectedSide = side;
    syncEditorLayoutControls();
    updateSlotFields();
    renderEditor();
  };

  function updateEditorFromFields() {
    app.editor.name = document.getElementById("pb-editor-name").value;
    var width = Math.max(600, Math.min(2400, parseInt(document.getElementById("pb-editor-width").value, 10) || 1200));
    var height = Math.max(900, Math.min(3600, parseInt(document.getElementById("pb-editor-height").value, 10) || 1800));
    if (width !== app.editor.canvasWidth || height !== app.editor.canvasHeight) {
      app.editor.canvasWidth = width;
      app.editor.canvasHeight = height;
      document.getElementById("pb-editor-canvas").width = width;
      document.getElementById("pb-editor-canvas").height = height;
    }
    document.getElementById("pb-editor-paper").value = width === 1200 && height === 1800 ? "portrait" : width === 1800 && height === 1200 ? "landscape" : "custom";
    renderEditor();
  }

  function updateSlotFields() {
    var slot = selectedEditorSlots()[app.editor.selectedSlot];
    if (!slot) return;
    document.getElementById("pb-editor-slot-number").textContent = app.editor.selectedSlot + 1;
    document.getElementById("pb-editor-slot-side").textContent = app.editor.printLayout === "two_up" ? (app.editor.selectedSide === "right" ? "· ฝั่งขวา" : "· ฝั่งซ้าย") : "";
    ["x", "y", "width", "height"].forEach(function (field) { document.getElementById("pb-slot-" + field).value = slot[field]; });
  }

  function updateSlotFromFields() {
    var slot = selectedEditorSlots()[app.editor.selectedSlot];
    if (!slot) return;
    var half = app.editor.canvasWidth / 2;
    var minimumX = app.editor.printLayout === "two_up" && app.editor.selectedSide === "right" ? half : 0;
    var maximumX = app.editor.printLayout === "two_up" && app.editor.selectedSide === "left" ? half : app.editor.canvasWidth;
    slot.x = Math.max(minimumX, parseInt(document.getElementById("pb-slot-x").value, 10) || minimumX);
    slot.y = Math.max(0, parseInt(document.getElementById("pb-slot-y").value, 10) || 0);
    slot.width = Math.min(maximumX - minimumX, Math.max(40, parseInt(document.getElementById("pb-slot-width").value, 10) || 40));
    slot.height = Math.min(app.editor.canvasHeight, Math.max(40, parseInt(document.getElementById("pb-slot-height").value, 10) || 40));
    slot.x = Math.min(slot.x, maximumX - slot.width);
    slot.y = Math.min(slot.y, app.editor.canvasHeight - slot.height);
    updateSlotFields();
    renderEditor();
  }

  function readEditorFile(event, kind) {
    var file = event.target.files && event.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      app.editor[kind + "Data"] = reader.result;
      app.editor[kind + "Url"] = reader.result;
      app.editor[kind + "Removed"] = false;
      renderEditor();
    };
    reader.readAsDataURL(file);
  }

  window.pbClearTemplateAsset = function (kind) {
    if (kind !== "background" && kind !== "overlay") return;
    app.editor[kind + "Data"] = null;
    app.editor[kind + "Url"] = null;
    app.editor[kind + "Removed"] = true;
    document.getElementById("pb-editor-" + kind).value = "";
    renderEditor();
  };

  function canvasPoint(event) {
    var canvas = document.getElementById("pb-editor-canvas");
    var rect = canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * canvas.width / rect.width, y: (event.clientY - rect.top) * canvas.height / rect.height };
  }

  function editorPointerDown(event) {
    var point = canvasPoint(event);
    var side = app.editor.printLayout === "two_up" && point.x >= app.editor.canvasWidth / 2 ? "right" : "left";
    var slots = side === "right" ? app.editor.rightSlots : app.editor.slots;
    for (var index = slots.length - 1; index >= 0; index--) {
      var slot = slots[index];
      if (point.x >= slot.x && point.x <= slot.x + slot.width && point.y >= slot.y && point.y <= slot.y + slot.height) {
        app.editor.selectedSlot = index;
        app.editor.selectedSide = side;
        app.editor.dragging = { dx: point.x - slot.x, dy: point.y - slot.y };
        event.currentTarget.setPointerCapture(event.pointerId);
        syncEditorLayoutControls();
        updateSlotFields();
        renderEditor();
        break;
      }
    }
  }

  function editorPointerMove(event) {
    if (!app.editor.dragging) return;
    var point = canvasPoint(event);
    var slot = selectedEditorSlots()[app.editor.selectedSlot];
    var minimumX = app.editor.printLayout === "two_up" && app.editor.selectedSide === "right" ? app.editor.canvasWidth / 2 : 0;
    var maximumX = app.editor.printLayout === "two_up" && app.editor.selectedSide === "left" ? app.editor.canvasWidth / 2 : app.editor.canvasWidth;
    slot.x = Math.round(Math.max(minimumX, Math.min(maximumX - slot.width, point.x - app.editor.dragging.dx)));
    slot.y = Math.round(Math.max(0, Math.min(app.editor.canvasHeight - slot.height, point.y - app.editor.dragging.dy)));
    updateSlotFields();
    renderEditor();
  }

  function drawEditorAsset(context, image, canvas) {
    if (!image) return;
    var fullRatio = canvas.width / canvas.height;
    var imageRatio = image.naturalWidth / image.naturalHeight;
    if (app.editor.printLayout === "two_up" && Math.abs(imageRatio - fullRatio / 2) <= fullRatio * .01) {
      context.drawImage(image, 0, 0, canvas.width / 2, canvas.height);
      context.drawImage(image, canvas.width / 2, 0, canvas.width / 2, canvas.height);
    } else context.drawImage(image, 0, 0, canvas.width, canvas.height);
  }

  async function renderEditor() {
    var canvas = document.getElementById("pb-editor-canvas");
    if (!canvas) return;
    var version = (app.editor.renderVersion || 0) + 1;
    app.editor.renderVersion = version;
    var background = await loadImage(app.editor.backgroundUrl).catch(function () { return null; });
    if (version !== app.editor.renderVersion) return;
    var overlay = await loadImage(app.editor.overlayUrl).catch(function () { return null; });
    if (version !== app.editor.renderVersion) return;
    var context = canvas.getContext("2d");
    context.fillStyle = "#f8f4ec";
    context.fillRect(0, 0, canvas.width, canvas.height);
    drawEditorAsset(context, background, canvas);
    function drawSlot(slot, index, side) {
      var selected = index === app.editor.selectedSlot && side === app.editor.selectedSide;
      context.fillStyle = selected ? "rgba(56,189,248,.36)" : "rgba(255,255,255,.28)";
      context.fillRect(slot.x, slot.y, slot.width, slot.height);
      context.strokeStyle = selected ? "#0ea5e9" : "#fff";
      context.lineWidth = selected ? 8 : 5;
      context.strokeRect(slot.x, slot.y, slot.width, slot.height);
      context.fillStyle = "#0f172a";
      context.font = "700 42px system-ui";
      context.fillText(String(index + 1), slot.x + 20, slot.y + 52);
    }
    app.editor.slots.forEach(function (slot, index) { drawSlot(slot, index, "left"); });
    if (app.editor.printLayout === "two_up") app.editor.rightSlots.forEach(function (slot, index) { drawSlot(slot, index, "right"); });
    drawEditorAsset(context, overlay, canvas);
  }

  window.pbSaveTemplateDefinition = async function () {
    var button = document.getElementById("pb-editor-save");
    button.disabled = true;
    try {
      var payload = {
        id: app.editor.id,
        name: app.editor.name.trim() || "เทมเพลตใหม่",
        canvas_width: app.editor.canvasWidth,
        canvas_height: app.editor.canvasHeight,
        print_layout: app.editor.printLayout,
        slots: app.editor.slots,
        right_slots: app.editor.printLayout === "two_up" ? app.editor.rightSlots : null,
        background_data: app.editor.backgroundData,
        overlay_data: app.editor.overlayData,
        remove_background: app.editor.backgroundRemoved,
        remove_overlay: app.editor.overlayRemoved
      };
      var saved = await api("/api/share/templates", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      delete app.imageCache["/api/share/templates/" + saved.id + "/asset/background"];
      delete app.imageCache["/api/share/templates/" + saved.id + "/asset/overlay"];
      await loadTemplates();
      var activated = await activateTemplate(saved.id);
      window.pbCloseTemplateEditor();
      toast(activated ? "บันทึกเทมเพลตและตั้งเป็นกรอบปัจจุบันแล้ว" : "บันทึกเทมเพลตแล้ว · ยังไม่เปลี่ยนกรอบขณะมีคิวกำลังถ่าย");
    } catch (error) { toast(error.message, 4800); }
    finally { button.disabled = false; }
  };

  async function loadCameraPanel() {
    var section = document.getElementById("pb-hardware-camera");
    if (!section) return;
    section.innerHTML = '<div class="pb-loading">กำลังตรวจกล้อง Canon…</div>';
    try {
      var data = await api("/api/share/hardware/cameras?ts=" + Date.now());
      var supported = (data.detected || []).filter(function (camera) { return camera.supported; });
      section.innerHTML = '<div class="pb-section-heading"><div><strong>Canon ผ่านสาย USB</strong><p>ระบบใช้ gphoto2 และรองรับกล้อง Canon ครั้งละหนึ่งตัว</p></div><button type="button" onclick="pbRefreshHardware()">ตรวจใหม่</button></div>' + (supported.length ? supported.map(function (camera) { return '<div class="pb-device-row online"><span>●</span><div><strong>' + escapeHtml(camera.model) + '</strong><small>' + escapeHtml(camera.port) + '</small></div></div>'; }).join("") : '<div class="pb-device-empty">ยังไม่พบกล้อง Canon — ตรวจสาย USB, เปิดกล้อง และปิด EOS Utility</div>');
    } catch (error) { section.innerHTML = '<div class="pb-device-empty">' + escapeHtml(error.message) + '</div>'; }
  }

  async function loadPrinters(options) {
    try {
      var data = await api("/api/share/hardware/printers?ts=" + Date.now());
      var previousProfileId = app.activePrinterProfileId;
      app.printers = data.printers || [];
      app.printerProfiles = data.profiles || [];
      app.activePrinterProfileId = data.active_profile_id;
      app.paperSizes = data.paper_sizes || [];
      if (previousProfileId !== app.activePrinterProfileId) {
        var activeProfile = app.printerProfiles.find(function (profile) { return profile.id === app.activePrinterProfileId; });
        var defaultCopies = activeProfile ? Math.max(1, Math.min(99, activeProfile.copies || 1)) : 1;
        window.finishedPrintQty = defaultCopies;
        window.galleryPrintQty = defaultCopies;
        ["pb-finished-print-qty", "pb-finished-print-qty-label", "pb-gallery-print-qty", "pb-gallery-print-qty-label"].forEach(function (id) {
          var element = document.getElementById(id);
          if (element) element.textContent = defaultCopies;
        });
      }
      renderPrinters();
    } catch (error) {
      var section = document.getElementById("pb-hardware-printers");
      if (section) section.innerHTML = '<div class="pb-device-empty">' + escapeHtml(error.message) + '</div>';
      if (options && options.rethrow) throw error;
    }
  }

  function renderPrinters() {
    var section = document.getElementById("pb-hardware-printers");
    if (!section) return;
    var knownPrinters = app.printers.map(function (printer) { return printer.name; });
    app.printerProfiles.forEach(function (profile) { if (knownPrinters.indexOf(profile.printer_name) === -1) knownPrinters.push(profile.printer_name); });
    var detected = app.printers.length ? '<div class="pb-detected-printers">' + app.printers.map(function (printer) {
      return '<div class="pb-device-row ' + (printer.online ? "online" : "offline") + '"><span>●</span><div><strong>' + escapeHtml(printer.name) + (printer.default ? ' <small>ค่าเริ่มต้น</small>' : '') + '</strong><small>' + escapeHtml(printer.status) + '</small></div></div>';
    }).join("") + '</div>' : '<div class="pb-device-empty">ยังไม่พบเครื่องพิมพ์ใน macOS/CUPS</div>';
    section.innerHTML = '<div class="pb-section-heading"><div><strong>โปรไฟล์เครื่องพิมพ์ CUPS</strong><p>เลือกเครื่องและขนาดกระดาษสำหรับปุ่มพิมพ์ทุกจุด</p></div></div>' + detected + '<div class="pb-printer-layout"><div class="pb-profile-list">' + app.printerProfiles.map(function (profile) {
      var id = safeId(profile.id);
      return '<article class="pb-profile ' + (profile.id === app.activePrinterProfileId ? "is-active" : "") + '"><div><strong>' + escapeHtml(profile.name) + '</strong><span>' + escapeHtml(profile.printer_name) + ' • ' + escapeHtml(profile.paper_size) + '</span></div><div class="pb-card-actions"><button type="button" onclick="pbActivatePrinter(\'' + id + '\')">เลือกใช้</button><button type="button" onclick="pbTestPrinter(\'' + id + '\')">Test Print</button>' + (app.printerProfiles.length > 1 ? '<button class="danger" type="button" onclick="pbDeletePrinter(\'' + id + '\')">ลบ</button>' : "") + '</div></article>';
    }).join("") + '</div><form id="pb-printer-form" class="pb-printer-form" onsubmit="return pbSavePrinter(event)"><strong>เพิ่มโปรไฟล์</strong><label>ชื่อโปรไฟล์<input id="pb-printer-profile-name" required value="โปรไฟล์ใหม่"></label><label>เครื่องพิมพ์<select id="pb-printer-name" required>' + knownPrinters.map(function (name) { return '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>'; }).join("") + '</select></label><label>ขนาดกระดาษ<select id="pb-printer-paper">' + app.paperSizes.map(function (paper) { return '<option value="' + escapeHtml(paper.id) + '">' + escapeHtml(paper.name) + '</option>'; }).join("") + '</select></label><label>จำนวนเริ่มต้น<input id="pb-printer-copies" type="number" min="1" max="99" value="1"></label><button type="submit">บันทึกโปรไฟล์</button></form></div>';
  }

  window.pbSavePrinter = async function (event) {
    event.preventDefault();
    try {
      await api("/api/share/printer-profiles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: document.getElementById("pb-printer-profile-name").value, printer_name: document.getElementById("pb-printer-name").value, paper_size: document.getElementById("pb-printer-paper").value, copies: parseInt(document.getElementById("pb-printer-copies").value, 10) || 1 }) });
      await loadPrinters();
      toast("บันทึกโปรไฟล์เครื่องพิมพ์แล้ว");
    } catch (error) { toast(error.message, 4200); }
    return false;
  };

  window.pbActivatePrinter = async function (id) {
    try { await api("/api/share/printer-profiles/" + encodeURIComponent(id) + "/activate", { method: "POST" }); await loadPrinters(); toast("เลือกโปรไฟล์เครื่องพิมพ์แล้ว"); } catch (error) { toast(error.message, 4200); }
  };
  window.pbDeletePrinter = async function (id) {
    if (!confirm("ลบโปรไฟล์เครื่องพิมพ์นี้ใช่หรือไม่?")) return;
    try { await api("/api/share/printer-profiles/" + encodeURIComponent(id), { method: "DELETE" }); await loadPrinters(); } catch (error) { toast(error.message, 4200); }
  };
  window.pbTestPrinter = async function (id) {
    if (!confirm("ส่งกระดาษทดสอบ 1 แผ่นไปยังเครื่องพิมพ์นี้ใช่หรือไม่?")) return;
    try { await api("/api/share/printer-profiles/" + encodeURIComponent(id) + "/test", { method: "POST" }); toast("ส่ง Test Print แล้ว"); } catch (error) { toast(error.message, 4500); }
  };

  async function loadPreflightPanel() {
    var section = document.getElementById("pb-hardware-preflight");
    if (!section) return;
    section.innerHTML = '<div class="pb-loading">กำลังตรวจความพร้อมระบบ…</div>';
    try {
      var data = await api("/api/share/preflight?ts=" + Date.now());
      var badgeLabel = { ok: "ผ่าน", warning: "คำเตือน", fail: "ไม่ผ่าน" };
      var summary = data.summary || {};
      var headerNote = data.ready_for_event
        ? "ระบบหลักพร้อมเปิดใช้งาน (ผ่าน " + (summary.ok || 0) + " · คำเตือน " + (summary.warning || 0) + ")"
        : "ยังไม่พร้อมเปิดงาน ตรวจรายการด้านล่าง";
      if (typeof data.ready_to_capture === "boolean") {
        headerNote += " · " + (data.ready_to_capture ? "ถ่ายได้" : "ถ่ายไม่ได้")
          + " · " + (data.ready_to_print ? "พิมพ์ภาพเดิมได้" : "ยังพิมพ์ไม่ได้");
      }
      section.innerHTML =
        '<div class="pb-section-heading"><div><strong>รายการตรวจความพร้อมก่อนเปิดงาน</strong><p>' + escapeHtml(headerNote) + '</p></div><button type="button" onclick="pbRefreshHardware()">ตรวจใหม่</button></div>' +
        '<div class="pb-preflight-list">' +
        (data.checks || []).map(function (check) {
          var cls = check.status === "ok" ? "ok" : (check.status === "fail" ? "fail" : "warning");
          return '<div class="pb-preflight-row ' + cls + '"><span class="pb-preflight-badge">' + (badgeLabel[check.status] || escapeHtml(check.status)) + '</span><div><strong>' + escapeHtml(check.label) + '</strong><small>' + escapeHtml(check.detail) + '</small></div></div>';
        }).join("") +
        '</div>';
    } catch (error) {
      section.innerHTML = '<div class="pb-device-empty">' + escapeHtml(error.message) + '</div>';
    }
  }

  window.pbOpenHardware = async function () {
    document.getElementById("pb-hardware-modal").classList.remove("pb-hidden");
    await Promise.all([loadCameraPanel(), loadTemplates(), loadPrinters(), loadPreflightPanel()]);
  };
  window.pbCloseHardware = function () { document.getElementById("pb-hardware-modal").classList.add("pb-hidden"); };
  window.pbRefreshHardware = function () { loadCameraPanel(); loadPrinters(); loadPreflightPanel(); };

  function createStudioModal() {
    var modal = document.createElement("div");
    modal.id = "pb-template-modal";
    modal.className = "pb-modal pb-hidden";
    modal.innerHTML = '<div class="pb-modal-overlay" onclick="pbCloseTemplateStudio()"></div><div class="pb-modal-box pb-template-box"><div class="pb-modal-header"><h3 id="pb-template-heading">จัดลำดับภาพในเทมเพลต</h3><button class="pb-modal-close" type="button" onclick="pbCloseTemplateStudio()" aria-label="ปิด">×</button></div><div class="pb-template-layout"><div class="pb-template-panel"><div id="pb-studio-import" class="pb-studio-import" hidden><label class="pb-section-label" for="pb-studio-files">1. เพิ่มภาพจาก Mac (JPG/PNG)</label><input id="pb-studio-files" type="file" accept="image/jpeg,image/png,.jpg,.jpeg,.png" multiple><small>เลือกทีละภาพหรือหลายภาพก็ได้ เพิ่มรอบถัดไปได้ · บันทึกโดยไม่ผูกคิว</small></div><span id="pb-studio-template-label" class="pb-section-label">1. เลือกเทมเพลตที่มีจำนวนช่องตรงกับชุดภาพ</span><div id="pb-studio-template-options" class="pb-template-options"></div><div class="pb-template-order"><span class="pb-section-label">2. จัดลำดับและครอบภาพ</span><div id="pb-template-order"></div></div><div class="pb-template-fields"><span class="pb-section-label">3. แก้ข้อความบนภาพ</span><label>ชื่อหลัก<input id="pb-template-title" maxlength="120"></label><label>ข้อความรอง<input id="pb-template-subtitle" maxlength="180"></label></div><div class="pb-template-actions"><button type="button" class="pb-btn pb-btn-gallery" onclick="pbCloseTemplateStudio()">ยกเลิก</button><button id="pb-template-save" type="button" class="pb-btn pb-btn-keep" onclick="pbSaveTemplate()">สร้างภาพพร้อม QR</button></div><div id="pb-template-status" class="pb-template-status" aria-live="polite"></div><div id="pb-template-result" class="pb-template-result"><strong>ภาพพร้อมดาวน์โหลดแล้ว</strong><br><img id="pb-template-qr" alt="QR code สำหรับดาวน์โหลดภาพ"><div class="pb-template-result-links"><a id="pb-template-download" target="_blank" rel="noopener">ดาวน์โหลด / เปิดภาพ</a><button type="button" onclick="pbPrintTemplateResult()">พิมพ์ภาพ</button><button type="button" onclick="pbShareTemplateResult()">แชร์ / AirDrop</button><button type="button" onclick="pbCloseTemplateStudio()">เสร็จสิ้น</button></div></div></div><div class="pb-template-preview"><canvas id="pb-template-canvas" width="1200" height="1800"></canvas></div></div></div>';
    document.body.appendChild(modal);
    document.getElementById("pb-template-title").addEventListener("input", function (event) { app.studio.title = event.target.value; renderStudioCanvas(); });
    document.getElementById("pb-template-subtitle").addEventListener("input", function (event) { app.studio.subtitle = event.target.value; renderStudioCanvas(); });
    document.getElementById("pb-studio-files").addEventListener("change", function (event) { window.pbImportStudioFiles(event.target.files); });
  }

  function releaseImportPhotos() {
    app.studio.objectUrls.forEach(function (url) { delete app.imageCache[url]; URL.revokeObjectURL(url); });
    app.studio.objectUrls = [];
  }

  function setStudioMode(mode) {
    app.studio.mode = mode;
    document.getElementById("pb-template-heading").textContent = mode === "import" ? "สร้างภาพจากไฟล์ · ไม่ผูกคิว" : "จัดลำดับภาพในเทมเพลต";
    document.getElementById("pb-studio-import").hidden = mode !== "import";
    document.getElementById("pb-studio-template-label").textContent = mode === "import" ? "2. เลือกเทมเพลต" : "1. เลือกเทมเพลตที่มีจำนวนช่องตรงกับชุดภาพ";
    document.getElementById("pb-template-save").textContent = mode === "import" ? "บันทึกภาพ (ไม่ผูกคิว)" : "สร้างภาพพร้อม QR";
  }

  function renderStudioOptions() {
    var container = document.getElementById("pb-studio-template-options");
    var count = app.studio.sources.length;
    var matches = app.templates.filter(function (template) { return app.studio.mode === "import" || template.capture_count === count; });
    container.innerHTML = matches.map(function (template) { return '<button type="button" class="pb-template-option ' + (template.id === app.studio.templateId ? "is-active" : "") + '" onclick="pbSetStudioTemplate(\'' + safeId(template.id) + '\')">' + templateThumb(template) + '<span>' + escapeHtml(template.name) + ' · ' + template.capture_count + ' ภาพ</span></button>'; }).join("") || '<p class="pb-template-status">ไม่มีเทมเพลตที่มี ' + count + ' ช่องภาพ</p>';
  }

  window.pbSetStudioTemplate = function (id) {
    app.studio.templateId = id;
    if (app.studio.mode === "import") app.studio.templateManuallySelected = true;
    app.studio.result = null;
    renderStudioOptions();
    document.getElementById("pb-template-result").classList.remove("is-visible");
    updateImportProgress();
    renderStudioCanvas().catch(function (error) { document.getElementById("pb-template-status").textContent = error.message; });
  };

  function updateImportProgress(updateStatus) {
    if (app.studio.mode !== "import") return;
    var template = templateById(app.studio.templateId);
    var count = app.studio.sources.length;
    var saveButton = document.getElementById("pb-template-save");
    if (saveButton) {
      saveButton.disabled = !template || count !== template.capture_count;
      saveButton.textContent = template && count < template.capture_count ? "เพิ่มภาพอีก " + (template.capture_count - count) + " ภาพ" : "บันทึกภาพ (ไม่ผูกคิว)";
    }
    if (updateStatus === false) return;
    document.getElementById("pb-template-status").textContent = template
      ? count === template.capture_count ? "ภาพครบ " + count + "/" + template.capture_count + " ช่อง · ตรวจตัวอย่างแล้วกดบันทึกได้" : count > template.capture_count ? "มีภาพเกิน " + (count - template.capture_count) + " ภาพ · เอาภาพออกหรือเลือกเทมเพลตที่มีช่องมากกว่า" : "เลือกแล้ว " + count + "/" + template.capture_count + " ภาพ · เพิ่มภาพได้อีก " + (template.capture_count - count) + " ภาพ"
      : "เลือกเทมเพลตก่อนเพิ่มภาพ";
  }

  async function renderStudioCanvas() {
    var template = templateById(app.studio.templateId);
    var canvas = document.getElementById("pb-template-canvas");
    var renderVersion = app.studio.renderVersion = (app.studio.renderVersion || 0) + 1;
    if (!canvas) return;
    if (!template || app.studio.mode !== "import" && app.studio.sources.length !== template.capture_count) {
      var empty = canvas.getContext("2d");
      empty.fillStyle = "#201a1d";
      empty.fillRect(0, 0, canvas.width, canvas.height);
      empty.fillStyle = "#f6e5c6";
      empty.textAlign = "center";
      empty.font = "600 38px system-ui,sans-serif";
      empty.fillText("เลือกภาพให้ครบตามช่องเทมเพลต", canvas.width / 2, canvas.height / 2, canvas.width * .88);
      return false;
    }
    var loaded;
    try {
      loaded = await Promise.all([
        loadImage(template.background_url).catch(function () { return null; }),
        Promise.all(app.studio.sources.slice(0, template.capture_count).map(function (source) { return loadImage(source.full_url); })),
        loadImage(template.overlay_url).catch(function () { return null; })
      ]);
    } catch (error) {
      if (renderVersion !== app.studio.renderVersion) return false;
      throw error;
    }
    if (renderVersion !== app.studio.renderVersion) return false;
    canvas.width = template.canvas_width;
    canvas.height = template.canvas_height;
    var context = canvas.getContext("2d");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    var background = loaded[0];
    if (background) context.drawImage(background, 0, 0, canvas.width, canvas.height);
    var photos = loaded[1];
    photos.forEach(function (photo, index) {
      var slot = template.slots[index];
      var crop = app.studio.sources[index].crop;
      drawCover(context, photo, slot, true, crop);
      if (template.print_layout === "two_up") drawCover(context, photo, template.right_slots && template.right_slots[index] || Object.assign({}, slot, { x: slot.x + canvas.width / 2 }), true, crop);
    });
    var overlay = loaded[2];
    if (overlay) context.drawImage(overlay, 0, 0, canvas.width, canvas.height);
    context.textAlign = "center";
    context.shadowColor = "rgba(0,0,0,.75)";
    context.shadowBlur = 14;
    context.fillStyle = "#fff6dc";
    context.font = "700 " + Math.round(canvas.width * .045) + "px Georgia,serif";
    if (app.studio.title) {
      if (template.print_layout === "two_up") {
        context.font = "700 " + Math.round(canvas.width * .024) + "px Georgia,serif";
        context.fillText(app.studio.title.slice(0, 48), canvas.width / 4, canvas.height * .91, canvas.width * .42);
        context.fillText(app.studio.title.slice(0, 48), canvas.width * .75, canvas.height * .91, canvas.width * .42);
      } else context.fillText(app.studio.title.slice(0, 48), canvas.width / 2, canvas.height * .91, canvas.width * .88);
    }
    context.fillStyle = "#f2d38a";
    context.font = "500 " + Math.round(canvas.width * .024) + "px system-ui,sans-serif";
    if (app.studio.subtitle) {
      if (template.print_layout === "two_up") {
        context.font = "500 " + Math.round(canvas.width * .014) + "px system-ui,sans-serif";
        context.fillText(app.studio.subtitle.slice(0, 70), canvas.width / 4, canvas.height * .95, canvas.width * .43);
        context.fillText(app.studio.subtitle.slice(0, 70), canvas.width * .75, canvas.height * .95, canvas.width * .43);
      } else context.fillText(app.studio.subtitle.slice(0, 70), canvas.width / 2, canvas.height * .95, canvas.width * .84);
    }
    context.shadowBlur = 0;
    return true;
  }

  function renderOrder() {
    document.getElementById("pb-template-order").innerHTML = app.studio.sources.map(function (source, index) {
      var crop = source.crop || { zoom: 100, x: 0, y: 0 };
      var isSelected = app.studio.cropIndex === index;
      var controls = isSelected ? '<div class="pb-crop-controls"><label>ขยายภาพ <output id="pb-crop-zoom-value">' + (Number(crop.zoom) || 100) + '%</output><input type="range" min="100" max="300" step="5" value="' + (Number(crop.zoom) || 100) + '" oninput="pbUpdateTemplateCrop(' + index + ',\'zoom\',this.value)"></label><label>เลื่อนซ้าย–ขวา <input type="range" min="-100" max="100" step="5" value="' + (Number(crop.x) || 0) + '" oninput="pbUpdateTemplateCrop(' + index + ',\'x\',this.value)"></label><label>เลื่อนบน–ล่าง <input type="range" min="-100" max="100" step="5" value="' + (Number(crop.y) || 0) + '" oninput="pbUpdateTemplateCrop(' + index + ',\'y\',this.value)"></label><button type="button" onclick="pbResetTemplateCrop(' + index + ')">คืนค่ากลางภาพ</button></div>' : '';
      return '<div class="pb-order-item"><div class="pb-order-row"><img src="' + source.thumbnail_url + '" alt="ภาพลำดับ ' + (index + 1) + '"><strong>ตำแหน่งที่ ' + (index + 1) + (source.file ? '<small>' + escapeHtml(source.file.name) + '</small>' : '') + '</strong><div class="pb-order-actions"><button type="button" onclick="pbSelectTemplateCrop(' + index + ')" aria-label="ปรับภาพตำแหน่งที่ ' + (index + 1) + '" aria-expanded="' + isSelected + '" class="' + (isSelected ? 'is-active' : '') + '">ครอบ</button><button type="button" onclick="pbMoveTemplatePhoto(' + index + ',-1)" ' + (index === 0 ? "disabled" : "") + '>↑</button><button type="button" onclick="pbMoveTemplatePhoto(' + index + ',1)" ' + (index === app.studio.sources.length - 1 ? "disabled" : "") + '>↓</button>' + (app.studio.mode === "import" ? '<button type="button" onclick="pbRemoveTemplatePhoto(' + index + ')" aria-label="เอาภาพตำแหน่งที่ ' + (index + 1) + ' ออก">×</button>' : '') + '</div></div>' + controls + '</div>';
    }).join("");
  }

  function invalidateStudioResult() {
    app.studio.result = null;
    document.getElementById("pb-template-result").classList.remove("is-visible");
  }

  window.pbSelectTemplateCrop = function (index) {
    app.studio.cropIndex = app.studio.cropIndex === index ? -1 : index;
    renderOrder();
  };
  window.pbUpdateTemplateCrop = function (index, axis, value) {
    var source = app.studio.sources[index];
    if (!source || ["zoom", "x", "y"].indexOf(axis) === -1) return;
    if (!source.crop) source.crop = { zoom: 100, x: 0, y: 0 };
    var number = Number(value);
    if (!Number.isFinite(number)) return;
    source.crop[axis] = axis === "zoom" ? Math.max(100, Math.min(300, number)) : Math.max(-100, Math.min(100, number));
    var output = document.getElementById("pb-crop-zoom-value");
    if (output && axis === "zoom") output.textContent = source.crop.zoom + "%";
    invalidateStudioResult();
    renderStudioCanvas().catch(function (error) { document.getElementById("pb-template-status").textContent = error.message; });
  };
  window.pbResetTemplateCrop = function (index) {
    var source = app.studio.sources[index];
    if (!source) return;
    source.crop = { zoom: 100, x: 0, y: 0 };
    invalidateStudioResult();
    renderOrder();
    renderStudioCanvas().catch(function (error) { document.getElementById("pb-template-status").textContent = error.message; });
  };

  async function findCurrentCollageId(explicitId) {
    if (explicitId) return explicitId;
    if (window.currentGalleryItemId) return window.currentGalleryItemId;
    var statusData = await api("/api/processing/status").catch(function () { return null; });
    if (statusData && statusData.jobmodel && statusData.jobmodel.present_mediaitem_id) return statusData.jobmodel.present_mediaitem_id;
    var items = await api("/api/mediacollection/?limit=30");
    var collage = items.find(function (item) { return item.media_type === "collage"; });
    return collage && collage.id;
  }

  window.pbOpenTemplateStudio = async function (explicitId) {
    releaseImportPhotos();
    app.studio.cloudPollId = (app.studio.cloudPollId || 0) + 1;
    app.studio.sources = [];
    app.studio.cropIndex = 0;
    app.studio.templateManuallySelected = false;
    app.studio.result = null;
    setStudioMode("captured");
    document.getElementById("pb-template-modal").classList.remove("pb-hidden");
    document.getElementById("pb-template-result").classList.remove("is-visible");
    document.getElementById("pb-template-status").textContent = "กำลังโหลดภาพต้นฉบับ…";
    try {
      if (!app.templates.length) await loadTemplates();
      app.studio.collageId = await findCurrentCollageId(explicitId);
      if (!app.studio.collageId) throw new Error("ยังไม่มีชุดภาพสำหรับจัดเทมเพลต");
      var payload = await api("/api/share/template-sources/" + app.studio.collageId);
      app.studio.sources = payload.sources;
      var active = templateById(app.activeTemplateId);
      app.studio.templateId = active && active.capture_count === payload.capture_count ? active.id : payload.suggested_template_id;
      if (!app.studio.templateId) throw new Error("ยังไม่มีเทมเพลต " + payload.capture_count + " ช่อง กรุณาสร้างเทมเพลตก่อน");
      document.getElementById("pb-template-title").value = app.studio.title;
      document.getElementById("pb-template-subtitle").value = app.studio.subtitle;
      renderStudioOptions();
      renderOrder();
      document.getElementById("pb-template-status").textContent = "กำลังสร้างตัวอย่างจากภาพถ่าย…";
      await renderStudioCanvas();
      document.getElementById("pb-template-status").textContent = "ใช้ลูกศรจัดลำดับ แล้วตรวจตัวอย่างก่อนสร้างภาพ";
    } catch (error) { document.getElementById("pb-template-status").textContent = error.message; toast(error.message, 4500); }
  };
  window.pbOpenImportTemplateStudio = async function () {
    releaseImportPhotos();
    app.studio.cloudPollId = (app.studio.cloudPollId || 0) + 1;
    app.studio.collageId = null;
    app.studio.sources = [];
    app.studio.cropIndex = 0;
    app.studio.templateManuallySelected = false;
    app.studio.result = null;
    setStudioMode("import");
    document.getElementById("pb-studio-files").value = "";
    document.getElementById("pb-template-title").value = app.studio.title;
    document.getElementById("pb-template-subtitle").value = app.studio.subtitle;
    document.getElementById("pb-template-result").classList.remove("is-visible");
    document.getElementById("pb-template-modal").classList.remove("pb-hidden");
    document.getElementById("pb-template-status").textContent = "เลือกเทมเพลต แล้วเพิ่มภาพทีละภาพหรือหลายภาพก็ได้";
    try {
      if (!app.templates.length) await loadTemplates();
      app.studio.templateId = app.activeTemplateId;
      renderStudioOptions();
      renderOrder();
      await renderStudioCanvas();
      updateImportProgress();
    } catch (error) { document.getElementById("pb-template-status").textContent = error.message; }
  };
  window.pbImportStudioFiles = async function (files) {
    if (app.studio.mode !== "import") return;
    var selected = Array.prototype.slice.call(files || []);
    if (!selected.length) return;
    var allFiles = app.studio.sources.map(function (source) { return source.file; }).concat(selected);
    if (allFiles.length > 6 || allFiles.some(function (file) { return !["image/jpeg", "image/png", ""].includes(file.type) || !/\.(jpe?g|png)$/i.test(file.name) || file.size > 15000000; }) || allFiles.reduce(function (sum, file) { return sum + file.size; }, 0) > 60000000) {
      document.getElementById("pb-studio-files").value = "";
      document.getElementById("pb-template-status").textContent = "เลือก JPG/PNG ไม่เกิน 6 ภาพ ภาพละ 15 MB และรวมไม่เกิน 60 MB";
      return;
    }
    selected.forEach(function (file) {
      var url = URL.createObjectURL(file);
      app.studio.objectUrls.push(url);
      app.studio.sources.push({ file: file, full_url: url, thumbnail_url: url });
    });
    document.getElementById("pb-studio-files").value = "";
    invalidateStudioResult();
    var current = templateById(app.studio.templateId);
    if (!current || current.capture_count < app.studio.sources.length || !app.studio.templateManuallySelected) {
      var matching = app.templates.find(function (template) { return template.capture_count === app.studio.sources.length; }) || app.templates.find(function (template) { return template.capture_count >= app.studio.sources.length; });
      if (matching) app.studio.templateId = matching.id;
    }
    renderStudioOptions();
    renderOrder();
    try {
      await renderStudioCanvas();
      updateImportProgress();
    } catch (error) { document.getElementById("pb-template-status").textContent = error.message; }
  };
  window.pbCloseTemplateStudio = function () {
    document.getElementById("pb-template-modal").classList.add("pb-hidden");
    app.studio.cloudPollId = (app.studio.cloudPollId || 0) + 1;
    releaseImportPhotos();
  };
  window.pbMoveTemplatePhoto = function (index, delta) {
    var next = index + delta;
    if (next < 0 || next >= app.studio.sources.length) return;
    var moved = app.studio.sources.splice(index, 1)[0];
    app.studio.sources.splice(next, 0, moved);
    if (app.studio.cropIndex === index) app.studio.cropIndex = next;
    else if (app.studio.cropIndex === next) app.studio.cropIndex = index;
    invalidateStudioResult();
    renderOrder();
    renderStudioCanvas().catch(function (error) { document.getElementById("pb-template-status").textContent = error.message; });
  };
  window.pbRemoveTemplatePhoto = function (index) {
    if (app.studio.mode !== "import") return;
    var removed = app.studio.sources.splice(index, 1)[0];
    if (!removed) return;
    delete app.imageCache[removed.full_url];
    URL.revokeObjectURL(removed.full_url);
    app.studio.objectUrls = app.studio.objectUrls.filter(function (url) { return url !== removed.full_url; });
    invalidateStudioResult();
    if (app.studio.cropIndex >= app.studio.sources.length) app.studio.cropIndex = app.studio.sources.length - 1;
    renderOrder();
    updateImportProgress();
    renderStudioCanvas().catch(function (error) { document.getElementById("pb-template-status").textContent = error.message; });
  };

  window.pbSaveTemplate = async function () {
    var button = document.getElementById("pb-template-save");
    button.disabled = true;
    try {
      var selectedTemplate = templateById(app.studio.templateId);
      if (!selectedTemplate || app.studio.sources.length !== selectedTemplate.capture_count) throw new Error("เลือกภาพให้ครบตามจำนวนช่องของเทมเพลต");
      document.getElementById("pb-template-status").textContent = "กำลังบันทึกภาพ… กรุณารอสักครู่";
      if (!await renderStudioCanvas()) throw new Error("ตัวอย่างภาพยังไม่พร้อม กรุณารอสักครู่แล้วลองอีกครั้ง");
      var canvas = document.getElementById("pb-template-canvas");
      if (app.studio.mode === "import") {
        var blob = await new Promise(function (resolve, reject) { canvas.toBlob(function (value) { value ? resolve(value) : reject(new Error("สร้างภาพจากเทมเพลตไม่สำเร็จ")); }, "image/png"); });
        var form = new FormData();
        form.append("template_id", app.studio.templateId);
        form.append("title", app.studio.title);
        form.append("subtitle", app.studio.subtitle);
        form.append("composed_image", blob, "template.png");
        app.studio.sources.forEach(function (source) { form.append("photos", source.file, source.file.name); });
        app.studio.result = await api("/api/share/compose-template-from-files", { method: "POST", body: form });
      } else {
        app.studio.result = await api("/api/share/compose-template", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ image_data: canvas.toDataURL("image/png"), source_ids: app.studio.sources.map(function (source) { return source.id; }), template_id: app.studio.templateId, title: app.studio.title, subtitle: app.studio.subtitle }) });
      }
      var result = app.studio.result;
      var qr = document.getElementById("pb-template-qr");
      var download = document.getElementById("pb-template-download");
      qr.removeAttribute("src");
      qr.style.display = "none";
      download.href = result.share_url;
      download.textContent = "เปิดภาพที่บันทึกบน Mac";
      document.getElementById("pb-template-result").classList.add("is-visible");
      document.getElementById("pb-template-status").textContent = result.cloud_status === "offline"
        ? "บันทึกภาพแล้ว (โหมดออฟไลน์) · พิมพ์ / AirDrop / QR วงแลนพร้อมใช้งานทันที"
        : result.cloud_status === "disabled"
          ? "บันทึกภาพแล้ว · QR ใช้ได้เฉพาะ Wi-Fi เดียวกับ Mac"
          : "บันทึกภาพแล้ว · กำลังอัปโหลดเพื่อสร้าง QR ออนไลน์…";
      if (result.cloud_status === "disabled" || result.cloud_status === "offline") {
        qr.src = result.qr_url + "?t=" + Date.now();
        qr.style.display = "";
      } else {
        var pollId = app.studio.cloudPollId = (app.studio.cloudPollId || 0) + 1;
        (async function pollCloud(attempt) {
          if (app.studio.cloudPollId !== pollId) return;
          try {
            var cloud = await api("/api/share/cloud-status/" + result.id);
            if (cloud.status === "ready") {
              qr.src = result.qr_url + "?t=" + Date.now();
              qr.style.display = "";
              download.href = cloud.share_url;
              download.textContent = "ดาวน์โหลดออนไลน์ (1 ชั่วโมง)";
              document.getElementById("pb-template-status").textContent = "อัปโหลดสำเร็จ · QR ใช้ได้ทุกเครือข่าย 1 ชั่วโมง";
              toast("QR ออนไลน์พร้อมใช้งานแล้ว");
              return;
            }
            if (cloud.status === "offline") {
              qr.src = result.qr_url + "?t=" + Date.now();
              qr.style.display = "";
              document.getElementById("pb-template-status").textContent = "บันทึกภาพแล้ว (โหมดออฟไลน์) · พิมพ์ / AirDrop / QR วงแลนพร้อมใช้งานทันที";
              return;
            }
            if (cloud.status === "expired") {
              document.getElementById("pb-template-status").textContent = "QR หมดอายุแล้ว · ภาพยังอยู่บน Mac";
              return;
            }
            if (cloud.status === "failed") {
              qr.src = result.qr_url + "?t=" + Date.now();
              qr.style.display = "";
              document.getElementById("pb-template-status").textContent = "อัปโหลดออนไลน์ไม่สำเร็จ · ใช้ QR วงแลนสำรองบนภาพนี้ได้ทันที";
              return;
            }
            document.getElementById("pb-template-status").textContent = "กำลังอัปโหลดภาพเพื่อสร้าง QR ออนไลน์…";
          } catch (_error) { document.getElementById("pb-template-status").textContent = "กำลังรอการเชื่อมต่อ · ภาพยังอยู่บน Mac"; }
          if (attempt < 80) setTimeout(function () { pollCloud(attempt + 1); }, 3000);
        })(0);
      }
      if (typeof window.pbUpdateGalleryCount === "function") window.pbUpdateGalleryCount();
    } catch (error) { document.getElementById("pb-template-status").textContent = error.message; toast(error.message, 4500); }
    finally { button.disabled = false; updateImportProgress(false); }
  };

  window.pbPrintTemplateResult = function () { if (app.studio.result) window.pbPrintItem(app.studio.result.id, 1); };
  window.pbShareTemplateResult = function () { if (app.studio.result && typeof window.pbShareItem === "function") window.pbShareItem(app.studio.result.id); };

  function installProfilePrinting() {
    var pendingPrint = null;
    var sendingPrint = false;
    var picker = document.createElement("div");
    picker.id = "pb-print-picker";
    picker.className = "pb-modal pb-hidden";
    picker.innerHTML = '<div class="pb-modal-overlay"></div><div class="pb-modal-box pb-print-picker-box" role="dialog" aria-modal="true" aria-labelledby="pb-print-picker-title"><div class="pb-modal-header"><h3 id="pb-print-picker-title">เลือกเครื่องพิมพ์</h3><button type="button" class="pb-modal-close" id="pb-print-picker-close" aria-label="ปิด">×</button></div><p>เลือกเครื่องสำหรับภาพนี้ก่อนส่งเข้าคิว · หนึ่งครั้งส่งไปหนึ่งเครื่อง</p><label for="pb-print-picker-profile">เครื่องพิมพ์และกระดาษ</label><select id="pb-print-picker-profile"></select><p id="pb-print-picker-status" role="status"></p><div class="pb-print-picker-actions"><button type="button" id="pb-print-picker-cancel">ยกเลิก</button><button type="button" id="pb-print-picker-submit">ส่งเข้าคิวพิมพ์</button></div></div>';
    document.body.appendChild(picker);
    var select = picker.querySelector("#pb-print-picker-profile");
    var status = picker.querySelector("#pb-print-picker-status");
    var submit = picker.querySelector("#pb-print-picker-submit");
    var copiesInput = document.createElement("input");
    copiesInput.id = "pb-print-picker-copies";
    copiesInput.type = "number";
    copiesInput.min = "1";
    copiesInput.max = "99";
    copiesInput.step = "1";
    copiesInput.inputMode = "numeric";
    var copiesLabel = document.createElement("label");
    copiesLabel.htmlFor = copiesInput.id;
    copiesLabel.textContent = "จำนวนใบ";
    select.after(copiesLabel, copiesInput);
    function closePicker() { if (!sendingPrint) { picker.classList.add("pb-hidden"); pendingPrint = null; } }
    picker.querySelector(".pb-modal-overlay").onclick = closePicker;
    picker.querySelector("#pb-print-picker-close").onclick = closePicker;
    picker.querySelector("#pb-print-picker-cancel").onclick = closePicker;
    select.onchange = function () {
      var profile = app.printerProfiles.find(function (entry) { return entry.id === select.value; });
      var printer = profile && app.printers.find(function (entry) { return entry.name === profile.printer_name; });
      status.textContent = printer && printer.online ? "พร้อมรับงาน · " + printer.name : "เครื่องนี้ไม่พร้อมรับงาน กรุณาตรวจการเชื่อมต่อก่อน";
      submit.disabled = !printer || !printer.online;
    };
    submit.onclick = async function () {
      if (!pendingPrint || submit.disabled) return;
      var profile = app.printerProfiles.find(function (entry) { return entry.id === select.value; });
      if (!profile) return;
      if (!copiesInput.reportValidity()) return;
      var copies = Number(copiesInput.value);
      if (!Number.isInteger(copies) || copies < 1 || copies > 99) {
        status.textContent = "กรอกจำนวน 1–99 ใบ";
        return;
      }
      sendingPrint = true;
      submit.disabled = true;
      select.disabled = true;
      copiesInput.disabled = true;
      status.textContent = "กำลังส่งงานไป " + profile.printer_name + "…";
      try {
        var idemKey = (window.crypto && typeof window.crypto.randomUUID === "function")
          ? ("print-" + window.crypto.randomUUID())
          : ("print-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10));
        var path = "/api/share/print/" + encodeURIComponent(pendingPrint.id);
        var headers = { "Content-Type": "application/json", "Idempotency-Key": idemKey };
        var payload = { profile_id: profile.id, copies: copies };
        var result = await api(path, { method: "POST", headers: headers, body: JSON.stringify(payload) });
        status.textContent = "ส่งเข้าคิวแล้ว · " + (result.print_job_id || profile.printer_name);
        toast("ส่งพิมพ์ " + copies + " ใบ ไปที่ " + profile.name + " แล้ว");
        picker.classList.add("pb-hidden");
        pendingPrint = null;
        if (typeof window.pbFetchPrintQueue === "function") setTimeout(window.pbFetchPrintQueue, 500);
      } catch (error) {
        status.textContent = error.message + " · ตรวจคิวพิมพ์ก่อนส่งซ้ำ เพื่อป้องกันภาพซ้ำ";
        toast(error.message, 4500);
      } finally {
        sendingPrint = false;
        select.disabled = false;
        copiesInput.disabled = false;
        if (pendingPrint) {
          var currentProfile = app.printerProfiles.find(function (entry) { return entry.id === select.value; });
          var currentPrinter = currentProfile && app.printers.find(function (entry) { return entry.name === currentProfile.printer_name; });
          submit.disabled = !currentPrinter || !currentPrinter.online;
        }
      }
    };
    window.pbPrintItem = async function (id, copies) {
      if (document.documentElement.dataset.pbView !== "operator") return;
      pendingPrint = { id: id, copies: Math.max(1, Math.min(99, Number(copies) || 1)) };
      copiesInput.value = pendingPrint.copies;
      picker.classList.remove("pb-hidden");
      select.replaceChildren();
      status.textContent = "กำลังตรวจเครื่องพิมพ์…";
      submit.disabled = true;
      try {
        await loadPrinters({ rethrow: true });
        app.printerProfiles.forEach(function (profile) {
          var printer = app.printers.find(function (entry) { return entry.name === profile.printer_name; });
          var option = new Option(profile.name + " · " + profile.printer_name + (printer && printer.online ? " · พร้อม" : " · ไม่พร้อม"), profile.id);
          option.disabled = !printer || !printer.online;
          select.add(option);
        });
        var preferred = app.printerProfiles.find(function (profile) { return profile.id === app.activePrinterProfileId && app.printers.some(function (printer) { return printer.name === profile.printer_name && printer.online; }); });
        var firstReady = preferred || app.printerProfiles.find(function (profile) { return app.printers.some(function (printer) { return printer.name === profile.printer_name && printer.online; }); });
        if (firstReady) select.value = firstReady.id;
        select.onchange();
      } catch (error) { status.textContent = "ตรวจเครื่องพิมพ์ไม่ได้: " + error.message; }
    };
    window.pbPrintLatest = async function () {
      try {
        var data = await api("/api/processing/status");
        var id = data.jobmodel && data.jobmodel.present_mediaitem_id;
        if (!id && window.galleryItems && window.galleryItems[0]) id = window.galleryItems[0].id;
        if (!id) throw new Error("ยังไม่มีภาพสำหรับพิมพ์");
        window.pbPrintItem(id, window.finishedPrintQty || 1);
      } catch (error) { toast(error.message, 4200); }
    };
  }

  function addLaunchButtons() {
    var operatorActions = document.querySelector(".pb-workspace-actions");
    if (document.documentElement.dataset.pbView === "operator" && operatorActions && !document.getElementById("pb-import-studio-button")) {
      var importButton = document.createElement("button");
      importButton.id = "pb-import-studio-button";
      importButton.type = "button";
      importButton.className = "pb-import-studio-button";
      importButton.textContent = "＋ สร้างจากไฟล์";
      importButton.title = "อัปโหลดภาพเข้าเทมเพลตโดยไม่ถ่ายและไม่ผูกคิว";
      importButton.onclick = function () { window.pbOpenImportTemplateStudio(); };
      operatorActions.insertBefore(importButton, operatorActions.firstChild);
    }
    var finishedBar = document.getElementById("pb-finished-bar");
    if (finishedBar && !document.getElementById("pb-template-finished-button")) {
      var button = document.createElement("button");
      button.id = "pb-template-finished-button";
      button.className = "pb-btn pb-template-launch";
      button.type = "button";
      button.textContent = "จัดเทมเพลต";
      button.onclick = function () { window.pbOpenTemplateStudio(); };
      finishedBar.insertBefore(button, finishedBar.firstChild);
    }
    var detailActions = document.querySelector("#pb-gallery-detail-view .pb-modal-actions");
    if (detailActions && !document.getElementById("pb-template-gallery-button")) {
      var galleryButton = document.createElement("button");
      galleryButton.id = "pb-template-gallery-button";
      galleryButton.className = "pb-btn pb-template-launch";
      galleryButton.type = "button";
      galleryButton.textContent = "จัดเทมเพลต";
      galleryButton.onclick = function () { window.pbOpenTemplateStudio(window.currentGalleryItemId); };
      detailActions.insertBefore(galleryButton, detailActions.firstChild);
    }
  }

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (!document.getElementById("pb-template-modal").classList.contains("pb-hidden")) window.pbCloseTemplateStudio();
    ["pb-template-modal", "pb-template-editor-modal", "pb-hardware-modal"].forEach(function (id) {
      var modal = document.getElementById(id);
      if (modal) modal.classList.add("pb-hidden");
    });
  });

  createHardwareModal();
  createTemplateEditor();
  createStudioModal();
  injectControls();
  addLaunchButtons();
  installProfilePrinting();
  installCameraHealth();
  loadTemplates().catch(function (error) { toast(error.message); });
  var templateSyncInFlight = false;
  setInterval(function () {
    if (document.hidden || templateSyncInFlight) return;
    templateSyncInFlight = true;
    api("/api/share/templates?ts=" + Date.now()).then(function (data) {
      if (data.active_template_id !== app.activeTemplateId || (data.templates || []).length !== app.templates.length) loadTemplates();
    }).catch(function () {}).finally(function () { templateSyncInFlight = false; });
  }, 6000);
  if (document.documentElement.dataset.pbView === "operator") loadPrinters();
  improveAccessibility();
  translateCameraNotifications();
  new MutationObserver(function (records) {
    records.forEach(function (record) { record.addedNodes.forEach(function (node) { if (node.nodeType === 1) { improveAccessibility(node); translateCameraNotifications(node); } }); });
  }).observe(document.body, { childList: true, subtree: true });
})();
