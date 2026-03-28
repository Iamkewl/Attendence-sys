"""IoT Camera Simulator.

Connects to the WebSocket control channel and waits for 'CAPTURE' signals.
When triggered, takes a snapshot from your webcam and sends it to the ingest API.

Usage:
  python -m scripts.camera_client
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid

import cv2
import httpx
try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    exit(1)

# Configuration matching the database seed
API_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"
DEVICE_ID = 1          # Matches our seeded Cam-A1 (id=1)
CAMERA_ID = "Cam-A1"
SCHEDULE_ID = 1        # Matches seeded CS-301 schedule
SECRET_KEY = "Device123!"  # Placeholder secret key for HMAC


def compute_signature(image_bytes: bytes, timestamp: str, nonce: str) -> str:
    """Compute HMAC-SHA256 signature matching the ingest.py specification."""
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    payload = f"{DEVICE_ID}:{timestamp}:{image_hash}"
    data = f"{payload}:{nonce}".encode("utf-8")
    return hmac.new(SECRET_KEY.encode("utf-8"), data, hashlib.sha256).hexdigest()


async def upload_snapshot(frame_bytes: bytes):
    """Upload multipart snapshot with HMAC authentication."""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = compute_signature(frame_bytes, timestamp, nonce)

    async with httpx.AsyncClient() as client:
        files = {"image": ("snapshot.jpg", frame_bytes, "image/jpeg")}
        data = {
            "device_id": str(DEVICE_ID),
            "schedule_id": str(SCHEDULE_ID),
            "camera_id": CAMERA_ID,
            "timestamp": timestamp,
            "nonce": nonce
        }
        headers = {"X-Signature": signature}
        
        print(f"[*] Uploading snapshot via API (size: {len(frame_bytes) // 1024} KB)...")
        try:
            res = await client.post(
                f"{API_URL}/api/v1/ingest", 
                data=data, 
                files=files, 
                headers=headers
            )
            print(f"  └─ [+] Ingest response: {res.status_code} - {res.text}")
            if res.status_code == 200:
                print("  └─ [✓] Success! Image dispatched to Celery CV worker. Check dashboard!")
        except Exception as e:
            print(f"  └─ [-] Upload failed: {e}")


async def listen_for_capture_signals():
    """Connect to WebSocket and await CAPTURE triggers."""
    ws_uri = f"{WS_URL}/api/v1/ws/device/{DEVICE_ID}"
    print(f"[*] Connecting to WebSocket: {ws_uri}")
    
    cap = None
    try:
        async with websockets.connect(ws_uri) as ws:
            print("[+] Connected! Waiting for CAPTURE signals from backend server...")
            print("    (Note: APScheduler triggers a capture every 1 minute if backend is running)")
            
            # Initialize laptop webcam (Index 0)
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                print("[-] Could not open webcam (index 0).")
                return
                
            # Quick warm-up
            cap.read()

            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                
                if data.get("action") == "CAPTURE":
                    print("\n[🔊] CAPTURE signal received from Server!")
                    
                    ret, frame = cap.read()
                    if ret:
                        # Encode as JPG
                        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                        # Upload asynchronously so we keep listening
                        asyncio.create_task(upload_snapshot(buffer.tobytes()))
                    else:
                        print("[-] Failed to capture frame from webcam")
                        
    except websockets.exceptions.ConnectionClosed:
        print("[-] WebSocket connection closed by server.")
    except Exception as e:
        print(f"[-] WebSocket error: {e}")
    finally:
        if cap and cap.isOpened():
            cap.release()
            print("[*] Webcam released.")


if __name__ == "__main__":
    print("=== IoT Camera Simulator ===")
    asyncio.run(listen_for_capture_signals())
