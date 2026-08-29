#!/usr/bin/env python3
"""Worker curto de OpenCV/Numpy: termina e devolve toda a RAM ao sistema."""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import json
import sys
import cv2
import detector


def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        payload = json.load(f)
    frame_path = str(payload.get("frame_path") or "")
    img = cv2.imread(frame_path)
    if img is None:
        raise RuntimeError("O OpenCV não conseguiu abrir o frame de análise.")
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass
    h, w = img.shape[:2]
    box = detector.detectar_card(img)
    conf = detector.confianca(img, box)
    print(json.dumps({
        "ok": True,
        "frame_w": int(w),
        "frame_h": int(h),
        "box": list(box) if box is not None else None,
        "confianca": float(conf),
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "erro": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
