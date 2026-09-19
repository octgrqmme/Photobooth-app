/**
 * Photo strip and collage canvas renderer
 * Compiles individual camera shots into professional printable templates
 */

// Helper to load image from dataUrl into an HTMLImageElement
export function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = (e) => reject(e);
    img.src = src;
  });
}

/**
 * Apply image filter on a 2D canvas context
 */
export function applyFilter(ctx, filterType) {
  switch (filterType) {
    case 'bw':
      ctx.filter = 'grayscale(100%) contrast(115%) brightness(105%)';
      break;
    case 'sepia':
      ctx.filter = 'sepia(70%) contrast(105%) brightness(102%) saturate(120%)';
      break;
    case 'warm':
      ctx.filter = 'sepia(30%) saturate(130%) brightness(105%) contrast(105%)';
      break;
    case 'cool':
      ctx.filter = 'saturate(90%) hue-rotate(190deg) contrast(110%) brightness(102%)';
      break;
    case 'glamour':
      ctx.filter = 'brightness(110%) contrast(105%) saturate(110%)';
      break;
    case 'vivid':
      ctx.filter = 'saturate(150%) contrast(110%) brightness(102%)';
      break;
    case 'none':
    default:
      ctx.filter = 'none';
      break;
  }
}

/**
 * Draw image with 'cover' aspect ratio into given destination rectangle
 */
function drawImageCover(ctx, img, dx, dy, dWidth, dHeight, radius = 0) {
  ctx.save();
  if (radius > 0) {
    ctx.beginPath();
    ctx.roundRect(dx, dy, dWidth, dHeight, radius);
    ctx.clip();
  }

  const imgRatio = img.width / img.height;
  const targetRatio = dWidth / dHeight;

  let sx, sy, sWidth, sHeight;

  if (imgRatio > targetRatio) {
    sHeight = img.height;
    sWidth = img.height * targetRatio;
    sx = (img.width - sWidth) / 2;
    sy = 0;
  } else {
    sWidth = img.width;
    sHeight = img.width / targetRatio;
    sx = 0;
    sy = (img.height - sHeight) / 2;
  }

  ctx.drawImage(img, sx, sy, sWidth, sHeight, dx, dy, dWidth, dHeight);
  ctx.restore();
}

/**
 * Render complete Photobooth strip / collage
 * @param {Array<string>} captures - Array of base64 image data URLs
 * @param {string} layout - 'single' | 'strip3' | 'strip4' | 'dual' | 'grid'
 * @param {string} filter - 'none' | 'bw' | 'sepia' | 'warm' | 'cool' | 'glamour' | 'vivid'
 * @param {object} frameConfig - { backgroundColor, textColor, title, subtitle, date, showDottedLine }
 * @returns {Promise<string>} Base64 dataUrl of rendered high-res image
 */
