import React, { useRef, useEffect, useState } from 'react';
import { Camera, RefreshCw, AlertTriangle } from 'lucide-react';
import { sounds } from '../utils/soundEffects';

export default function CameraView({
  selectedDeviceId,
  mirrorCamera = true,
  countdownDuration = 3,
  isCapturing,
  currentShotIndex,
  totalShots,
  countdownNumber,
  isFlashing,
  capturedShots = [],
  onStartCapture,
  t
}) {
  const videoRef = useRef(null);
  const [stream, setStream] = useState(null);
  const [cameraError, setCameraError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  // Initialize and switch camera stream
  useEffect(() => {
    let currentStream = null;

    async function initCamera() {
      setIsLoading(true);
      setCameraError(null);

      // Stop existing stream tracks
      if (stream) {
        stream.getTracks().forEach(track => track.stop());
      }

      const constraints = {
        video: {
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          deviceId: selectedDeviceId ? { exact: selectedDeviceId } : undefined
        },
        audio: false
      };

      try {
        const mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
        currentStream = mediaStream;
        setStream(mediaStream);
        if (videoRef.current) {
          videoRef.current.srcObject = mediaStream;
        }
        setIsLoading(false);
      } catch (err) {
        console.error('Failed to get camera stream:', err);
        // Fallback to generic video if exact constraints fail
        try {
          const fallbackStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
          currentStream = fallbackStream;
          setStream(fallbackStream);
          if (videoRef.current) {
            videoRef.current.srcObject = fallbackStream;
          }
          setIsLoading(false);
        } catch (fallbackErr) {
          setCameraError(fallbackErr.message || t.cameraError);
          setIsLoading(false);
        }
      }
    }

    initCamera();

    return () => {
      if (currentStream) {
        currentStream.getTracks().forEach(track => track.stop());
      }
    };
  }, [selectedDeviceId]);

  // Capture current video frame into high-res data URL
  const captureFrame = () => {
    if (!videoRef.current) return null;
    const video = videoRef.current;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    const ctx = canvas.getContext('2d');

    if (mirrorCamera) {
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
    }

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.95);
  };

  // Expose captureFrame to window/parent if needed
  useEffect(() => {
    window.__photoboothCaptureFrame = captureFrame;
    return () => {
      delete window.__photoboothCaptureFrame;
    };
  }, [mirrorCamera]);

  return (
    <div className="relative w-full h-full flex items-center justify-center bg-black overflow-hidden rounded-2xl md:rounded-3xl border border-slate-800 shadow-2xl">
      {/* Video element */}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className={`w-full h-full object-cover transition-transform duration-300 ${mirrorCamera ? 'scale-x-[-1]' : ''}`}
      />

      {/* Loading state */}
      {isLoading && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-slate-950/80 z-10 backdrop-blur-sm">
          <RefreshCw className="w-10 h-10 text-cyan-400 animate-spin mb-3" />
          <p className="text-slate-300 text-sm font-medium">กำลังเปิดใช้งานกล้อง...</p>
        </div>
      )}

      {/* Camera Error State */}
      {cameraError && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-slate-950 p-6 text-center z-10">
          <AlertTriangle className="w-12 h-12 text-rose-500 mb-3" />
          <h3 className="text-lg font-bold text-white mb-1">{t.cameraError}</h3>
          <p className="text-xs text-slate-400 max-w-md">{cameraError}</p>
          <p className="text-xs text-cyan-400 mt-4">
            กรุณาอนุญาต Camera Permission ในบราวเซอร์ หรือตรวจสอบว่ามีแอปอื่นเปิดกล้องค้างอยู่หรือไม่
          </p>
        </div>
      )}

      {/* White Screen Flash Overlay */}
      {isFlashing && (
        <div className="absolute inset-0 bg-white z-50 animate-flash pointer-events-none" />
      )}

      {/* Countdown Overlay (3... 2... 1...) */}
      {isCapturing && countdownNumber !== null && countdownNumber > 0 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 backdrop-blur-[2px] z-30 pointer-events-none">
          <div className="relative flex items-center justify-center">
            {/* Outer pulsating glow ring */}
            <div className="w-48 h-48 md:w-60 md:h-60 rounded-full border-4 border-cyan-400/40 animate-ping absolute" />
            <div className="w-40 h-40 md:w-52 md:h-52 rounded-full bg-slate-950/70 border-4 border-cyan-400 flex items-center justify-center shadow-[0_0_50px_rgba(6,182,212,0.6)]">
              <span className="text-7xl md:text-9xl font-black text-white animate-countdown tracking-tight">
                {countdownNumber}
              </span>
            </div>
          </div>

          <p className="mt-8 text-xl md:text-2xl font-bold text-white drop-shadow-md tracking-wider">
            {countdownNumber === 1 ? t.smile : t.getReady}
          </p>

          {/* Shot progress indicator */}
          {totalShots > 1 && (
            <div className="mt-4 px-4 py-1.5 rounded-full bg-slate-900/80 border border-slate-700 text-sm font-semibold text-cyan-300">
              {t.shotCounter.replace('{current}', currentShotIndex + 1).replace('{total}', totalShots)}
            </div>
          )}
        </div>
      )}

      {/* Top Banner: Multi-shot progress pill */}
      {isCapturing && totalShots > 1 && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-full bg-slate-900/90 border border-slate-700/80 shadow-lg flex items-center gap-2 z-20">
          <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-ping" />
          <span className="text-xs font-semibold text-white">
            {t.shotCounter.replace('{current}', currentShotIndex + 1).replace('{total}', totalShots)}
          </span>
        </div>
      )}

      {/* Side thumbnails of already captured shots in this sequence */}
      {capturedShots.length > 0 && isCapturing && (
        <div className="absolute right-4 top-1/2 -translate-y-1/2 flex flex-col gap-2 z-20">
          {capturedShots.map((shot, idx) => (
            <div
              key={idx}
              className="w-16 h-20 md:w-20 md:h-24 rounded-lg overflow-hidden border-2 border-cyan-400 shadow-lg transform transition animate-fade-in"
            >
              <img src={shot} alt={`Shot ${idx + 1}`} className="w-full h-full object-cover" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
