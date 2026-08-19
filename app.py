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
FONT_DIR = os.path.join(BASE_DIR, "fonts")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(FONT_DIR, exist_ok=True)

# 1. ดาวน์โหลดฟอนต์ภาษาไทยมาตรฐานอัตโนมัติ (แก้ปัญหาลายน้ำ/ซับไตเติลภาษาต่างด้าว)
FONT_PATH = os.path.join(FONT_DIR, "THSarabunNew.ttf")
def ensure_thai_font():
    if not os.path.exists(FONT_PATH):
        font_url = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Bold.ttf"
        try:
            r = requests.get(font_url, timeout=30)
            if r.status_code == 200:
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"Font download error: {e}")

ensure_thai_font()

# 2. ฟังก์ชันเสียงพากย์ไทยนุ่มนวล
async def generate_voice(text, voice_name, output_path):
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate="-5%", pitch="-1Hz")
    await communicate.save(output_path)
    return output_path

# 3. ฟังก์ชันดึงภาพแยก Seed ทุกฉาก (ป้องกันภาพซ้ำ)
async def fetch_image(session, prompt, filepath, hf_token, idx, width, height):
    aspect_hint = "vertical portrait 9:16" if height > width else "widescreen horizontal 16:9"
    enhanced_prompt = f"{prompt}, tranquil Buddhist scene, cinematic soft lighting, masterpiece, 8k --no text, watermark"
    
    endpoints = [
        "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
        "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
    ]
    headers = {"Authorization": f"Bearer {hf_token}", "x-wait-for-model": "true"}
    payload = {"inputs": f"{enhanced_prompt}, {aspect_hint}"}

    for url in endpoints:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as response:
                if response.status == 200:
                    content = await response.read()
                    if len(content) > 10000:
                        with open(filepath, "wb") as f:
                            f.write(content)
                        return filepath
        except Exception as e:
            print(f"HF Scene {idx+1} Warning: {e}")

    # Fallback คุณภาพสูง พร้อม Random Seed ให้แต่ละภาพไม่ซ้ำกันเด็ดขาด
    try:
        encoded_prompt = urllib.parse.quote(f"peaceful Buddhist scene, {prompt}, masterpiece, 8k")
        backup_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true&model=flux&seed={9100 + (idx * 137)}"
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
            img_path = os.path.join(TEMP_DIR, f"{job_id}_img_{idx:02d}.jpg")
            tasks.append(fetch_image(session, prompt, img_path, hf_token, idx, width, height))
        results = await asyncio.gather(*tasks)
    
    # เรียงลำดับไฟล์ภาพตามฉาก 0 ถึง N
    valid_images = [r for r in sorted(results, key=lambda x: str(x)) if r and os.path.exists(r)]
    return valid_images

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
    return jsonify({"status": "healthy", "service": "Unified Dharma Video Engine"}), 200

@app.route("/render", methods=["POST"])
def render_video():
    ensure_thai_font()
    data = request.get_json(force=True)
    prompts = data.get("prompts", [])
    hf_token = data.get("hf_token", "")
    mode = data.get("mode", "short")
    watermark_text = data.get("watermark", "- บารมี พระใหม่ -")
    story_script = data.get("story_script")
    voice = data.get("voice", "th-TH-PremwadeeNeural")

    if mode == "short":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    if not prompts or not hf_token or not story_script:
        return jsonify({"error": "prompts, hf_token, and story_script are required"}), 400

    job_id = str(uuid.uuid4())[:8]
    audio_path = os.path.join(TEMP_DIR, f"{job_id}_audio.mp3")
    final_video_name = f"video_{job_id}.mp4"
    final_video_path = os.path.join(OUTPUT_DIR, final_video_name)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 1. สร้างเสียงพากย์
        loop.run_until_complete(generate_voice(story_script, voice, audio_path))
        audio_duration = get_audio_duration(audio_path)

        # 2. สร้างภาพทุกฉาก
        image_files = loop.run_until_complete(generate_all_images(prompts, job_id, hf_token, width, height))
        loop.close()

        if len(image_files) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(audio_duration / len(image_files), 2.0)

        # 3. สร้าง Concat List สำหรับภาพสลับฉาก
        concat_file = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
        with open(concat_file, "w") as f:
            for img in image_files:
                f.write(f"file '{img}'\n")
                f.write(f"duration {duration_per_image:.2f}\n")
            f.write(f"file '{image_files[-1]}'\n")

        # 4. Filter FFmpeg: แสดงผลภาษาไทยผ่านฟอนต์ Sarabun + จัดตำแหน่งลายน้ำ
        font_arg = f":fontfile='{FONT_PATH}'" if os.path.exists(FONT_PATH) else ""
        vf_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"drawtext=text='{watermark_text}'{font_arg}:fontcolor=white:fontsize=52:box=1:boxcolor=black@0.5:boxborderw=12:x=(w-text_w)/2:y=140,"
            f"format=yuv420p"
        )

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio_path,
            "-vf", vf_filter,
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
