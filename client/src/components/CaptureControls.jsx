import React from 'react';
import { Camera, Layers, Clock, Sparkles } from 'lucide-react';

export default function CaptureControls({
  layout,
  setLayout,
  countdownDuration,
  setCountdownDuration,
  isCapturing,
  onStartCapture,
  t
}) {
  const layoutOptions = [
    { id: 'strip4', label: t.layouts.strip4, shots: 4, icon: '🎞️', desc: 'ยอดนิยม! สไตล์ตู้ถ่ายรูป' },
    { id: 'dual', label: t.layouts.dual, shots: 4, icon: '✂️', desc: 'พิมพ์ 2 แถวพร้อมตัดครึ่ง 4x6"' },
    { id: 'strip3', label: t.layouts.strip3, shots: 3, icon: '📸', desc: '3 ช็อต ยาวคลาสสิก' },
    { id: 'grid', label: t.layouts.grid, shots: 4, icon: '▦', desc: 'ตาราง 2x2 สไตล์โพลารอยด์' },
    { id: 'single', label: t.layouts.single, shots: 1, icon: '🖼️', desc: '1 ภาพเดี่ยวพร้อมกรอบ' },
  ];

  return (
    <div className="w-full flex flex-col items-center gap-4 py-4 z-20">
      {/* Layout selector pills */}
      <div className="flex flex-wrap items-center justify-center gap-2 p-1.5 bg-slate-900/90 backdrop-blur-md rounded-2xl border border-slate-800 shadow-xl max-w-full overflow-x-auto">
        {layoutOptions.map((opt) => {
          const isSelected = layout === opt.id;
          return (
            <button
              key={opt.id}
              disabled={isCapturing}
              onClick={() => setLayout(opt.id)}
              className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs sm:text-sm font-semibold transition-all cursor-pointer ${
                isSelected
                  ? 'bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-md shadow-cyan-500/30'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              } ${isCapturing ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span>{opt.icon}</span>
              <span>{opt.label}</span>
            </button>
          );
        })}
      </div>

      {/* Main Action Area */}
      <div className="flex items-center gap-6 md:gap-8">
        {/* Countdown Timer Selector (3s / 5s) */}
        <div className="flex items-center gap-1 p-1 bg-slate-900/80 rounded-xl border border-slate-800">
          {[3, 5].map((sec) => (
            <button
              key={sec}
              disabled={isCapturing}
              onClick={() => setCountdownDuration(sec)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition cursor-pointer ${
                countdownDuration === sec
                  ? 'bg-slate-800 text-cyan-400 border border-slate-700'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {sec}s
            </button>
          ))}
        </div>

        {/* Giant Capture Button */}
        <button
          disabled={isCapturing}
          onClick={onStartCapture}
          className={`relative group flex items-center justify-center w-20 h-20 md:w-24 md:h-24 rounded-full transition-all duration-300 transform active:scale-95 cursor-pointer ${
            isCapturing
              ? 'opacity-60 cursor-not-allowed scale-95'
              : 'hover:scale-105 shadow-[0_0_40px_rgba(6,182,212,0.4)]'
          }`}
          title={t.startCapture}
        >
          {/* Outer ring */}
          <div className="absolute inset-0 rounded-full bg-gradient-to-tr from-cyan-500 via-sky-400 to-blue-600 animate-pulse group-hover:blur-[2px] transition" />

          {/* Inner ring */}
          <div className="absolute inset-1.5 rounded-full bg-slate-950 flex items-center justify-center">
            {/* Core shutter button */}
            <div className="w-14 h-14 md:w-16 md:h-16 rounded-full bg-gradient-to-tr from-cyan-400 to-blue-500 flex items-center justify-center shadow-inner group-hover:from-cyan-300 group-hover:to-blue-400 transition">
              <Camera className="w-7 h-7 md:w-8 md:h-8 text-slate-950 font-bold" />
            </div>
          </div>
        </button>

        {/* Keyboard hint badge */}
        <div className="hidden sm:flex flex-col items-start text-left">
          <span className="text-xs font-semibold text-slate-300">{t.startCapture}</span>
          <span className="text-[11px] text-slate-500">{t.pressSpace}</span>
        </div>
      </div>
    </div>
  );
}