export async function renderPhotoStrip(captures, layout = 'strip4', filter = 'none', frameConfig = {}) {
  const {
    backgroundColor = '#ffffff',
    textColor = '#1e293b',
    title = 'PHOTOBOOTH MEMORY',
    subtitle = 'BANGKOK, THAILAND',
    date = new Date().toLocaleDateString('th-TH', { year: 'numeric', month: 'short', day: 'numeric' }),
    showDottedLine = true
  } = frameConfig;

  // Load all images
  const loadedImages = await Promise.all(captures.map(src => loadImage(src)));
  if (loadedImages.length === 0) throw new Error('No captures provided');

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { alpha: false });

  if (layout === 'strip3' || layout === 'strip4') {
    // Standard 2x6" strip format (600 x 1800 px)
    const W = 600;
    const H = 1800;
    canvas.width = W;
    canvas.height = H;

    // Fill background
    ctx.fillStyle = backgroundColor;
    ctx.fillRect(0, 0, W, H);

    const count = layout === 'strip3' ? 3 : 4;
    const marginX = 40;
    const marginTop = 45;
    const gap = 20;
    const footerHeight = layout === 'strip3' ? 240 : 180;
    const availableH = H - marginTop - footerHeight - (gap * (count - 1));
    const photoH = availableH / count;
    const photoW = W - (marginX * 2);

    // Draw photos
    for (let i = 0; i < count; i++) {
      const img = loadedImages[i % loadedImages.length];
      const y = marginTop + i * (photoH + gap);

      ctx.save();
      applyFilter(ctx, filter);
      drawImageCover(ctx, img, marginX, y, photoW, photoH, 12);
      ctx.restore();

      // Subtle photo border
      ctx.strokeStyle = 'rgba(0,0,0,0.06)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.roundRect(marginX, y, photoW, photoH, 12);
      ctx.stroke();
    }

    // Draw Footer Text & Branding
    drawFooter(ctx, W / 2, H - (footerHeight / 2) - 10, {
      title,
      subtitle,
      date,
      textColor,
      compact: layout === 'strip4'
    });

  } else if (layout === 'dual') {
    // 4x6" Paper containing 2 identical 4-photo strips side-by-side (1200 x 1800 px)
    const W = 1200;
    const H = 1800;
    canvas.width = W;
    canvas.height = H;

    // Fill background
    ctx.fillStyle = backgroundColor;
    ctx.fillRect(0, 0, W, H);

    const stripW = W / 2; // 600 px each
    const count = 4;
    const marginX = 36;
    const marginTop = 45;
    const gap = 20;
    const footerHeight = 180;
    const availableH = H - marginTop - footerHeight - (gap * (count - 1));
    const photoH = availableH / count;
    const photoW = stripW - (marginX * 2);

    // Draw Left and Right strip copies
    [0, stripW].forEach((offsetX) => {
      for (let i = 0; i < count; i++) {
        const img = loadedImages[i % loadedImages.length];
        const x = offsetX + marginX;
        const y = marginTop + i * (photoH + gap);

        ctx.save();
        applyFilter(ctx, filter);
        drawImageCover(ctx, img, x, y, photoW, photoH, 12);
        ctx.restore();

        ctx.strokeStyle = 'rgba(0,0,0,0.06)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.roundRect(x, y, photoW, photoH, 12);
        ctx.stroke();
      }

      // Footer branding on each strip
      drawFooter(ctx, offsetX + (stripW / 2), H - (footerHeight / 2) - 10, {
        title,
        subtitle,
        date,
        textColor,
        compact: true
      });
    });

    // Draw center dotted scissor cutting line
    if (showDottedLine) {
      ctx.save();
      ctx.strokeStyle = 'rgba(100, 116, 139, 0.4)';
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 8]);
      ctx.beginPath();
      ctx.moveTo(stripW, 20);
      ctx.lineTo(stripW, H - 20);
      ctx.stroke();

      // Scissor icon indicator at top and bottom
      ctx.fillStyle = 'rgba(100, 116, 139, 0.6)';
      ctx.font = '20px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('✂️', stripW, 35);
      ctx.fillText('✂️', stripW, H - 25);
      ctx.restore();
    }

  } else if (layout === 'grid') {
    // 2x2 Grid Collage (1200 x 1500 px)
    const W = 1200;
    const H = 1500;
    canvas.width = W;
    canvas.height = H;

    ctx.fillStyle = backgroundColor;
    ctx.fillRect(0, 0, W, H);

    const marginX = 50;
    const marginTop = 50;
    const gap = 24;
    const footerH = 200;
    const gridW = W - (marginX * 2);
    const photoW = (gridW - gap) / 2;
    const photoH = photoW * 0.78; // proportional landscape cell

    for (let r = 0; r < 2; r++) {
      for (let c = 0; c < 2; c++) {
        const idx = r * 2 + c;
        const img = loadedImages[idx % loadedImages.length];
        const x = marginX + c * (photoW + gap);
        const y = marginTop + r * (photoH + gap);

        ctx.save();
        applyFilter(ctx, filter);
        drawImageCover(ctx, img, x, y, photoW, photoH, 16);
        ctx.restore();

        ctx.strokeStyle = 'rgba(0,0,0,0.06)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.roundRect(x, y, photoW, photoH, 16);
        ctx.stroke();
      }
    }

    // Grid footer
    drawFooter(ctx, W / 2, H - (footerH / 2) + 20, {
      title,
      subtitle,
      date,
      textColor,
      compact: false
    });

  } else {
    // Single Photo (Polaroid / Classic 1-photo frame) 1200 x 1600 px
    const W = 1200;
    const H = 1600;
    canvas.width = W;
    canvas.height = H;

    ctx.fillStyle = backgroundColor;
    ctx.fillRect(0, 0, W, H);

    const marginX = 60;
    const marginTop = 60;
    const photoW = W - (marginX * 2);
    const photoH = 1240;
    const img = loadedImages[0];

    ctx.save();
    applyFilter(ctx, filter);
    drawImageCover(ctx, img, marginX, marginTop, photoW, photoH, 16);
    ctx.restore();

    ctx.strokeStyle = 'rgba(0,0,0,0.06)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(marginX, marginTop, photoW, photoH, 16);
    ctx.stroke();

    // Footer
    drawFooter(ctx, W / 2, H - 120, {
      title,
      subtitle,
      date,
      textColor,
      compact: false
    });
  }

  return canvas.toDataURL('image/png', 0.95);
}

/**
 * Draw stylish footer branding on canvas
 */
function drawFooter(ctx, centerX, centerY, { title, subtitle, date, textColor, compact }) {
  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = textColor;

  if (compact) {
    // Compact typography for 4-shot strips
    ctx.font = '700 24px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.letterSpacing = '2px';
    ctx.fillText(title.toUpperCase(), centerX, centerY - 16);

    ctx.font = '500 15px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.letterSpacing = '1px';
    ctx.fillStyle = textColor + 'b3'; // 70% opacity
    ctx.fillText(`${subtitle}  •  ${date}`, centerX, centerY + 18);
  } else {
    // Standard larger typography
    ctx.font = '800 36px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.letterSpacing = '3px';
    ctx.fillText(title.toUpperCase(), centerX, centerY - 28);

    ctx.font = '600 20px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.letterSpacing = '1px';
    ctx.fillStyle = textColor + 'bf';
    ctx.fillText(subtitle, centerX, centerY + 12);

    ctx.font = '500 16px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.fillStyle = textColor + '99';
    ctx.fillText(date, centerX, centerY + 42);
  }

  ctx.restore();
}
