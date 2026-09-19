import React, { useState } from 'react';
import { QrCode, X, Copy, Check, Smartphone, Wifi } from 'lucide-react';

export default function QRCodeModal({ isOpen, onClose, qrCodeDataUrl, shareUrl, t }) {
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const handleCopy = () => {
    if (shareUrl) {
      navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-md p-4 animate-fade-in">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6 md:p-8 max-w-sm w-full shadow-2xl relative flex flex-col items-center text-center">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-2 rounded-full bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700 transition cursor-pointer"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Title */}
        <div className="w-12 h-12 rounded-2xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-cyan-400 mb-3">
          <Smartphone className="w-6 h-6" />
        </div>
        <h3 className="text-xl font-bold text-white mb-1">{t.qrTitle}</h3>
        <p className="text-xs text-slate-400 mb-4">{t.qrInstruction}</p>

        {/* QR Code Container */}
        <div className="p-4 bg-white rounded-2xl shadow-lg border-4 border-cyan-500/30 mb-4">
          {qrCodeDataUrl ? (
            <img src={qrCodeDataUrl} alt="Photobooth QR Code" className="w-56 h-56 object-contain" />
          ) : (
            <div className="w-56 h-56 flex items-center justify-center text-slate-400">
              <QrCode className="w-16 h-16 animate-pulse" />
            </div>
          )}
        </div>

        {/* Wi-Fi Info Hint */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800/80 border border-slate-700/60 text-xs text-slate-300 mb-4">
          <Wifi className="w-3.5 h-3.5 text-cyan-400" />
          <span>เชื่อมต่อ Wi-Fi เดียวกันก่อนสแกน</span>
        </div>

        {/* Copy Link Section */}
        {shareUrl && (
          <div className="w-full flex items-center gap-2 p-2 bg-slate-950/60 rounded-xl border border-slate-800 mb-4">
            <span className="text-xs text-slate-400 truncate flex-1 text-left px-2">
              {shareUrl}
            </span>
            <button
              onClick={handleCopy}
              className="px-3 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold flex items-center gap-1 transition cursor-pointer"
            >
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? t.copied : t.copyLink}</span>
            </button>
          </div>
        )}

        <button
          onClick={onClose}
          className="w-full py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-semibold transition cursor-pointer"
        >
          {t.close}
        </button>
      </div>
    </div>
  );
}
