#!/usr/bin/env python3
"""Download all V2 models for the attendance system."""
import os
import sys

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)
os.chdir(MODELS_DIR)

# --- 1. YOLOv12l ---
yolo_path = os.path.join(MODELS_DIR, "yolov12l.pt")
if not os.path.exists(yolo_path) or os.path.getsize(yolo_path) < 1000:
    print("=== Downloading YOLOv12l ===")
    try:
        from ultralytics import YOLO
        model = YOLO("yolov12l")
        # ultralytics downloads to cwd or ~/.config; find it
        import glob
        candidates = glob.glob("yolov12l*.pt") + glob.glob(os.path.expanduser("~/.config/Ultralytics/**/yolov12l*"), recursive=True)
        if not candidates:
            # Check if it downloaded to the cwd
            candidates = [f for f in os.listdir(".") if "yolov12" in f.lower()]
        for c in candidates:
            print(f"  Found: {c} ({os.path.getsize(c)} bytes)")
        # The YOLO constructor should have created yolov12l.pt in cwd
        if os.path.exists("yolov12l.pt") and os.path.getsize("yolov12l.pt") > 1000:
            print(f"  OK: yolov12l.pt ({os.path.getsize('yolov12l.pt')} bytes)")
        else:
            print("  WARNING: yolov12l.pt not found in cwd after YOLO() call")
    except Exception as e:
        print(f"  ERROR: {e}")
        # Fallback: try direct wget via subprocess
        import subprocess
        # Try the v8.4.0 tag which should have yolov12 models
        for tag in ["v8.4.0", "v8.3.40", "v8.3.0"]:
            url = f"https://github.com/ultralytics/assets/releases/download/{tag}/yolov12l.pt"
            print(f"  Trying wget from {url}...")
            result = subprocess.run(["wget", "-q", url, "-O", "yolov12l.pt"], capture_output=True)
            if os.path.exists("yolov12l.pt") and os.path.getsize("yolov12l.pt") > 1000:
                print(f"  OK: yolov12l.pt ({os.path.getsize('yolov12l.pt')} bytes)")
                break
            else:
                print(f"  Failed with tag {tag}")
                if os.path.exists("yolov12l.pt"):
                    os.remove("yolov12l.pt")
else:
    print(f"=== YOLOv12l already exists: {os.path.getsize(yolo_path)} bytes ===")

# --- Summary ---
print("\n=== Models directory contents ===")
for f in sorted(os.listdir(MODELS_DIR)):
    fp = os.path.join(MODELS_DIR, f)
    if os.path.isfile(fp):
        size_mb = os.path.getsize(fp) / (1024 * 1024)
        print(f"  {f}: {size_mb:.1f} MB")
