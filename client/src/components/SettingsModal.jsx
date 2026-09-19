import React, { useState, useEffect } from 'react';
import { X, Printer, Camera, Palette, Volume2, VolumeX, RefreshCw, Check, AlertCircle, Sparkles } from 'lucide-react';
import { sounds } from '../utils/soundEffects';

export default function SettingsModal({
  isOpen,
  onClose,
  config,
  onSaveConfig,
  printerInfo,
  onRefreshPrinters,
  onTestPrint,
  isTestingPrint,
  testPrintStatus,
  t
}) {
  const [localConfig, setLocalConfig] = useState({ ...config });
  const [cameras, setCameras] = useState([]);
  const [savedSuccess, setSavedSuccess] = useState(false);

  // Sync state when opened
  useEffect(() => {
    if (isOpen) {
      setLocalConfig({ ...config });
      // Enumerate camera devices
      if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
        navigator.mediaDevices.enumerateDevices()
          .then(devices => {
            const videoInputs = devices.filter(d => d.kind === 'videoinput');
            setCameras(videoInputs);
          })
          .catch(err => console.error('Error enumerating devices:', err));
      }
    }
  }, [isOpen, config]);

  if (!isOpen) return null;

  const handleChange = (key, value) => {
    setLocalConfig(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = () => {
    onSaveConfig(localConfig);
    sounds.setEnabled(localConfig.soundEnabled);
    setSavedSuccess(true);
    setTimeout(() => {
      setSavedSuccess(false);
      onClose();
    }, 800);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/85 backdrop-blur-md p-4 animate-fade-in">
      <div className="bg-slate-900 border border-slate-800 rounded-3xl max-w-2xl w-full max-h-[90vh] flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-cyan-400" />
              {t.settingsTitle}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-full bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700 transition cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 text-slate-300 text-sm">
          {/* 1. Printer Section */}
          <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800/80 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-white flex items-center gap-2">
                <Printer className="w-4 h-4 text-cyan-400" />
                {t.printerSettings}
              </h3>
              <button
                type="button"
                onClick={onRefreshPrinters}
                className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center gap-1 cursor-pointer"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                รีเฟรชรายการเครื่องพิมพ์
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-slate-400 mb-1">
                  {t.selectedPrinter}
                </label>
                <select
                  value={localConfig.selectedPrinter || ''}
                  onChange={(e) => handleChange('selectedPrinter', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-400 cursor-pointer"
                >
                  {printerInfo?.printers?.length > 0 ? (
                    printerInfo.printers.map((p) => (
                      <option key={p.name} value={p.name}>
                        {p.name} {p.isDefault ? '(ค่าเริ่มต้น)' : ''} - {p.status}
                      </option>
                    ))
                  ) : (
                    <option value="">ไม่พบเครื่องปริ้นในระบบ</option>
                  )}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-slate-400 mb-1">
                    {t.paperSize}
                  </label>
                  <select
                    value={localConfig.paperSize}
                    onChange={(e) => handleChange('paperSize', e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-400 cursor-pointer"
                  >
                    <option value="iso_a4_210x297mm">A4 (210 x 297 mm)</option>
                    <option value="4x6">4x6 นิ้ว (Photo Paper)</option>
                    <option value="Custom.2x6in">2x6 นิ้ว (Photobooth Strip)</option>
                    <option value="na_letter_8.5x11in">Letter</option>
                  </select>
                </div>

                <div className="flex items-end">
                  <button
                    type="button"
                    disabled={isTestingPrint}
                    onClick={onTestPrint}
                    className="w-full py-2 px-3 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-xl text-xs font-semibold text-cyan-300 flex items-center justify-center gap-1.5 transition cursor-pointer"
                  >
                    <Printer className="w-4 h-4" />
                    <span>{isTestingPrint ? 'กำลังทดสอบ...' : t.testPrint}</span>
                  </button>
                </div>
              </div>

              {testPrintStatus && (
                <p className="text-xs text-emerald-400 flex items-center gap-1">
                  <Check className="w-3.5 h-3.5" />
                  {testPrintStatus}
                </p>
              )}

              {/* Auto print toggle */}
              <div className="flex items-center justify-between pt-2 border-t border-slate-800">
                <span className="text-xs text-slate-300 font-medium">{t.autoPrint}</span>
                <input
                  type="checkbox"
                  checked={localConfig.autoPrint}
                  onChange={(e) => handleChange('autoPrint', e.target.checked)}
                  className="w-4 h-4 rounded text-cyan-500 cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* 2. Camera Section */}
          <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800/80 space-y-4">
            <h3 className="font-bold text-white flex items-center gap-2">
              <Camera className="w-4 h-4 text-cyan-400" />
              {t.cameraSettings}
            </h3>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-slate-400 mb-1">
                  {t.selectedCamera}
                </label>
                <select
                  value={localConfig.selectedCameraId || ''}
                  onChange={(e) => handleChange('selectedCameraId', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-400 cursor-pointer"
                >
                  <option value="">กล้องค่าเริ่มต้นของระบบ (Default)</option>
                  {cameras.map((c, i) => (
                    <option key={c.deviceId} value={c.deviceId}>
                      {c.label || `กล้องที่ ${i + 1}`}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-slate-400 mb-1">
                    {t.countdownDuration}
                  </label>
                  <select
                    value={localConfig.countdownDuration}
                    onChange={(e) => handleChange('countdownDuration', parseInt(e.target.value, 10))}
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-400 cursor-pointer"
                  >
                    <option value={3}>3 วินาที</option>
                    <option value={5}>5 วินาที</option>
                    <option value={8}>8 วินาที</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-400 mb-1">
                    {t.intervalBetweenShots}
                  </label>
                  <select
                    value={localConfig.shotInterval}
                    onChange={(e) => handleChange('shotInterval', parseInt(e.target.value, 10))}
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-400 cursor-pointer"
                  >
                    <option value={2}>2 วินาที</option>
                    <option value={3}>3 วินาที</option>
                    <option value={4}>4 วินาที</option>
                  </select>
                </div>
              </div>

              {/* Mirror toggle */}
              <div className="flex items-center justify-between pt-2 border-t border-slate-800">
                <span className="text-xs text-slate-300 font-medium">{t.mirrorCamera}</span>
                <input
                  type="checkbox"
                  checked={localConfig.mirrorCamera}
                  onChange={(e) => handleChange('mirrorCamera', e.target.checked)}
                  className="w-4 h-4 rounded text-cyan-500 cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* 3. Event Branding & Template Section */}
          <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800/80 space-y-4">
            <h3 className="font-bold text-white flex items-center gap-2">
              <Palette className="w-4 h-4 text-cyan-400" />
              {t.brandingSettings}
            </h3>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-slate-400 mb-1">
                  {t.eventTitleText}
                </label>
                <input
                  type="text"
                  value={localConfig.eventTitle}
                  onChange={(e) => handleChange('eventTitle', e.target.value)}
                  placeholder="เช่น HAPPY BIRTHDAY, WEDDING OF LISA & MARK"
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-400"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-400 mb-1">
                  {t.eventSubtitleText}
                </label>
                <input
                  type="text"
                  value={localConfig.eventSubtitle}
                  onChange={(e) => handleChange('eventSubtitle', e.target.value)}
                  placeholder="เช่น BANGKOK, THAILAND"
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-400"
                />
              </div>

              {/* Audio feedback toggle */}
              <div className="flex items-center justify-between pt-2 border-t border-slate-800">
                <span className="text-xs text-slate-300 font-medium flex items-center gap-2">
                  {localConfig.soundEnabled ? <Volume2 className="w-4 h-4 text-cyan-400" /> : <VolumeX className="w-4 h-4 text-slate-500" />}
                  {t.soundEnabled}
                </span>
                <input
                  type="checkbox"
                  checked={localConfig.soundEnabled}
                  onChange={(e) => handleChange('soundEnabled', e.target.checked)}
                  className="w-4 h-4 rounded text-cyan-500 cursor-pointer"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="p-4 border-t border-slate-800 flex items-center justify-end gap-3 bg-slate-950/80">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition cursor-pointer"
          >
            ยกเลิก
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="px-5 py-2 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white text-xs font-bold transition shadow-lg shadow-cyan-500/20 cursor-pointer flex items-center gap-1.5"
          >
            {savedSuccess ? <Check className="w-4 h-4" /> : null}
            <span>{savedSuccess ? 'บันทึกเรียบร้อย!' : t.saveSettings}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
