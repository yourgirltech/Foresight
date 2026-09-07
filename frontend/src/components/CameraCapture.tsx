import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, RotateCcw, Upload, X } from "lucide-react";

type Phase = "loading" | "live" | "review" | "error";

interface Props {
  /** Called with the captured still as a JPEG File. */
  onCapture: (file: File) => void;
  /** Close without capturing. */
  onClose: () => void;
  /** Fall back to the OS file picker (camera unavailable / user chose to). */
  onPickFile: () => void;
}

/**
 * A real in-browser camera. Uses getUserMedia for a live preview + a still
 * capture (front desk on a tablet or a laptop with a webcam). Where the camera
 * is unavailable — no device, permission blocked, insecure origin — it offers
 * the file picker instead.
 */
export function CameraCapture({ onCapture, onClose, onPickFile }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const shotUrlRef = useRef<string | null>(null);

  const [phase, setPhase] = useState<Phase>("loading");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [shotUrl, setShotUrl] = useState<string | null>(null);
  const shotBlobRef = useRef<Blob | null>(null);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const clearShot = useCallback(() => {
    if (shotUrlRef.current) URL.revokeObjectURL(shotUrlRef.current);
    shotUrlRef.current = null;
    shotBlobRef.current = null;
    setShotUrl(null);
  }, []);

  const start = useCallback(async () => {
    setPhase("loading");
    setErrorMsg("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setErrorMsg(
        "This browser can't open the camera here (it needs a secure connection). Choose a saved image instead.",
      );
      setPhase("error");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
      setPhase("live");
    } catch (e) {
      const name = (e as DOMException)?.name;
      if (name === "NotAllowedError" || name === "SecurityError") {
        setErrorMsg(
          "Camera access was blocked. Allow the camera for this site in your browser settings, or choose a saved image.",
        );
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        setErrorMsg("No camera was found on this device. Choose a saved image instead.");
      } else {
        setErrorMsg("The camera couldn't be started. Choose a saved image instead.");
      }
      setPhase("error");
    }
  }, []);

  useEffect(() => {
    void start();
    return () => {
      stopStream();
      clearShot();
    };
  }, [start, stopStream, clearShot]);

  // lock body scroll while open
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  function capture() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        clearShot();
        const url = URL.createObjectURL(blob);
        shotUrlRef.current = url;
        shotBlobRef.current = blob;
        setShotUrl(url);
        setPhase("review");
        stopStream();
      },
      "image/jpeg",
      0.92,
    );
  }

  function retake() {
    clearShot();
    void start();
  }

  function usePhoto() {
    const blob = shotBlobRef.current;
    if (!blob) return;
    const file = new File([blob], `card-${Date.now()}.jpg`, { type: "image/jpeg" });
    stopStream();
    clearShot();
    onCapture(file);
  }

  function close() {
    stopStream();
    clearShot();
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-900/95">
      <div className="flex items-center justify-between px-4 py-3 text-white">
        <span className="text-sm font-medium">Photograph the insurance card</span>
        <button onClick={close} aria-label="Close" className="rounded p-1 hover:bg-white/10">
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="relative flex flex-1 items-center justify-center overflow-hidden p-4">
        {/* live preview */}
        <video
          ref={videoRef}
          playsInline
          muted
          className={[
            "max-h-full max-w-full rounded-lg bg-black",
            phase === "live" ? "block" : "hidden",
          ].join(" ")}
        />
        {/* captured still */}
        {phase === "review" && shotUrl && (
          <img
            src={shotUrl}
            alt="Captured card"
            className="max-h-full max-w-full rounded-lg object-contain"
          />
        )}
        {phase === "loading" && <p className="text-sm text-white/70">Starting the camera…</p>}
        {phase === "error" && (
          <div className="max-w-sm rounded-lg bg-white p-5 text-center">
            <p className="text-sm text-slate-700">{errorMsg}</p>
            <button
              onClick={() => {
                stopStream();
                clearShot();
                onPickFile();
              }}
              className="mt-4 inline-flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
            >
              <Upload className="h-4 w-4" />
              Choose a saved image
            </button>
          </div>
        )}

        {phase === "live" && (
          <div className="pointer-events-none absolute inset-8 rounded-xl border-2 border-dashed border-white/40" />
        )}
      </div>

      {/* controls */}
      <div className="flex items-center justify-center gap-4 px-4 pb-8 pt-3">
        {phase === "live" && (
          <button
            onClick={capture}
            className="inline-flex items-center gap-2 rounded-full bg-white px-6 py-3 text-sm font-semibold text-slate-900 hover:bg-slate-100"
          >
            <Camera className="h-5 w-5" />
            Capture
          </button>
        )}
        {phase === "review" && (
          <>
            <button
              onClick={retake}
              className="inline-flex items-center gap-2 rounded-full border border-white/40 px-5 py-3 text-sm font-semibold text-white hover:bg-white/10"
            >
              <RotateCcw className="h-4 w-4" />
              Retake
            </button>
            <button
              onClick={usePhoto}
              className="inline-flex items-center gap-2 rounded-full bg-brand-600 px-6 py-3 text-sm font-semibold text-white hover:bg-brand-700"
            >
              Use this photo
            </button>
          </>
        )}
      </div>

      <canvas ref={canvasRef} className="hidden" />
    </div>
  );
}
