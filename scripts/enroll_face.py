"""Enroll a student face from an image.

Usage:
  python -m scripts.enroll_face path/to/photo.jpg --name "John Doe" --enrollment "2024005" --email "john@test.io"
"""

import argparse
import asyncio
import os
import sys

import cv2
import httpx

# Add project root to path so we can import backend packages
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.ai_pipeline import get_ai_pipeline


async def main():
    parser = argparse.ArgumentParser(description="Enroll a student face from an image")
    parser.add_argument("image_path", help="Path to the face image")
    parser.add_argument("--name", required=True, help="Student full name")
    parser.add_argument("--enrollment", required=True, help="Enrollment number")
    parser.add_argument("--email", required=True, help="Student email")
    parser.add_argument("--api-url", default="http://localhost:8000", help="API base URL")
    args = parser.parse_args()

    # 1. Load Image
    print(f"[*] Loading image: {args.image_path}")
    image = cv2.imread(args.image_path)
    if image is None:
        print("[-] Failed to load image. Check the path.")
        return

    # 2. Extract Embedding using AI Pipeline
    print("[*] Initializing AI Pipeline models (this may take a few seconds)...")
    pipeline = get_ai_pipeline()
    
    print("[*] Detecting faces and extracting 512-d embedding...")
    matches = pipeline.process_frame(image)
    if not matches:
        print("[-] No face detected in the image.")
        return
    if len(matches) > 1:
        print("[-] Multiple faces detected. Please use a clear portrait image with only ONE face.")
        return

    match = matches[0]
    embedding = match.embedding
    print(f"[+] Successfully extracted {len(embedding)}-d embedding. (Confidence: {match.confidence:.2f})")

    # 3. Authenticate with API
    print(f"[*] Authenticating with API at {args.api_url}...")
    async with httpx.AsyncClient() as client:
        # Use our seeded admin user
        auth_res = await client.post(
            f"{args.api_url}/api/v1/auth/login",
            json={"email": "admin@attendai.io", "password": "Admin123!"}
        )
        if auth_res.status_code != 200:
            print(f"[-] Login failed. Ensure backend is running and seeded. Error: {auth_res.text}")
            return
            
        token = auth_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 4. Create Student Record
        print(f"[*] Creating student record in database: {args.name}...")
        student_res = await client.post(
            f"{args.api_url}/api/v1/students",
            headers=headers,
            json={
                "enrollment_number": args.enrollment,
                "full_name": args.name,
                "email": args.email
            }
        )
        if student_res.status_code not in (200, 201):
            print(f"[-] Failed to create student: {student_res.text}")
            return
            
        student_id = student_res.json()["id"]

        # 5. Upload Raw Embedding Vector
        print("[*] Uploading embedding vector to database via /enroll endpoint...")
        enroll_res = await client.post(
            f"{args.api_url}/api/v1/students/enroll",
            headers=headers,
            json={
                "student_id": student_id,
                "pose_label": "frontal",
                "resolution": "high",
                "model_name": "arcface",
                "embedding": embedding.tolist()
            }
        )
        
        if enroll_res.status_code in (200, 201):
            print(f"✅ Success! {args.name} is fully enrolled (ID: {student_id}) and ready for recognition.")
        else:
            print(f"[-] Failed to upload embedding: {enroll_res.text}")


if __name__ == "__main__":
    asyncio.run(main())
