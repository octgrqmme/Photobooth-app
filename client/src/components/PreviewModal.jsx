import React, { useState } from 'react';
import { Printer, QrCode, Download, RotateCcw, Check, Sparkles, AlertCircle, Copy } from 'lucide-react';
import { sounds } from '../utils/soundEffects';

export default function PreviewModal({
  isOpen,
  renderedImage,
  onFilterChange,
  activeFilter,
  onFrameColorChange,
  activeFrameColor,
  onPrintDirect,
  isPrinting,
  printSuccess,
  printError,
  onOpenQRCode,
  onRetake,
  printerName,
  t
}) {
  const [copies, setCopies] = useState(1);

  if (!isOpen || !renderedImage) return null;

  const filters = [
    { id: 'none', label: t.filters.none },
    { id: 'bw', label: t.filters.bw },
    { id: 'sepia', label: t.filters.sepia },
    { id: 'warm', label: t.filters.warm },
    { id: 'cool', label: t.filters.cool },
    { id: 'glamour', label: t.filters.glamour },
    { id: 'vivid', label: t.filters.vivid },
  ];

  const frameColors = [
    { color: '#ffffff', label: 'White' },
    { color: '#0f172a', label: 'Dark' },
    { color: '#fbcfe8', label: 'Pink' },
    { color: '#fef3c7', label: 'Cream' },
    { color: '#ddd6fe', label: 'Purple' },
    { color: '#ccfbf1', label: 'Mint' },
  ];

  const handleDownload = () => {
    const link = document.createElement('a');
    link.download = `photobooth_${Date.now()}.png`;
    link.href = renderedImage;
    link.click();
  };

  const handleBrowserPrint = () => {
    window.print();
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/90 backdrop-blur-md p-4 overflow-y-auto">
      <div className="relative max-w-5xl w-full flex flex-col lg:flex-row items-center justify-center gap-6 py-6">
        {/* Left/Center: Photo Strip Display */}
        <div className="flex-1 flex items-center justify-center max-h-[75vh] w-full">
          <div className="relative max-h-[75vh] p-2 bg-slate-900/60 rounded-2xl border border-slate-800 shadow-2xl flex items-center justify-center">
            <img
              id="preview-rendered-img"
              src={renderedImage}
              alt="Photobooth Print Preview"
              className="max-h-[70vh] w-auto object-contain rounded-xl shadow-lg transition-all duration-300"
            />
          </div>
        </div>

        {/* Right / Sidebar: Customization & Actions */}
        <div className="w-full lg:w-96 flex flex-col gap-5 bg-slate-900/90 border border-slate-800 rounded-3xl p-5 md:p-6 shadow-2xl">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div>
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-cyan-400" />
                รูปภาพของคุณพร้อมแล้ว!
              </h2>
              <p className="text-xs text-slate-400">เลือกฟิลเตอร์ สั่งพิมพ์ หรือสแกน QR Code</p>
            </div>
          </div>

          {/* Filter Selection */}
          <div>
            <label className="text-xs font-semibold text-slate-300 mb-2 block">{t.filter}</label>
            <div className="flex flex-wrap gap-1.5">
              {filters.map((f) => (
                <button
                  key={f.id}
                  onClick={() => onFilterChange(f.id)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer ${
                    activeFilter === f.id
                      ? 'bg-cyan-500 text-slate-950 font-bold shadow-sm'
                      : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* Frame Color Selection */}
          <div>
            <label className="text-xs font-semibold text-slate-300 mb-2 block">{t.frameColorLabel}</label>
            <div className="flex items-center gap-3">
              {frameColors.map((c) => (
                <button
                  key={c.color}
                  onClick={() => onFrameColorChange(c.color)}
                  className={`w-7 h-7 rounded-full transition-transform cursor-pointer border-2 ${
                    activeFrameColor === c.color ? 'scale-125 border-cyan-400 shadow-md' : 'border-slate-700 hover:scale-110'
                  }`}
                  style={{ backgroundColor: c.color }}
                  title={c.label}
                />
              ))}
            </div>
          </div>

          {/* Print Status Feedback */}
          {printSuccess && (
            <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl flex items-center gap-2 text-emerald-300 text-xs animate-fade-in">
              <Check className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>{t.printSuccess}</span>
            </div>
          )}

          {printError && (
            <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl flex items-center gap-2 text-rose-300 text-xs animate-fade-in">
              <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
              <span>{printError}</span>
            </div>
          )}

          {/* Print Actions */}
          <div className="flex flex-col gap-2.5 pt-2 border-t border-slate-800">
            {/* Copies selector & Direct Print Button */}
            <div className="flex items-center gap-2">
              <div className="flex items-center bg-slate-800 rounded-xl border border-slate-700 px-2 py-1">
                <span className="text-xs text-slate-400 mr-2">{t.copies}:</span>
                <select
                  value={copies}
                  onChange={(e) => setCopies(parseInt(e.target.value, 10))}
                  className="bg-transparent text-white text-xs font-bold focus:outline-none cursor-pointer"
                >
                  {[1, 2, 3, 4, 5].map((num) => (
                    <option key={num} value={num} className="bg-slate-900 text-white">
                      {num}
                    </option>
                  ))}
                </select>
              </div>

              {/* Direct CUPS Print Button */}
              <button
                disabled={isPrinting}
                onClick={() => onPrintDirect(copies)}
                className={`flex-1 py-3.5 px-4 rounded-xl text-sm font-bold flex items-center justify-center gap-2 shadow-lg transition-all cursor-pointer ${
                  isPrinting
                    ? 'bg-slate-800 text-slate-400 cursor-not-allowed'
                    : 'bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-slate-950 shadow-emerald-500/25 active:scale-98'
                }`}
              >
                <Printer className="w-5 h-5" />
                <span>{isPrinting ? t.printing : `${t.directPrint} (${copies})`}</span>
              </button>
            </div>

            {/* QR Code Sharing Button */}
            <button
              onClick={onOpenQRCode}
              className="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white text-sm font-semibold flex items-center justify-center gap-2 shadow-md shadow-cyan-500/20 transition active:scale-98 cursor-pointer"
            >
              <QrCode className="w-4 h-4" />
              <span>{t.saveMobile}</span>
            </button>

            {/* Bottom Row: Download, Browser Print, Retake */}
            <div className="grid grid-cols-3 gap-2 mt-1">
              <button
                onClick={handleDownload}
                className="py-2 px-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium flex items-center justify-center gap-1 transition cursor-pointer"
                title={t.download}
              >
                <Download className="w-3.5 h-3.5" />
                <span>โหลดไฟล์</span>
              </button>

              <button
                onClick={handleBrowserPrint}
                className="py-2 px-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium flex items-center justify-center gap-1 transition cursor-pointer"
                title={t.browserPrint}
              >
                <Printer className="w-3.5 h-3.5" />
                <span>AirPrint</span>
              </button>

              <button
                onClick={onRetake}
                className="py-2 px-2 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-300 text-xs font-medium flex items-center justify-center gap-1 transition cursor-pointer"
                title={t.retake}
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>{t.retake}</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Hidden container specifically for window.print() CSS */}
      <div id="printable-photo" className="hidden">
        <img src={renderedImage} alt="Printable Photo" />
      </div>
    </div>
  );
}
