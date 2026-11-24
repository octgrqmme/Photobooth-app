(function () {
  "use strict";

  var SCENE_ID = "pb-lk-scene";
  var STYLE_ID = "pb-lk-scene-style";
  var initialized = false;

  function ensureSceneStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent =
      ".pb-lk-scene{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden;user-select:none;}" +
      "html[data-pb-view=\"guest\"] #pb-lk-scene{display:none!important;}" +
      ".pb-lk-scene--river-ambient{opacity:0.82;}" +
      ".pb-lk-scene--parchment-river{opacity:0.48;}" +
      ".pb-lk-sky{position:absolute;top:0;left:0;width:100%;height:48vh;min-height:220px;display:block;}" +
      ".pb-lk-skyline{position:absolute;left:0;top:26vh;width:100%;height:22vh;min-height:110px;display:block;}" +
      ".pb-lk-river-canvas{position:absolute;left:0;top:46vh;width:100%;height:54vh;display:block;}" +
      ".pb-lk-foreground{position:absolute;left:0;bottom:0;width:100%;height:20vh;min-height:90px;display:block;}" +
      ".pb-lk-parchment-wrap{position:absolute;inset:0;width:100%;height:100%;display:block;}" +
      "body[data-lk-scene]>header{z-index:20;}" +
      "body[data-lk-scene]>header:not(.pb-admin-header):not(#pb-workspace-header){position:relative;z-index:2;}" +
      "body[data-lk-scene]>main:not(#pb-operator-layout),body[data-lk-scene]>footer,body[data-lk-scene]>.board-footer,body[data-lk-scene]>.container,body[data-lk-scene]>.stepper-wrap{position:relative;z-index:1;}" +
      "@keyframes pbLkStarTwinkle{0%,100%{opacity:0.35;}50%{opacity:0.92;}}" +
      "@keyframes pbLkLanternRise{0%,100%{transform:translateY(0px);opacity:0.78;}50%{transform:translateY(-10px);opacity:0.98;}}" +
      "@keyframes pbLkHangingSway{0%,100%{transform:rotate(-1.6deg);}50%{transform:rotate(1.6deg);}}" +
      "@keyframes pbLkParchmentBob{0%,100%{transform:translateY(0px);}50%{transform:translateY(-4px);}}" +
      ".pb-lk-star{animation:pbLkStarTwinkle 4.2s ease-in-out infinite;}" +
      ".pb-lk-sky-lantern{animation:pbLkLanternRise 7s ease-in-out infinite;}" +
      ".pb-lk-hanging{transform-box:fill-box;transform-origin:top center;}" +
      ".pb-lk-hang-a{animation:pbLkHangingSway 5.5s ease-in-out infinite;}" +
      ".pb-lk-hang-b{animation:pbLkHangingSway 6.8s ease-in-out infinite reverse;}" +
      ".pb-lk-float-a{animation:pbLkParchmentBob 4.8s ease-in-out infinite;}" +
      ".pb-lk-float-b{animation:pbLkParchmentBob 5.8s ease-in-out infinite reverse;}" +
      "@media (prefers-reduced-motion:reduce){.pb-lk-star,.pb-lk-sky-lantern,.pb-lk-hanging,.pb-lk-parchment-krathong{animation:none!important;}}";
    (document.head || document.documentElement).appendChild(style);
  }

  function shouldSkipScene() {
    var root = document.documentElement;
    if (!root || !document.body) return true;
    if (root.dataset && root.dataset.pbView === "guest") return true;
    return false;
  }

  function buildSkyMarkup() {
    var stars = [
      [8, 12, 1.4, 0.7], [16, 21, 1.1, 0.5], [24, 9, 1.6, 0.8],
      [35, 16, 1.2, 0.55], [44, 7, 1.5, 0.65], [56, 19, 1.2, 0.6],
      [63, 11, 1.7, 0.75], [71, 25, 1.1, 0.5], [89, 12, 1.5, 0.7],
      [94, 24, 1.2, 0.55], [12, 31, 1.0, 0.45], [51, 28, 1.3, 0.5]
    ].map(function (s, idx) {
      return '<circle class="pb-lk-star" style="animation-delay:' + (idx * 0.45).toFixed(2) + 's" cx="' + s[0] + '%" cy="' + s[1] + '%" r="' + s[2] + '" fill="#fef3c7" opacity="' + s[3] + '"/>';
    }).join("");

    var skyLanterns = [
      { x: 18, y: 24, scale: 0.75, delay: 0 },
      { x: 31, y: 15, scale: 0.55, delay: 1.6 },
      { x: 47, y: 22, scale: 0.65, delay: 3.1 },
      { x: 61, y: 16, scale: 0.85, delay: 0.9 },
      { x: 68, y: 27, scale: 0.6, delay: 2.4 },
      { x: 91, y: 29, scale: 0.7, delay: 1.2 }
    ].map(function (l) {
      var w = 14 * l.scale;
      var h = 19 * l.scale;
      return (
        '<g transform="translate(' + (l.x * 16) + ' ' + (l.y * 5) + ')">' +
          '<g class="pb-lk-sky-lantern" style="animation-delay:' + l.delay + 's">' +
            '<circle cx="0" cy="0" r="' + (w * 1.9) + '" fill="url(#pbLkLanternGlow)" opacity="0.72"/>' +
            '<path d="M' + (-w * 0.5) + ',' + (-h * 0.5) + ' Q0,' + (-h * 0.68) + ' ' + (w * 0.5) + ',' + (-h * 0.5) + ' L' + (w * 0.34) + ',' + (h * 0.45) + ' L' + (-w * 0.34) + ',' + (h * 0.45) + ' Z" fill="url(#pbLkLanternBody)" stroke="#fde68a" stroke-opacity="0.45" stroke-width="0.9"/>' +
            '<ellipse cx="0" cy="' + (h * 0.44) + '" rx="' + (w * 0.28) + '" ry="' + (w * 0.1) + '" fill="#fffbeb" opacity="0.95"/>' +
          '</g>' +
        '</g>'
      );
    }).join("");

    function hangingLantern(cx, topY, scale, cls) {
      var w = 22 * scale;
      var h = 34 * scale;
      return (
        '<g transform="translate(' + cx + ' ' + topY + ')">' +
          '<g class="pb-lk-hanging ' + cls + '">' +
            '<line x1="0" y1="0" x2="0" y2="' + (28 * scale) + '" stroke="#d97706" stroke-width="1.3" stroke-opacity="0.75"/>' +
            '<circle cx="0" cy="' + (28 * scale + h * 0.5) + '" r="' + (w * 2.1) + '" fill="url(#pbLkLanternGlow)" opacity="0.58"/>' +
            '<path d="M0,' + (28 * scale) + ' L' + (w * 0.6) + ',' + (28 * scale + h * 0.16) + ' L' + w + ',' + (28 * scale + h * 0.48) + ' L' + (w * 0.72) + ',' + (28 * scale + h * 0.85) + ' L0,' + (28 * scale + h) + ' L' + (-w * 0.72) + ',' + (28 * scale + h * 0.85) + ' L' + (-w) + ',' + (28 * scale + h * 0.48) + ' L' + (-w * 0.6) + ',' + (28 * scale + h * 0.16) + ' Z" fill="url(#pbLkLanternBody)" stroke="#fcd34d" stroke-width="1.2" stroke-opacity="0.78"/>' +
            '<line x1="' + (-w * 0.6) + '" y1="' + (28 * scale + h * 0.16) + '" x2="' + (w * 0.6) + '" y2="' + (28 * scale + h * 0.16) + '" stroke="#fde68a" stroke-opacity="0.55" stroke-width="0.9"/>' +
            '<line x1="' + (-w * 0.72) + '" y1="' + (28 * scale + h * 0.85) + '" x2="' + (w * 0.72) + '" y2="' + (28 * scale + h * 0.85) + '" stroke="#fde68a" stroke-opacity="0.55" stroke-width="0.9"/>' +
            '<line x1="0" y1="' + (28 * scale + h) + '" x2="0" y2="' + (28 * scale + h + 16 * scale) + '" stroke="#fbbf24" stroke-width="1.4" stroke-opacity="0.75"/>' +
            '<circle cx="0" cy="' + (28 * scale + h * 0.5) + '" r="' + (w * 0.28) + '" fill="#fffef2" opacity="0.9"/>' +
          '</g>' +
        '</g>'
      );
    }

    return (
      '<svg class="pb-lk-sky" viewBox="0 0 1600 500" preserveAspectRatio="xMidYMid slice">' +
        '<defs>' +
          '<linearGradient id="pbLkSkyGrad" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#0d0906"/>' +
            '<stop offset="56%" stop-color="#1e150c"/>' +
            '<stop offset="100%" stop-color="#3c2814"/>' +
          '</linearGradient>' +
          '<radialGradient id="pbLkMoonHalo" cx="50%" cy="50%" r="50%">' +
            '<stop offset="0%" stop-color="#fffef5" stop-opacity="0.95"/>' +
            '<stop offset="32%" stop-color="#fde68a" stop-opacity="0.46"/>' +
            '<stop offset="68%" stop-color="#f59e0b" stop-opacity="0.14"/>' +
            '<stop offset="100%" stop-color="#f59e0b" stop-opacity="0"/>' +
          '</radialGradient>' +
          '<radialGradient id="pbLkLanternGlow" cx="50%" cy="50%" r="50%">' +
            '<stop offset="0%" stop-color="#fde68a" stop-opacity="0.72"/>' +
            '<stop offset="52%" stop-color="#f59e0b" stop-opacity="0.22"/>' +
            '<stop offset="100%" stop-color="#f59e0b" stop-opacity="0"/>' +
          '</radialGradient>' +
          '<linearGradient id="pbLkLanternBody" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#f59e0b"/>' +
            '<stop offset="55%" stop-color="#fde68a"/>' +
            '<stop offset="100%" stop-color="#fffef5"/>' +
          '</linearGradient>' +
        '</defs>' +
        '<rect width="1600" height="500" fill="url(#pbLkSkyGrad)"/>' +
        stars +
        '<g class="pb-lk-moon-group">' +
          '<circle cx="1310" cy="145" r="180" fill="url(#pbLkMoonHalo)"/>' +
          '<circle cx="1310" cy="145" r="64" fill="#fffbeb" opacity="0.96"/>' +
          '<circle cx="1310" cy="145" r="59" fill="#fef9c3" opacity="0.45"/>' +
        '</g>' +
        skyLanterns +
        '<path d="M-20,18 Q210,72 440,8 M1120,12 Q1350,74 1620,20" fill="none" stroke="#92400e" stroke-opacity="0.55" stroke-width="1.8"/>' +
        hangingLantern(125, 34, 0.92, "pb-lk-hang-a") +
        hangingLantern(248, 43, 0.72, "pb-lk-hang-b") +
        hangingLantern(1210, 26, 0.76, "pb-lk-hang-b") +
        hangingLantern(1388, 38, 0.98, "pb-lk-hang-a") +
        hangingLantern(1515, 29, 0.72, "pb-lk-hang-b") +
      '</svg>'
    );
  }

  function buildSkylineMarkup() {
    var waterfrontCandles = [
      180, 260, 395, 510, 625, 975, 1090, 1210, 1340, 1435
    ].map(function (x) {
      return (
        '<g transform="translate(' + x + ' 206)">' +
          '<circle cx="0" cy="-4" r="16" fill="url(#pbLkHorizonLight)" opacity="0.72"/>' +
          '<rect x="-2.2" y="-3" width="4.4" height="10" rx="1.4" fill="#fde68a" opacity="0.9"/>' +
          '<ellipse cx="0" cy="-5" rx="2.6" ry="4" fill="#fffbeb"/>' +
        '</g>'
      );
    }).join("");

    var garlands = [
      [575, 152, 650, 166], [650, 166, 725, 142],
      [725, 142, 800, 126], [800, 126, 875, 142],
      [875, 142, 950, 166], [950, 166, 1025, 152]
    ].map(function (pts) {
      var mx = (pts[0] + pts[2]) / 2;
      var my = (pts[1] + pts[3]) / 2 + 10;
      return (
        '<path d="M' + pts[0] + ',' + pts[1] + ' Q' + mx + ',' + my + ' ' + pts[2] + ',' + pts[3] + '" fill="none" stroke="#f59e0b" stroke-width="1.2" stroke-opacity="0.8"/>' +
        '<circle cx="' + mx + '" cy="' + (my - 3) + '" r="3.4" fill="#fde68a"/>' +
        '<circle cx="' + ((pts[0] + mx) / 2) + '" cy="' + ((pts[1] + my) / 2 - 2) + '" r="2.3" fill="#fbbf24"/>' +
        '<circle cx="' + ((pts[2] + mx) / 2) + '" cy="' + ((pts[3] + my) / 2 - 2) + '" r="2.3" fill="#fbbf24"/>'
      );
    }).join("");

    return (
      '<svg class="pb-lk-skyline" viewBox="0 0 1600 230" preserveAspectRatio="xMidYMax slice">' +
        '<defs>' +
          '<linearGradient id="pbLkMist" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#3c2814" stop-opacity="0"/>' +
            '<stop offset="72%" stop-color="#784b1d" stop-opacity="0.28"/>' +
            '<stop offset="100%" stop-color="#b47b33" stop-opacity="0.48"/>' +
          '</linearGradient>' +
          '<linearGradient id="pbLkPavilionWhite" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#fffaf0"/>' +
            '<stop offset="58%" stop-color="#ede0c9"/>' +
            '<stop offset="100%" stop-color="#b99c72"/>' +
          '</linearGradient>' +
          '<linearGradient id="pbLkSilhouette" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#5e4731"/>' +
            '<stop offset="100%" stop-color="#2c1e12"/>' +
          '</linearGradient>' +
          '<radialGradient id="pbLkHorizonLight" cx="50%" cy="50%" r="50%">' +
            '<stop offset="0%" stop-color="#fde68a" stop-opacity="0.85"/>' +
            '<stop offset="100%" stop-color="#f59e0b" stop-opacity="0"/>' +
          '</radialGradient>' +
        '</defs>' +
        '<rect x="0" y="95" width="1600" height="135" fill="url(#pbLkMist)"/>' +
        /* Distant Temple Spires & Mountain Contour */
        '<path d="M0,214 L0,185 Q240,152 520,178 Q800,148 1080,176 Q1360,154 1600,182 L1600,214 Z" fill="#2b1e13" opacity="0.65"/>' +
        '<g opacity="0.58" fill="url(#pbLkSilhouette)">' +
          '<polygon points="455,212 470,124 485,212"/>' +
          '<polygon points="620,212 634,116 648,212"/>' +
          '<polygon points="790,212 800,90 810,212"/>' +
          '<polygon points="950,212 964,116 978,212"/>' +
          '<polygon points="1115,212 1130,124 1145,212"/>' +
          '<path d="M510,212 L515,152 L542,135 L569,152 L574,212 Z"/>' +
          '<path d="M1026,212 L1031,152 L1058,135 L1085,152 L1090,212 Z"/>' +
        '</g>' +
        /* Palm Tree Silhouettes */
        '<g stroke="#3d2c1b" fill="none" stroke-linecap="round" opacity="0.72">' +
          '<path d="M405,210 Q402,174 396,154" stroke-width="3"/>' +
          '<path d="M396,154 Q378,144 368,158 M396,154 Q382,136 376,138 M396,154 Q414,142 424,158 M396,154 Q412,136 418,140" stroke-width="1.6"/>' +
          '<path d="M1195,210 Q1198,174 1204,154" stroke-width="3"/>' +
          '<path d="M1204,154 Q1186,142 1176,158 M1204,154 Q1188,136 1182,140 M1204,154 Q1222,144 1232,158 M1204,154 Q1218,136 1224,138" stroke-width="1.6"/>' +
        '</g>' +
        /* Left & Right Riverside Pavilions and Pagodas */
        '<path d="M70,214 L70,184 L126,156 L184,184 L184,214 Z" fill="url(#pbLkPavilionWhite)"/>' +
        '<path d="M188,214 L188,176 L235,146 L282,176 L282,214 Z" fill="#d9c6a9"/>' +
        '<path d="M294,214 L299,145 Q318,125 337,145 L342,214 Z" fill="url(#pbLkPavilionWhite)"/>' +
        '<polygon points="315,132 318,74 321,132" fill="#fffaf0"/>' +
        '<path d="M430,214 L436,156 L456,120 L476,156 L482,214 Z" fill="#e5d5ba"/>' +
        '<path d="M1118,214 L1124,156 L1144,120 L1164,156 L1170,214 Z" fill="#e5d5ba"/>' +
        '<path d="M1258,214 L1263,145 Q1282,125 1301,145 L1306,214 Z" fill="url(#pbLkPavilionWhite)"/>' +
        '<polygon points="1279,132 1282,74 1285,132" fill="#fffaf0"/>' +
        '<path d="M1318,214 L1318,176 L1365,146 L1412,176 L1412,214 Z" fill="#d9c6a9"/>' +
        '<path d="M1416,214 L1416,184 L1472,156 L1530,184 L1530,214 Z" fill="url(#pbLkPavilionWhite)"/>' +
        /* PSU Science Iconic Pumpkin Building ("ตึกฟักทอง") Dome */
        '<path d="M510,214 L510,180 Q530,152 575,148 Q610,132 650,138 Q690,118 725,132 Q765,108 800,118 Q835,108 875,132 Q910,118 950,138 Q990,132 1025,148 Q1070,152 1090,180 L1090,214 Z" fill="url(#pbLkPavilionWhite)"/>' +
        /* Recessed Pumpkin Building Arches */
        '<path d="M540,214 Q572,172 604,214 Z" fill="#1b120b"/>' +
        '<path d="M618,214 Q656,164 694,214 Z" fill="#170f09"/>' +
        '<path d="M708,214 Q748,158 788,214 Z" fill="#140c07"/>' +
        '<path d="M812,214 Q852,158 892,214 Z" fill="#140c07"/>' +
        '<path d="M906,214 Q944,164 982,214 Z" fill="#170f09"/>' +
        '<path d="M996,214 Q1028,172 1060,214 Z" fill="#1b120b"/>' +
        /* Subtle Dome Rib Shading */
        '<path d="M650,138 Q656,164 656,182 M725,132 Q732,158 732,176 M800,118 L800,174 M875,132 Q868,158 868,176 M950,138 Q944,164 944,182" stroke="#cbb592" stroke-opacity="0.45" stroke-width="1.2" fill="none"/>' +
        garlands +
        /* Waterfront Promenade Line & Candles */
        '<rect x="0" y="212" width="1600" height="18" fill="#1a1109"/>' +
        waterfrontCandles +
      '</svg>'
    );
  }

  function buildForegroundMarkup() {
    return (
      '<svg class="pb-lk-foreground" viewBox="0 0 1600 180" preserveAspectRatio="xMidYMax slice">' +
        /* Left lotus pads & reed stalk */
        '<g opacity="0.82">' +
          '<ellipse cx="90" cy="176" rx="145" ry="42" fill="#23160c" stroke="#92400e" stroke-opacity="0.6" stroke-width="1.4"/>' +
          '<ellipse cx="265" cy="186" rx="95" ry="30" fill="#1d120a" stroke="#b45309" stroke-opacity="0.45" stroke-width="1.2"/>' +
          '<path d="M210,178 L210,56" stroke="#b45309" stroke-width="2" stroke-opacity="0.72"/>' +
          '<ellipse cx="210" cy="54" rx="5.5" ry="16" fill="#5b3413" stroke="#d97706" stroke-width="1.1"/>' +
          '<circle cx="210" cy="34" r="3.5" fill="#fde68a" opacity="0.85"/>' +
        '</g>' +
        /* Right lotus pads & open lotus silhouette */
        '<g opacity="0.82">' +
          '<ellipse cx="1480" cy="178" rx="150" ry="44" fill="#23160c" stroke="#92400e" stroke-opacity="0.6" stroke-width="1.4"/>' +
          '<ellipse cx="1310" cy="186" rx="95" ry="30" fill="#1d120a" stroke="#b45309" stroke-opacity="0.45" stroke-width="1.2"/>' +
          '<g transform="translate(1332 104)">' +
            '<circle cx="0" cy="0" r="30" fill="#18100a" stroke="#d97706" stroke-opacity="0.65" stroke-width="1.4"/>' +
            '<path d="M-16,10 Q0,-26 16,10 Q0,18 -16,10 Z" fill="#452b15" stroke="#fcd34d" stroke-opacity="0.7" stroke-width="1.1"/>' +
            '<circle cx="0" cy="-2" r="4" fill="#fffbeb"/>' +
          '</g>' +
        '</g>' +
      '</svg>'
    );
  }

  function buildParchmentMarkup() {
    function miniLantern(x, y, scale) {
      return (
        '<g transform="translate(' + x + ' ' + y + ') scale(' + scale + ')">' +
          '<line x1="0" y1="-26" x2="0" y2="0" stroke="#92400e" stroke-width="1.4"/>' +
          '<path d="M0,0 L14,8 L20,24 L14,40 L0,48 L-14,40 L-20,24 L-14,8 Z" fill="#fde68a" fill-opacity="0.55" stroke="#b45309" stroke-width="1.4"/>' +
          '<line x1="-14" y1="8" x2="14" y2="8" stroke="#b45309" stroke-opacity="0.65" stroke-width="1"/>' +
          '<line x1="-14" y1="40" x2="14" y2="40" stroke="#b45309" stroke-opacity="0.65" stroke-width="1"/>' +
          '<line x1="0" y1="48" x2="0" y2="64" stroke="#b45309" stroke-width="1.4"/>' +
        '</g>'
      );
    }

    function miniKrathong(x, y, scale, cls) {
      return (
        '<g transform="translate(' + x + ' ' + y + ') scale(' + scale + ')">' +
          '<g class="pb-lk-parchment-krathong ' + cls + '">' +
            '<ellipse cx="0" cy="18" rx="44" ry="8" fill="none" stroke="#d97706" stroke-opacity="0.45" stroke-width="1.2"/>' +
            '<ellipse cx="0" cy="14" rx="31" ry="9" fill="#fef3c7" stroke="#92400e" stroke-width="1.4"/>' +
            '<path d="M-28,12 L-22,2 L-14,11 L-6,1 L0,11 L6,1 L14,11 L22,2 L28,12 Z" fill="#d97706" fill-opacity="0.32" stroke="#78350f" stroke-width="1.2"/>' +
            '<rect x="-3" y="-12" width="6" height="19" rx="2" fill="#fffbeb" stroke="#92400e" stroke-width="1.2"/>' +
            '<path d="M0,-22 Q5,-16 0,-12 Q-5,-16 0,-22 Z" fill="#f59e0b"/>' +
          '</g>' +
        '</g>'
      );
    }

    return (
      '<svg class="pb-lk-parchment-wrap" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice">' +
        '<defs>' +
          '<linearGradient id="pbLkParchmentRiver" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#f59e0b" stop-opacity="0"/>' +
            '<stop offset="45%" stop-color="#d97706" stop-opacity="0.14"/>' +
            '<stop offset="100%" stop-color="#92400e" stop-opacity="0.24"/>' +
          '</linearGradient>' +
        '</defs>' +
        miniLantern(86, 108, 0.9) +
        miniLantern(1514, 108, 0.9) +
        '<path d="M0,715 Q260,685 540,715 T1080,715 T1600,705 L1600,900 L0,900 Z" fill="url(#pbLkParchmentRiver)"/>' +
        '<path class="pb-lk-wave-a" d="M-40,745 Q220,722 480,745 T1020,745 T1640,740" fill="none" stroke="#b45309" stroke-opacity="0.28" stroke-width="1.8"/>' +
        '<path class="pb-lk-wave-b" d="M-40,788 Q280,768 560,788 T1120,788 T1640,782" fill="none" stroke="#d97706" stroke-opacity="0.26" stroke-width="1.5"/>' +
        '<path class="pb-lk-wave-a" d="M-40,835 Q260,814 540,835 T1080,835 T1640,832" fill="none" stroke="#92400e" stroke-opacity="0.22" stroke-width="1.6"/>' +
        miniKrathong(185, 812, 0.85, "pb-lk-float-a") +
        miniKrathong(540, 772, 0.62, "pb-lk-float-b") +
        miniKrathong(1090, 785, 0.68, "pb-lk-float-a") +
        miniKrathong(1415, 825, 0.9, "pb-lk-float-b") +
      '</svg>'
    );
  }

  function startRiverCanvas(canvas, mode) {
    var ctx = canvas.getContext("2d");
    if (!ctx) return;

    var isFull = mode === "river-full";
    var krathongCount = isFull ? 14 : 8;
    var petalCount = isFull ? 18 : 11;
    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var krathongs = [];
    for (var i = 0; i < krathongCount; i++) {
      var depth = 0.12 + i / (krathongCount - 1) * 0.79;
      krathongs.push({
        u: ((i + 0.35) / krathongCount + (i % 2 === 0 ? 0.04 : -0.03)) % 1,
        v: depth,
        speed: (0.0085 + (1 - depth) * 0.006) * (i % 2 === 0 ? 1 : 0.82),
        bobPhase: i * 1.37,
        variant: i % 3
      });
    }

    var petalColors = ["#f472b6", "#fb923c", "#fde047", "#fef3c7", "#fb7185"];
    var petals = [];
    for (var p = 0; p < petalCount; p++) {
      petals.push({
        u: (p * 0.173) % 1,
        v: 0.14 + ((p * 0.29) % 0.8),
        speed: 0.011 + (p % 4) * 0.003,
        rx: 3 + (p % 3) * 1.4,
        ry: 1.5 + (p % 2) * 0.7,
        color: petalColors[p % petalColors.length],
        phase: p * 0.9
      });
    }

    var width = 0;
    var height = 0;
    var dpr = 1;

    function resize() {
      var rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      width = Math.max(320, Math.round(rect.width || window.innerWidth));
      height = Math.max(180, Math.round(rect.height || window.innerHeight * 0.56));
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function drawKrathong(k, t) {
      var x = k.u * (width + 180) - 90;
      var bob = Math.sin(t * 1.8 + k.bobPhase) * (1.8 + k.v * 2.2);
      var y = k.v * height + bob;
      var scale = 0.42 + k.v * 0.62;
      var flicker = 0.9 + 0.12 * Math.sin(t * 9.5 + k.bobPhase * 2);

      ctx.save();
      ctx.translate(x, y);
      ctx.scale(scale, scale);

      /* Water shimmer reflection under krathong */
      var refl = ctx.createRadialGradient(0, 14, 2, 0, 14, 42);
      refl.addColorStop(0, "rgba(253, 230, 138, 0.42)");
      refl.addColorStop(0.5, "rgba(245, 158, 11, 0.16)");
      refl.addColorStop(1, "rgba(245, 158, 11, 0)");
      ctx.fillStyle = refl;
      ctx.beginPath();
      ctx.ellipse(0, 14, 42, 13, 0, 0, Math.PI * 2);
      ctx.fill();

      /* Expanding ripple ring */
      var pulse = ((t * 0.35 + k.bobPhase * 0.2) % 1);
      ctx.strokeStyle = "rgba(253, 224, 71, " + ((1 - pulse) * 0.28).toFixed(3) + ")";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(0, 8, 28 + pulse * 26, 7 + pulse * 7, 0, 0, Math.PI * 2);
      ctx.stroke();

      /* Warm candlelight halo behind flowers */
      var halo = ctx.createRadialGradient(0, -24, 2, 0, -24, 52 * flicker);
      halo.addColorStop(0, "rgba(255, 251, 235, 0.72)");
      halo.addColorStop(0.35, "rgba(251, 191, 36, 0.32)");
      halo.addColorStop(1, "rgba(245, 158, 11, 0)");
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(0, -24, 52 * flicker, 0, Math.PI * 2);
      ctx.fill();

      /* Banana-leaf disk base & shadow */
      ctx.fillStyle = "rgba(12, 8, 4, 0.55)";
      ctx.beginPath();
      ctx.ellipse(0, 8, 30, 10, 0, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#4d7c0f";
      ctx.beginPath();
      ctx.ellipse(0, 4, 29, 9.5, 0, 0, Math.PI * 2);
      ctx.fill();

      /* Pleated green banana leaf triangles along rim */
      var peaks = 9;
      for (var p = 0; p < peaks; p++) {
        var frac = p / (peaks - 1);
        var px = -26 + frac * 52;
        var py = 4 + Math.sin(frac * Math.PI) * 5;
        ctx.fillStyle = p % 2 === 0 ? "#84cc16" : "#65a30d";
        ctx.beginPath();
        ctx.moveTo(px - 4.2, py);
        ctx.lineTo(px, py - 11);
        ctx.lineTo(px + 4.2, py);
        ctx.closePath();
        ctx.fill();
      }

      /* Marigold / lotus flower clusters */
      ctx.fillStyle = k.variant === 1 ? "#fb7185" : "#fb923c";
      ctx.beginPath();
      ctx.arc(-11, 0, 6.2, 0, Math.PI * 2);
      ctx.arc(11, 0, 5.8, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#ea580c";
      ctx.beginPath();
      ctx.arc(-11, 0, 2.8, 0, Math.PI * 2);
      ctx.arc(11, 0, 2.6, 0, Math.PI * 2);
      ctx.fill();

      /* Pink lotus bud */
      ctx.fillStyle = k.variant === 2 ? "#f43f5e" : "#f472b6";
      ctx.beginPath();
      ctx.moveTo(-6, 1);
      ctx.quadraticCurveTo(0, -14, 6, 1);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = "#fbcfe8";
      ctx.beginPath();
      ctx.moveTo(-3.2, 1);
      ctx.quadraticCurveTo(0, -10, 3.2, 1);
      ctx.closePath();
      ctx.fill();

      /* Three incense sticks */
      ctx.strokeStyle = "#b45309";
      ctx.lineWidth = 1.2;
      [-4, 0, 4].forEach(function (dx, idx) {
        ctx.beginPath();
        ctx.moveTo(dx * 0.6, -2);
        ctx.lineTo(dx * 1.4, -22 - (idx === 1 ? 3 : 0));
        ctx.stroke();
        ctx.fillStyle = "#fb7185";
        ctx.beginPath();
        ctx.arc(dx * 1.4, -22.5 - (idx === 1 ? 3 : 0), 1.3, 0, Math.PI * 2);
        ctx.fill();
      });

      /* Ivory candle + flickering golden flame */
      ctx.fillStyle = "#fef3c7";
      ctx.fillRect(-2.4, -18, 4.8, 15);
      ctx.fillStyle = "#fbbf24";
      ctx.beginPath();
      ctx.moveTo(0, -29 * flicker);
      ctx.quadraticCurveTo(4.2, -21, 0, -17.5);
      ctx.quadraticCurveTo(-4.2, -21, 0, -29 * flicker);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.ellipse(0, -21.5, 1.5, 3.2 * flicker, 0, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    }

    function renderFrame(t) {
      /* Rich nocturnal golden-bronze river surface */
      var riverGrad = ctx.createLinearGradient(0, 0, 0, height);
      riverGrad.addColorStop(0, "#1c130a");
      riverGrad.addColorStop(0.18, "#3e2b15");
      riverGrad.addColorStop(0.5, "#6b4c24");
      riverGrad.addColorStop(0.82, "#463117");
      riverGrad.addColorStop(1, "#181009");
      ctx.fillStyle = riverGrad;
      ctx.fillRect(0, 0, width, height);

      /* Broad moonlit reflection column under top-right moon (~82% X) */
      var moonX = width * 0.81;
      var moonRefl = ctx.createRadialGradient(moonX, height * 0.25, 10, moonX, height * 0.45, width * 0.48);
      moonRefl.addColorStop(0, "rgba(253, 230, 138, 0.32)");
      moonRefl.addColorStop(0.45, "rgba(217, 119, 6, 0.16)");
      moonRefl.addColorStop(1, "rgba(217, 119, 6, 0)");
      ctx.fillStyle = moonRefl;
      ctx.fillRect(0, 0, width, height);

      /* Horizontal shimmering water ripple bands */
      var rows = isFull ? 42 : 30;
      for (var r = 0; r < rows; r++) {
        var ratio = r / rows;
        var y = Math.pow(ratio, 1.32) * height;
        var bandH = 2.2 + ratio * 7.5;
        var waveShift = Math.sin(t * 0.85 + r * 0.65) * (24 + ratio * 45);
        var darkAlpha = 0.16 + Math.abs(Math.sin(r * 1.1 + t * 0.6)) * 0.22;
        ctx.fillStyle = "rgba(18, 12, 6, " + darkAlpha.toFixed(3) + ")";
        ctx.fillRect(0, y, width, bandH * 0.72);

        /* Moon-gold glint segments along ripple crests */
        var glintWidth = (90 + ratio * 210);
        var gx1 = (moonX - glintWidth * 0.5) + waveShift * 0.6;
        var shimmerGrad = ctx.createLinearGradient(gx1 - 60, y, gx1 + glintWidth + 60, y);
        shimmerGrad.addColorStop(0, "rgba(253, 230, 138, 0)");
        shimmerGrad.addColorStop(0.5, "rgba(254, 243, 199, " + (0.08 + ratio * 0.16).toFixed(3) + ")");
        shimmerGrad.addColorStop(1, "rgba(253, 230, 138, 0)");
        ctx.fillStyle = shimmerGrad;
        ctx.fillRect(gx1 - 60, y + 1, glintWidth + 120, Math.max(1.4, bandH * 0.26));
      }

      /* Floating flower petals */
      for (var p = 0; p < petals.length; p++) {
        var pt = petals[p];
        var px = ((pt.u + t * pt.speed * 0.06) % 1.08 - 0.04) * width;
        var py = pt.v * height + Math.sin(t * 1.5 + pt.phase) * 2.4;
        ctx.fillStyle = pt.color;
        ctx.globalAlpha = 0.78;
        ctx.beginPath();
        ctx.ellipse(px, py, pt.rx, pt.ry, Math.sin(pt.phase) * 0.35, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      /* Floating glowing krathongs */
      for (var k = 0; k < krathongs.length; k++) {
        if (!reducedMotion) {
          krathongs[k].u = ((k + 0.35) / krathongCount + t * krathongs[k].speed * 1.5) % 1;
        }
        drawKrathong(krathongs[k], t);
      }
    }

    resize();
    window.addEventListener("resize", resize);
    renderFrame(1.4);
    if (reducedMotion) return;

    var rafId = null;
    var lastPaint = 0;
    function tick(now) {
      rafId = window.requestAnimationFrame(tick);
      if (document.hidden) return;
      if (now - lastPaint < 32) return;
      lastPaint = now;
      renderFrame(now * 0.001);
    }
    rafId = window.requestAnimationFrame(tick);

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && !rafId) {
        rafId = window.requestAnimationFrame(tick);
      }
    });
  }

  function initLoyKrathongScene() {
    if (initialized || shouldSkipScene()) return;
    if (document.getElementById(SCENE_ID)) return;

    ensureSceneStyle();
    var mode = document.body.getAttribute("data-lk-scene") || "river-ambient";
    initialized = true;

    var container = document.createElement("div");
    container.id = SCENE_ID;
    container.className = "pb-lk-scene pb-lk-scene--" + mode;
    container.setAttribute("aria-hidden", "true");

    if (mode === "parchment-river") {
      container.innerHTML = buildParchmentMarkup();
      document.body.insertBefore(container, document.body.firstChild);
      return;
    }

    container.innerHTML = buildSkyMarkup() + buildSkylineMarkup() + '<canvas class="pb-lk-river-canvas"></canvas>' + buildForegroundMarkup()
      + '<div class="pb-lk-fireworks"><i></i><i></i><i></i></div>';
    document.body.insertBefore(container, document.body.firstChild);
    var front = document.createElement("div");
    front.className = "pb-lk-front";
    front.setAttribute("aria-hidden", "true");
    front.innerHTML = '<div class="pb-lk-fireworks"><i></i><i></i><i></i><i></i></div>'
      + '<img src="/assets/pb-loy-krathong.webp" alt=""><img src="/assets/pb-loy-krathong.webp" alt=""><img src="/assets/pb-loy-krathong.webp" alt="">';
    document.body.appendChild(front);

    var canvas = container.querySelector(".pb-lk-river-canvas");
    if (canvas) startRiverCanvas(canvas, mode);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLoyKrathongScene);
  } else {
    initLoyKrathongScene();
  }
})();
