import os
import uuid
import asyncio
import aiohttp
import requests
import subprocess
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

async def download_image(session, url, filepath):
    """ดาวน์โหลดภาพแบบ Asynchronous พร้อม Timeout 25 วินาที"""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        clean_url = str(url).strip()
        async with session.get(clean_url, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as response:
            if response.status == 200:
                with open(filepath, "wb") as f:
                    f.write(await response.read())
                return filepath
            else:
                print(f"Image download status {response.status} for: {clean_url}")
    except Exception as e:
        print(f"Failed to download image {url}: {e}")
    return None

async def download_all_images(image_urls, job_id):
    """ดาวน์โหลดทุกภาพพร้อมกันแบบคู่ขนาน (Parallel)"""
    async with aiohttp.ClientSession() as session:
        tasks = []
        for idx, url in enumerate(image_urls):
            img_path = os.path.join(TEMP_DIR, f"{job_id}_img_{idx}.jpg")
            tasks.append(download_image(session, url, img_path))
        
        results = await asyncio.gather(*tasks)
        image_files = [res for res in results if res is not None and os.path.exists(res)]
    return image_files

def get_audio_duration(audio_path):
    """ดึงความยาวของไฟล์เสียงด้วย ffprobe"""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of",
        "default=noprint_wrappers=1:nokey=1", audio_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "healthy", "service": "dharma-video-engine"}), 200

@app.route("/render", methods=["POST"])
def render_video():
    data = request.get_json(force=True)
    raw_audio_url = data.get("audio_url")
    image_urls = data.get("image_urls", [])

    if not raw_audio_url or not image_urls:
        return jsonify({"error": "audio_url and image_urls are required"}), 400

    # ตัดช่องว่างหน้า-หลัง URL อัตโนมัติป้องกัน 500 error
    audio_url = str(raw_audio_url).strip()

    job_id = str(uuid.uuid4())[:8]
    audio_path = os.path.join(TEMP_DIR, f"{job_id}_audio.mp3")
    final_video_name = f"final_{job_id}.mp4"
    final_video_path = os.path.join(OUTPUT_DIR, final_video_name)

    try:
        # 1. ดาวน์โหลดไฟล์เสียง
        print(f"[{job_id}] Downloading audio from: {audio_url}")
        audio_res = requests.get(audio_url, timeout=30)
        audio_res.raise_for_status()
        with open(audio_path, "wb") as f:
            f.write(audio_res.content)

        audio_duration = get_audio_duration(audio_path)
        print(f"[{job_id}] Audio duration: {audio_duration:.2f}s")

        # 2. ดาวน์โหลดรูปภาพทั้งหมดพร้อมกัน (Parallel)
        print(f"[{job_id}] Downloading {len(image_urls)} images in parallel...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        image_files = loop.run_until_complete(download_all_images(image_urls, job_id))
        loop.close()

        if not image_files:
            return jsonify({"error": "Failed to download any images"}), 500

        print(f"[{job_id}] Successfully downloaded {len(image_files)} images.")

        # คำนวณเวลาแสดงผลต่อภาพ
        duration_per_image = max(audio_duration / len(image_files), 1.0)

        # 3. สร้างไฟล์ Concat สำหรับ FFmpeg (Low RAM)
        concat_file = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
        with open(concat_file, "w") as f:
            for img in image_files:
                f.write(f"file '{img}'\n")
                f.write(f"duration {duration_per_image:.2f}\n")
            f.write(f"file '{image_files[-1]}'\n")

        # 4. รวมไฟล์ด้วย FFmpeg (Ultra-Fast & Low Memory)
        print(f"[{job_id}] Rendering video via FFmpeg...")
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio_path,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            final_video_path
        ]
        
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        video_url = f"{request.host_url.rstrip('/')}/outputs/{final_video_name}"
        print(f"[{job_id}] Render completed successfully: {video_url}")

        return jsonify({
            "status": "success",
            "video_url": video_url,
            "duration": audio_duration
        }), 200

    except Exception as e:
        print(f"[{job_id}] Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

    finally:
        # เคลียร์ไฟล์ชั่วคราว
        for f in os.listdir(TEMP_DIR):
            if f.startswith(job_id):
                try:
                    os.remove(os.path.join(TEMP_DIR, f))
                except Exception:
                    pass

@app.route("/outputs/<path:filename>", methods=["GET"])
def get_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
