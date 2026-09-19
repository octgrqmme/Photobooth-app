import React, { useState, useEffect, useCallback } from 'react';
import confetti from 'canvas-confetti';
import { translations } from './utils/i18n';
import { sounds } from './utils/soundEffects';
import { renderPhotoStrip } from './utils/stripRenderer';

// Components
import Navbar from './components/Navbar';
import CameraView from './components/CameraView';
import CaptureControls from './components/CaptureControls';
import PreviewModal from './components/PreviewModal';
import GalleryModal from './components/GalleryModal';
import SettingsModal from './components/SettingsModal';
import QRCodeModal from './components/QRCodeModal';

export default function App() {
  const [lang, setLang] = useState('th');
  const t = translations[lang];

  // System & Printers
  const [printerInfo, setPrinterInfo] = useState({ printers: [], defaultPrinter: null });
  const [systemInfo, setSystemInfo] = useState(null);

  // Configuration
  const [config, setConfig] = useState({
    selectedPrinter: '',
    paperSize: 'iso_a4_210x297mm',
    selectedCameraId: '',
    mirrorCamera: true,
    countdownDuration: 3,
    shotInterval: 2,
    eventTitle: 'PHOTOBOOTH MEMORY',
    eventSubtitle: 'BANGKOK, THAILAND',
    frameColor: '#ffffff',
    soundEnabled: true,
    autoPrint: false
  });

  // Capture State
  const [layout, setLayout] = useState('strip4'); // 'single' | 'strip3' | 'strip4' | 'dual' | 'grid'
  const [countdownDuration, setCountdownDuration] = useState(3);
  const [isCapturing, setIsCapturing] = useState(false);
  const [currentShotIndex, setCurrentShotIndex] = useState(0);
  const [totalShots, setTotalShots] = useState(4);
  const [countdownNumber, setCountdownNumber] = useState(null);
  const [isFlashing, setIsFlashing] = useState(false);
  const [capturedShots, setCapturedShots] = useState([]);

  // Rendered Output & Filters
  const [renderedImage, setRenderedImage] = useState(null);
  const [activeFilter, setActiveFilter] = useState('none');
  const [activeFrameColor, setActiveFrameColor] = useState('#ffffff');
  const [currentSession, setCurrentSession] = useState(null);

  // Modals
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [isGalleryOpen, setIsGalleryOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isQROpen, setIsQROpen] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Print Operations
  const [isPrinting, setIsPrinting] = useState(false);
  const [printSuccess, setPrintSuccess] = useState(false);
  const [printError, setPrintError] = useState(null);
  const [isTestingPrint, setIsTestingPrint] = useState(false);
  const [testPrintStatus, setTestPrintStatus] = useState(null);

  // Gallery
  const [sessions, setSessions] = useState([]);

  // Fetch system & printer info on mount
  const fetchSystemInfo = useCallback(async () => {
    try {
      const res = await fetch('/api/system-info');
      const data = await res.json();
      setSystemInfo(data);
      if (data.printers) {
        setPrinterInfo({
          printers: data.printers,
          defaultPrinter: data.defaultPrinter
        });
        setConfig(prev => ({
          ...prev,
          selectedPrinter: prev.selectedPrinter || data.defaultPrinter || data.printers[0]?.name || ''
        }));
      }
    } catch (err) {
      console.error('Failed to load system info:', err);
    }
  }, []);

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/photos');
      const data = await res.json();
      if (data.sessions) {
        setSessions(data.sessions);
      }
    } catch (err) {
      console.error('Failed to fetch gallery sessions:', err);
    }
  }, []);

  useEffect(() => {
    fetchSystemInfo();
    fetchSessions();
  }, [fetchSystemInfo, fetchSessions]);

  // Sync total shots based on layout
  useEffect(() => {
    switch (layout) {
      case 'single':
        setTotalShots(1);
        break;
      case 'strip3':
        setTotalShots(3);
        break;
      case 'strip4':
      case 'dual':
      case 'grid':
      default:
        setTotalShots(4);
        break;
    }
  }, [layout]);

  // Fullscreen Handler
  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(e => console.warn(e));
      setIsFullscreen(true);
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(e => console.warn(e));
        setIsFullscreen(false);
      }
    }
  };

  // Trigger Print to CUPS
  const handlePrintDirect = async (copies = 1, sessionToPrint = null) => {
    const targetSession = sessionToPrint || currentSession;
    const targetImage = renderedImage;
    const printerToUse = config.selectedPrinter || printerInfo.defaultPrinter;

    setIsPrinting(true);
    setPrintSuccess(false);
    setPrintError(null);

    try {
      const payload = {
        printerName: printerToUse,
        copies,
        paperSize: config.paperSize,
        ...(targetSession ? { sessionId: targetSession.id } : { imageData: targetImage })
      };

      const res = await fetch('/api/print', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || 'Print dispatch failed');
      }

      setPrintSuccess(true);
      setTimeout(() => setPrintSuccess(false), 5000);
    } catch (err) {
      console.error('Print failed:', err);
      setPrintError(err.message || t.printFail);
      setTimeout(() => setPrintError(null), 6000);
    } finally {
      setIsPrinting(false);
    }
  };

  // Test Print
  const handleTestPrint = async () => {
    setIsTestingPrint(true);
    setTestPrintStatus(null);
    try {
      // Create a small test canvas
      const testCanvas = document.createElement('canvas');
      testCanvas.width = 800;
      testCanvas.height = 1200;
      const ctx = testCanvas.getContext('2d');
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, 800, 1200);
      ctx.fillStyle = '#0f172a';
      ctx.font = 'bold 36px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('PHOTOBOOTH TEST PRINT', 400, 500);
      ctx.font = '24px sans-serif';
      ctx.fillText(new Date().toLocaleString('th-TH'), 400, 560);
      ctx.fillText(`Printer: ${config.selectedPrinter || 'Default'}`, 400, 620);
      const testData = testCanvas.toDataURL('image/png');

      const res = await fetch('/api/print', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          imageData: testData,
          printerName: config.selectedPrinter,
          copies: 1,
          paperSize: config.paperSize
        })
      });
      const data = await res.json();
      if (data.success) {
        setTestPrintStatus('ส่งคำสั่งพิมพ์ทดสอบสำเร็จ!');
      } else {
        setTestPrintStatus(`เกิดข้อผิดพลาด: ${data.error}`);
      }
    } catch (e) {
      setTestPrintStatus(`เกิดข้อผิดพลาด: ${e.message}`);
    } finally {
      setIsTestingPrint(false);
    }
  };

  // Start Capture Sequence
  const startCaptureSequence = useCallback(async () => {
    if (isCapturing || isPreviewOpen || isGalleryOpen || isSettingsOpen) return;

    setIsCapturing(true);
    setCapturedShots([]);
    setCurrentShotIndex(0);

    const shots = [];
    const shotsToTake = layout === 'single' ? 1 : (layout === 'strip3' ? 3 : 4);

    for (let shot = 0; shot < shotsToTake; shot++) {
      setCurrentShotIndex(shot);

      // Countdown loop: 3, 2, 1
      const duration = countdownDuration;
      for (let sec = duration; sec > 0; sec--) {
        setCountdownNumber(sec);
        if (sec === 1) {
          sounds.playFinalBeep();
        } else {
          sounds.playCountdownBeep();
        }
        await new Promise(r => setTimeout(r, 1000));
      }

      setCountdownNumber(0);

      // Shutter click & Flash
      sounds.playShutterClick();
      setIsFlashing(true);

      // Capture frame
      let frame = null;
      if (typeof window.__photoboothCaptureFrame === 'function') {
        frame = window.__photoboothCaptureFrame();
      }

      if (frame) {
        shots.push(frame);
        setCapturedShots([...shots]);
      }

      // Flash decay
      await new Promise(r => setTimeout(r, 350));
      setIsFlashing(false);
      setCountdownNumber(null);

      // Short interval between shots if not the last shot
      if (shot < shotsToTake - 1) {
        await new Promise(r => setTimeout(r, config.shotInterval * 1000));
      }
    }

    setIsCapturing(false);

    // Compilation & celebration
    try {
      sounds.playSuccessChime();
      confetti({
        particleCount: 80,
        spread: 70,
        origin: { y: 0.6 }
      });

      const initialColor = config.frameColor || '#ffffff';
      setActiveFrameColor(initialColor);
      setActiveFilter('none');

      const rendered = await renderPhotoStrip(shots, layout, 'none', {
        backgroundColor: initialColor,
        textColor: initialColor === '#0f172a' ? '#f8fafc' : '#1e293b',
        title: config.eventTitle,
        subtitle: config.eventSubtitle
      });

      setRenderedImage(rendered);

      // Save to server
      const saveRes = await fetch('/api/photos/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          imageData: rendered,
          rawCaptures: shots,
          layout,
          config: {
            title: config.eventTitle,
            subtitle: config.eventSubtitle,
            frameColor: initialColor
          }
        })
      });

      const saveData = await saveRes.json();
      if (saveData.session) {
        setCurrentSession(saveData.session);
        setSessions(prev => [saveData.session, ...prev]);

        // Auto print if configured
        if (config.autoPrint) {
          handlePrintDirect(1, saveData.session);
        }
      }

      setIsPreviewOpen(true);
    } catch (renderErr) {
      console.error('Failed to render photo strip:', renderErr);
    }
  }, [
    isCapturing,
    isPreviewOpen,
    isGalleryOpen,
    isSettingsOpen,
    layout,
    countdownDuration,
    config
  ]);

  // Re-render when filter or frame color changes in preview
  const handleFilterChange = async (newFilter) => {
    setActiveFilter(newFilter);
    if (capturedShots.length > 0) {
      const rendered = await renderPhotoStrip(capturedShots, layout, newFilter, {
        backgroundColor: activeFrameColor,
        textColor: activeFrameColor === '#0f172a' ? '#f8fafc' : '#1e293b',
        title: config.eventTitle,
        subtitle: config.eventSubtitle
      });
      setRenderedImage(rendered);
    }
  };

  const handleFrameColorChange = async (newColor) => {
    setActiveFrameColor(newColor);
    if (capturedShots.length > 0) {
      const rendered = await renderPhotoStrip(capturedShots, layout, activeFilter, {
        backgroundColor: newColor,
        textColor: newColor === '#0f172a' ? '#f8fafc' : '#1e293b',
        title: config.eventTitle,
        subtitle: config.eventSubtitle
      });
      setRenderedImage(rendered);
    }
  };

  // Keyboard shortcut (Spacebar triggers capture)
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.code === 'Space' && !e.repeat) {
        // Prevent default page scroll on space
        if (!isPreviewOpen && !isGalleryOpen && !isSettingsOpen && !isQROpen) {
          e.preventDefault();
          startCaptureSequence();
        }
      } else if (e.key === 'Escape') {
        setIsPreviewOpen(false);
        setIsGalleryOpen(false);
        setIsSettingsOpen(false);
        setIsQROpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [startCaptureSequence, isPreviewOpen, isGalleryOpen, isSettingsOpen, isQROpen]);

  // Delete session from gallery
  const handleDeleteSession = async (id) => {
    try {
      await fetch(`/api/photos/${id}`, { method: 'DELETE' });
      setSessions(prev => prev.filter(s => s.id !== id));
    } catch (e) {
      console.error('Failed to delete session:', e);
    }
  };

  return (
    <div className="flex flex-col h-screen w-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {/* Top Navigation */}
      <Navbar
        lang={lang}
        setLang={setLang}
        t={t}
        printerInfo={printerInfo}
        onOpenSettings={() => setIsSettingsOpen(true)}
        onOpenGallery={() => setIsGalleryOpen(true)}
        galleryCount={sessions.length}
        isFullscreen={isFullscreen}
        toggleFullscreen={toggleFullscreen}
      />

      {/* Main Kiosk Area */}
      <main className="flex-1 flex flex-col items-center justify-between p-3 md:p-6 overflow-hidden max-w-7xl w-full mx-auto">
        {/* Camera Live View Box */}
        <div className="flex-1 w-full flex items-center justify-center min-h-0 relative">
          <CameraView
            selectedDeviceId={config.selectedCameraId}
            mirrorCamera={config.mirrorCamera}
            countdownDuration={countdownDuration}
            isCapturing={isCapturing}
            currentShotIndex={currentShotIndex}
            totalShots={totalShots}
            countdownNumber={countdownNumber}
            isFlashing={isFlashing}
            capturedShots={capturedShots}
            onStartCapture={startCaptureSequence}
            t={t}
          />
        </div>

        {/* Bottom Kiosk Controls */}
        <CaptureControls
          layout={layout}
          setLayout={setLayout}
          countdownDuration={countdownDuration}
          setCountdownDuration={setCountdownDuration}
          isCapturing={isCapturing}
          onStartCapture={startCaptureSequence}
          t={t}
        />
      </main>

      {/* Modals */}
      <PreviewModal
        isOpen={isPreviewOpen}
        renderedImage={renderedImage}
        onFilterChange={handleFilterChange}
        activeFilter={activeFilter}
        onFrameColorChange={handleFrameColorChange}
        activeFrameColor={activeFrameColor}
        onPrintDirect={handlePrintDirect}
        isPrinting={isPrinting}
        printSuccess={printSuccess}
        printError={printError}
        onOpenQRCode={() => setIsQROpen(true)}
        onRetake={() => {
          setIsPreviewOpen(false);
          setCapturedShots([]);
          setRenderedImage(null);
        }}
        printerName={config.selectedPrinter || printerInfo.defaultPrinter}
        t={t}
      />

      <QRCodeModal
        isOpen={isQROpen}
        onClose={() => setIsQROpen(false)}
        qrCodeDataUrl={currentSession?.qrCodeDataUrl}
        shareUrl={currentSession?.shareUrl}
        t={t}
      />

      <GalleryModal
        isOpen={isGalleryOpen}
        onClose={() => setIsGalleryOpen(false)}
        sessions={sessions}
        onPrintSession={(session) => handlePrintDirect(1, session)}
        onDeleteSession={handleDeleteSession}
        onShowQR={(session) => {
          setCurrentSession(session);
          setIsQROpen(true);
        }}
        t={t}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        config={config}
        onSaveConfig={(newConfig) => setConfig(newConfig)}
        printerInfo={printerInfo}
        onRefreshPrinters={fetchSystemInfo}
        onTestPrint={handleTestPrint}
        isTestingPrint={isTestingPrint}
        testPrintStatus={testPrintStatus}
        t={t}
      />
    </div>
  );
}
