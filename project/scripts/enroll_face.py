"""Create a student and enroll from face images via API.

Usage:
  python -m scripts.enroll_face photo1.jpg photo2.jpg --name "John Doe"
"""

import argparse
import asyncio
from pathlib import Path

import httpx


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a student and enroll face images"
    )
    parser.add_argument(
        "image_paths",
        nargs="+",
        help="Path(s) to enrollment images",
    )
    parser.add_argument("--name", required=True, help="Student full name")
    parser.add_argument("--department", default=None, help="Department")
    parser.add_argument(
        "--enrollment-year",
        dest="enrollment_year",
        type=int,
        default=None,
        help="Enrollment year (e.g. 2026)",
    )
    parser.add_argument(
        "--pose-label",
        choices=["frontal", "left_34", "right_34"],
        default="frontal",
        help="Pose label for uploaded images",
    )
    parser.add_argument(
        "--use-adaface",
        action="store_true",
        help="Also store AdaFace embeddings",
    )
    parser.add_argument(
        "--manual-pose",
        action="store_true",
        help="Use --pose-label directly instead of automatic pose estimation",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="API base URL",
    )
    parser.add_argument(
        "--admin-email",
        default="admin@attendai.io",
        help="Admin login email",
    )
    parser.add_argument(
        "--admin-password",
        default="Admin123!",
        help="Admin login password",
    )
    args = parser.parse_args()

    image_files: list[tuple[str, tuple[str, bytes, str]]] = []
    for raw_path in args.image_paths:
        image_path = Path(raw_path)
        if not image_path.exists() or not image_path.is_file():
            print(f"[-] File not found: {image_path}")
            return
        image_files.append(
            (
                "images",
                (
                    image_path.name,
                    image_path.read_bytes(),
                    "image/jpeg",
                ),
            )
        )

    async with httpx.AsyncClient(timeout=45.0) as client:
        auth_res = await client.post(
            f"{args.api_url}/api/v1/auth/login",
            json={"email": args.admin_email, "password": args.admin_password},
        )
        if auth_res.status_code != 200:
            print(f"[-] Login failed: {auth_res.text}")
            return

        token = auth_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        student_res = await client.post(
            f"{args.api_url}/api/v1/students",
            headers=headers,
            json={
                "name": args.name,
                "department": args.department,
                "enrollment_year": args.enrollment_year,
            },
        )
        if student_res.status_code not in (200, 201):
            print(f"[-] Failed to create student: {student_res.text}")
            return

        student = student_res.json()
        student_id = student["id"]
        print(f"[+] Created student {student['name']} (ID: {student_id})")

        enroll_res = await client.post(
            f"{args.api_url}/api/v1/students/{student_id}/enroll/images",
            headers=headers,
            data={
                "pose_label": args.pose_label,
                "auto_pose": "false" if args.manual_pose else "true",
                "use_adaface": "true" if args.use_adaface else "false",
            },
            files=image_files,
        )
        if enroll_res.status_code != 200:
            print(f"[-] Enrollment failed: {enroll_res.text}")
            return

        summary = enroll_res.json()
        print(f"[+] {summary['message']}")
        print(
            "[+] Embeddings: "
            f"{summary['total_embeddings']}/{summary['required_embeddings']}"
        )
        for check in summary.get("checks", []):
            state = "accepted" if check.get("accepted") else "rejected"
            reason = check.get("reason")
            if reason:
                print(f"    - {check['filename']}: {state} ({reason})")
            else:
                print(f"    - {check['filename']}: {state}")


if __name__ == "__main__":
    asyncio.run(main())
