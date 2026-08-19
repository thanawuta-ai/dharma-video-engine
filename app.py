import os
import uuid
import urllib.parse
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

async def generate_image_hf(session, prompt, filepath, hf_token, idx):
    """ดึงภาพผ่าน Hugging Face Router API / SDXL / Fallback"""
    endpoints = [
        f"https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
        f"https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell",
        f"https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"
    ]
    headers = {
        "Authorization": f"Bearer {hf_token}",
        "x-wait-for-model": "true"
    }
    payload = {"inputs": prompt}

    # 1. พยายามเรียกผ่าน Hugging Face
    for url in endpoints:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as response:
                if response.status == 200:
                    content = await response.read()
                    if len(content) > 5000:  # มั่นใจว่าเป็นไฟล์รูปจริง
                        with open(filepath, "wb") as f:
                            f.write(content)
                        return filepath
        except Exception as e:
            print(f"HF Error on {url}: {e}")

    # 2. แผนสำรอง: ถ้า HF ติดคิว ให้ใช้ FLUX Engine สำรองทันที งานจะไม่ล่ม
    try:
        encoded_prompt = urllib.parse.quote(prompt)
        backup_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&model=flux&seed={1000 + idx}"
        async with session.get(backup_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                with open(filepath, "wb") as f:
                    f.write(await response.read())
                return filepath
    except Exception as e:
        print(f"Backup Error: {e}")

    return None

async def generate_all_images(prompts, job_id, hf_token):
    image_files = []
    async with aiohttp.ClientSession() as session:
        for idx, prompt in enumerate(prompts):
            img_path = os.path.join(TEMP_DIR, f"{job_id}_img_{idx}.jpg")
            res = await generate_image_hf(session, prompt, img_path, hf_token, idx)
            if res and os.path.exists(res):
                image_files.append(res)
            await asyncio.sleep(0.5)
    return image_files

def get_audio_duration(audio_path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of",
        "default=noprint_wrappers=1:nokey=1", audio_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return float(result.stdout.strip())

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "healthy"}), 200

@app.route("/render", methods=["POST"])
def render_video():
    data = request.get_json(force=True)
    audio_url = str(data.get("audio_url", "")).strip()
    prompts = data.get("prompts", [])
    hf_token = data.get("hf_token")

    if not audio_url or not prompts or not hf_token:
        return jsonify({"error": "audio_url, prompts, and hf_token are required"}), 400

    job_id = str(uuid.uuid4())[:8]
    audio_path = os.path.join(TEMP_DIR, f"{job_id}_audio.mp3")
    final_video_name = f"final_{job_id}.mp4"
    final_video_path = os.path.join(OUTPUT_DIR, final_video_name)

    try:
        # โหลดเสียง
        audio_res = requests.get(audio_url, timeout=30)
        audio_res.raise_for_status()
        with open(audio_path, "wb") as f:
            f.write(audio_res.content)
        audio_duration = get_audio_duration(audio_path)

        # เจนภาพ
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        image_files = loop.run_until_complete(generate_all_images(prompts, job_id, hf_token))
        loop.close()

        if len(image_files) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(audio_duration / len(image_files), 1.0)

        # ตัดต่อวิดีโอ FFmpeg
        concat_file = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
        with open(concat_file, "w") as f:
            for img in image_files:
                f.write(f"file '{img}'\n")
                f.write(f"duration {duration_per_image:.2f}\n")
            f.write(f"file '{image_files[-1]}'\n")

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
        return jsonify({"status": "success", "video_url": video_url, "images_rendered": len(image_files)}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
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
