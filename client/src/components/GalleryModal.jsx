import React, { useState } from 'react';
import { X, Printer, Download, Trash2, QrCode, Sparkles, AlertCircle } from 'lucide-react';

export default function GalleryModal({
  isOpen,
  onClose,
  sessions = [],
  onPrintSession,
  onDeleteSession,
  onShowQR,
  t
}) {
  const [selectedSession, setSelectedSession] = useState(null);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/85 backdrop-blur-md p-4 animate-fade-in">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl max-w-4xl w-full h-[85vh] flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-cyan-400" />
              {t.gallery} ({sessions.length})
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-full bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700 transition cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {sessions.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center text-slate-400">
              <AlertCircle className="w-12 h-12 mb-3 text-slate-600" />
              <p className="text-sm font-medium">ยังไม่มีรูปภาพที่บันทึกไว้</p>
              <p className="text-xs text-slate-500 mt-1">เริ่มถ่ายรูปช็อตแรกได้เลย!</p>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
              {sessions.map((session) => (
                <div
                  key={session.id}
                  className="group relative bg-slate-950 rounded-2xl overflow-hidden border border-slate-800 hover:border-cyan-500/50 transition-all shadow-md flex flex-col"
                >
                  <div
                    onClick={() => setSelectedSession(session)}
                    className="relative cursor-pointer overflow-hidden aspect-[1/2] flex items-center justify-center bg-slate-900"
                  >
                    <img
                      src={session.printUrl}
                      alt="Session photo"
                      className="w-full h-full object-contain p-1.5 transition-transform duration-300 group-hover:scale-105"
                      loading="lazy"
                    />
                    <div className="absolute inset-0 bg-slate-950/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                      <span className="text-xs font-semibold text-white px-2.5 py-1 bg-black/60 rounded-full backdrop-blur-xs">
                        แตะเพื่อดู
                      </span>
                    </div>
                  </div>

                  {/* Card bottom actions */}
                  <div className="p-2 bg-slate-900/90 border-t border-slate-800/80 flex items-center justify-between gap-1">
                    <span className="text-[10px] text-slate-400 truncate flex-1">
                      {new Date(session.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>

                    {/* Print again button */}
                    <button
                      onClick={() => onPrintSession(session)}
                      className="p-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 transition cursor-pointer"
                      title="พิมพ์ซ้ำ"
                    >
                      <Printer className="w-3.5 h-3.5" />
                    </button>

                    {/* QR Code button */}
                    <button
                      onClick={() => onShowQR(session)}
                      className="p-1.5 rounded-lg bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-400 transition cursor-pointer"
                      title="QR Code"
                    >
                      <QrCode className="w-3.5 h-3.5" />
                    </button>

                    {/* Delete button */}
                    <button
                      onClick={() => onDeleteSession(session.id)}
                      className="p-1.5 rounded-lg bg-rose-500/20 hover:bg-rose-500/30 text-rose-400 transition cursor-pointer"
                      title="ลบรูป"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Full-view Modal for single photo */}
      {selectedSession && (
        <div className="fixed inset-0 z-60 flex items-center justify-center bg-black/90 p-4 animate-fade-in">
          <div className="relative max-h-[90vh] flex flex-col items-center">
            <button
              onClick={() => setSelectedSession(null)}
              className="absolute -top-12 right-0 p-2 text-white bg-slate-800 rounded-full hover:bg-slate-700 cursor-pointer"
            >
              <X className="w-6 h-6" />
            </button>
            <img
              src={selectedSession.printUrl}
              alt="Enlarged photo"
              className="max-h-[80vh] w-auto object-contain rounded-2xl shadow-2xl border border-slate-700"
            />
            <div className="mt-4 flex items-center gap-3">
              <button
                onClick={() => onPrintSession(selectedSession)}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-bold rounded-xl flex items-center gap-2 cursor-pointer shadow-lg"
              >
                <Printer className="w-4 h-4" />
                <span>สั่งพิมพ์ (Print)</span>
              </button>
              <button
                onClick={() => onShowQR(selectedSession)}
                className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 text-white font-bold rounded-xl flex items-center gap-2 cursor-pointer shadow-lg"
              >
                <QrCode className="w-4 h-4" />
                <span>QR Code มือถือ</span>
              </button>
              <a
                href={selectedSession.printUrl}
                download={`photobooth_${selectedSession.id}.png`}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold rounded-xl flex items-center gap-2 cursor-pointer"
              >
                <Download className="w-4 h-4" />
                <span>บันทึกไฟล์</span>
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
