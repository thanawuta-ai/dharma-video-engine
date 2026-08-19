import os
import uuid
import urllib.parse
import asyncio
import aiohttp
import requests
import subprocess
import edge_tts
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# 1. ฟังก์ชันสร้างเสียงพากย์ฟรีด้วย Edge-TTS
async def generate_voice(text, voice_name, output_path):
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate="+0%", pitch="+0Hz")
    await communicate.save(output_path)
    return output_path

# 2. ฟังก์ชันดาวน์โหลด/เจนภาพ FLUX 
async def fetch_image(session, prompt, filepath, hf_token, idx, width, height):
    aspect_hint = "portrait 9:16 vertical" if height > width else "widescreen 16:9 horizontal"
    endpoints = [
        "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
        "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell",
        "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"
    ]
    headers = {"Authorization": f"Bearer {hf_token}", "x-wait-for-model": "true"}
    payload = {"inputs": f"{prompt}, {aspect_hint}, cinematic masterpiece, highly detailed, 4k"}

    # ลำดับที่ 1: เจนผ่าน Hugging Face Router
    for url in endpoints:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as response:
                if response.status == 200:
                    content = await response.read()
                    if len(content) > 5000:
                        with open(filepath, "wb") as f:
                            f.write(content)
                        return filepath
        except Exception as e:
            print(f"HF Scene {idx+1} Warning: {e}")

    # ลำดับที่ 2: ระบบสำรองอัตโนมัติ ป้องกันเซิร์ฟเวอร์ค้าง
    try:
        encoded_prompt = urllib.parse.quote(prompt)
        backup_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true&model=flux&seed={6000 + idx}"
        async with session.get(backup_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                with open(filepath, "wb") as f:
                    f.write(await response.read())
                return filepath
    except Exception as e:
        print(f"Backup Error scene {idx+1}: {e}")

    return None

async def generate_all_images(prompts, job_id, hf_token, width, height):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for idx, prompt in enumerate(prompts):
            img_path = os.path.join(TEMP_DIR, f"{job_id}_img_{idx}.jpg")
            tasks.append(fetch_image(session, prompt, img_path, hf_token, idx, width, height))
        results = await asyncio.gather(*tasks)
    return [r for r in results if r and os.path.exists(r)]

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
    return jsonify({"status": "healthy", "service": "Unified Video Rendering Engine"}), 200

@app.route("/render", methods=["POST"])
def render_video():
    data = request.get_json(force=True)
    prompts = data.get("prompts", [])
    hf_token = data.get("hf_token", "")
    mode = data.get("mode", "short")  # ค่า "short" = 9:16 แนวตั้ง | "long" = 16:9 แนวนอน
    
    if mode == "short":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    story_script = data.get("story_script")
    audio_url = data.get("audio_url")
    voice = data.get("voice", "th-TH-NiwatNeural")

    if not prompts or not hf_token:
        return jsonify({"error": "prompts and hf_token are required"}), 400
    if not story_script and not audio_url:
        return jsonify({"error": "Either story_script or audio_url is required"}), 400

    job_id = str(uuid.uuid4())[:8]
    audio_path = os.path.join(TEMP_DIR, f"{job_id}_audio.mp3")
    final_video_name = f"video_{job_id}.mp4"
    final_video_path = os.path.join(OUTPUT_DIR, final_video_name)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # จัดการเสียงพากย์
        if story_script:
            loop.run_until_complete(generate_voice(story_script, voice, audio_path))
        elif audio_url:
            res = requests.get(audio_url, timeout=30)
            res.raise_for_status()
            with open(audio_path, "wb") as f:
                f.write(res.content)

        audio_duration = get_audio_duration(audio_path)

        # จัดการรูปภาพ
        image_files = loop.run_until_complete(generate_all_images(prompts, job_id, hf_token, width, height))
        loop.close()

        if len(image_files) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(audio_duration / len(image_files), 1.0)

        # ตัดต่อด้วย FFmpeg
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
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            final_video_path
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        video_url = f"https://{request.host}/outputs/{final_video_name}"
        return jsonify({
            "status": "success",
            "mode": mode,
            "video_url": video_url,
            "duration_seconds": audio_duration,
            "images_rendered": len(image_files)
        }), 200

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
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=False)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
