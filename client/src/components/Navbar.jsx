import React from 'react';
import { Camera, Printer, Images, Settings, Maximize, Minimize, Globe, CheckCircle2, AlertCircle } from 'lucide-react';

export default function Navbar({
  lang,
  setLang,
  t,
  printerInfo,
  onOpenSettings,
  onOpenGallery,
  galleryCount = 0,
  isFullscreen,
  toggleFullscreen
}) {
  const activePrinter = printerInfo?.printers?.find(p => p.isDefault) || printerInfo?.printers?.[0];
  const isPrinterOnline = activePrinter?.online;

  return (
    <header className="h-16 px-4 md:px-6 bg-slate-900/90 backdrop-blur-md border-b border-slate-800 flex items-center justify-between z-30 sticky top-0">
      {/* Brand & App Title */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/25">
          <Camera className="w-5 h-5 text-white" />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold text-white tracking-wide">{t.appTitle}</h1>
            <span className="text-[10px] font-semibold bg-cyan-500/20 text-cyan-300 px-2 py-0.5 rounded-full border border-cyan-500/30">
              v1.0
            </span>
          </div>
          <p className="text-xs text-slate-400 hidden sm:block">{t.subtitle}</p>
        </div>
      </div>

      {/* Center: Live Printer Status Pill */}
      <div className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800/80 border border-slate-700/60 text-xs">
        <Printer className="w-4 h-4 text-slate-300" />
        {activePrinter ? (
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${isPrinterOnline ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`}></span>
            <span className="text-slate-200 font-medium truncate max-w-[180px]">
              {activePrinter.name.replace(/_/g, ' ')}
            </span>
            <span className={`text-[11px] ${isPrinterOnline ? 'text-emerald-400' : 'text-amber-400'}`}>
              ({isPrinterOnline ? t.printerReady : t.printerOffline})
            </span>
          </div>
        ) : (
          <span className="text-slate-400 flex items-center gap-1">
            <AlertCircle className="w-3.5 h-3.5 text-amber-400" />
            {t.noPrinter}
          </span>
        )}
      </div>

      {/* Action Buttons */}
      <div className="flex items-center gap-2 sm:gap-3">
        {/* Gallery button */}
        <button
          onClick={onOpenGallery}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs sm:text-sm font-medium transition border border-slate-700 cursor-pointer"
          title={t.gallery}
        >
          <Images className="w-4 h-4 text-cyan-400" />
          <span className="hidden sm:inline">{t.gallery}</span>
          {galleryCount > 0 && (
            <span className="bg-cyan-500 text-slate-950 font-bold px-1.5 py-0.2 text-[11px] rounded-full">
              {galleryCount}
            </span>
          )}
        </button>

        {/* Settings button */}
        <button
          onClick={onOpenSettings}
          className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 transition border border-slate-700 cursor-pointer"
          title={t.settings}
        >
          <Settings className="w-4 h-4 text-slate-300" />
        </button>

        {/* Language switch */}
        <button
          onClick={() => setLang(lang === 'th' ? 'en' : 'th')}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition border border-slate-700 cursor-pointer"
          title="Switch Language"
        >
          <Globe className="w-3.5 h-3.5 text-slate-400" />
          <span>{lang === 'th' ? 'TH' : 'EN'}</span>
        </button>

        {/* Fullscreen toggle */}
        <button
          onClick={toggleFullscreen}
          className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 transition border border-slate-700 cursor-pointer"
          title={isFullscreen ? t.exitFullscreen : t.fullscreen}
        >
          {isFullscreen ? <Minimize className="w-4 h-4 text-slate-300" /> : <Maximize className="w-4 h-4 text-slate-300" />}
        </button>
      </div>
    </header>
  );
}
