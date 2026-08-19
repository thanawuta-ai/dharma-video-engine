import os
import uuid
import urllib.parse
import asyncio
import aiohttp
import requests
import subprocess
import textwrap
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

FONT_PATH = os.path.join(FONT_DIR, "Sarabun-Bold.ttf")

def ensure_font():
    if not os.path.exists(FONT_PATH):
        try:
            r = requests.get("https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Bold.ttf", timeout=20)
            if r.status_code == 200:
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"Font Error: {e}")

ensure_font()

async def generate_voice(text, voice_name, output_path):
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate="-5%", pitch="-1Hz")
    await communicate.save(output_path)
    return output_path

async def fetch_single_image(session, prompt, filepath, hf_token, idx, width, height):
    dharma_themes = [
        "majestic golden Buddha statue sitting in peaceful meditation, surrounded by tranquil rainforest, golden sunlight rays, 8k cinematic masterpiece",
        "serene Thai Buddhist monk in saffron robe walking slowly in ancient bamboo forest garden, soft warm sunset light, photorealistic, 8k",
        "ancient golden Buddhist temple on mountain summit above sea of clouds, magnificent morning sunrise, breathtaking scenery, 8k",
        "radiant pink lotus flower blooming in calm temple pond, sparkling water reflections, tranquil zen atmosphere, 8k",
        "peaceful golden Buddha face in ancient wooden temple, warm glowing candlelight lanterns, holy and tranquil aura, 8k"
    ]
    theme = dharma_themes[idx % len(dharma_themes)]
    final_prompt = f"{theme}, {prompt} --no modern, distorted, text, watermark"

    # ช่องทางที่ 1: Hugging Face Router
    if hf_token:
        endpoints = [
            "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
            "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell",
            "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"
        ]
        headers = {"Authorization": f"Bearer {hf_token}", "x-wait-for-model": "true"}
        payload = {"inputs": f"{final_prompt}, vertical 9:16"}

        for url in endpoints:
            try:
                async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=25)) as response:
                    if response.status == 200:
                        content = await response.read()
                        if len(content) > 5000:
                            with open(filepath, "wb") as f:
                                f.write(content)
                            return filepath
            except Exception:
                pass

    # ช่องทางที่ 2: Pollinations AI
    try:
        encoded = urllib.parse.quote(final_prompt)
        polli_url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true&seed={15000 + idx * 73}"
        async with session.get(polli_url, timeout=aiohttp.ClientTimeout(total=25)) as response:
            if response.status == 200:
                content = await response.read()
                if len(content) > 5000:
                    with open(filepath, "wb") as f:
                        f.write(content)
                    return filepath
    except Exception:
        pass

    # ช่องทางที่ 3: ระบบสำรองฉุกเฉิน (สร้างภาพพื้นหลังบรรยากาศธรรมะอัตโนมัติ ไม่ให้ค้าง)
    colors = ["#1a2412", "#24180d", "#1c1427", "#122024", "#261d12"]
    bg_color = colors[idx % len(colors)]
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c={bg_color}:s={width}x{height}:d=1",
        "-vframes", "1", filepath
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return filepath

async def generate_all_images(prompts, job_id, hf_token, width, height):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for idx in range(len(prompts)):
            p = prompts[idx] if idx < len(prompts) else f"Scene {idx+1}"
            img_path = os.path.join(TEMP_DIR, f"{job_id}_img_{idx:02d}.jpg")
            tasks.append(fetch_single_image(session, p, img_path, hf_token, idx, width, height))
        results = await asyncio.gather(*tasks)
    return [r for r in sorted(results, key=lambda x: str(x)) if r and os.path.exists(r)]

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
    ensure_font()
    data = request.get_json(force=True)
    prompts = data.get("prompts", [])
    hf_token = data.get("hf_token", "")
    mode = data.get("mode", "short")
    watermark_text = data.get("watermark", "- บารมี พระใหม่ -")
    story_script = data.get("story_script")
    voice = data.get("voice", "th-TH-PremwadeeNeural")

    if not prompts or not story_script:
        return jsonify({"error": "prompts and story_script are required"}), 400

    # ปรับความละเอียดให้เหมาะสมเพื่อเรนเดอร์เร็วและประหยัดหน่วยความจำ
    if mode == "short":
        width, height = 720, 1280
        font_size_wm = 34
        font_size_sub = 26
        y_sub = 1020
    else:
        width, height = 1280, 720
        font_size_wm = 32
        font_size_sub = 24
        y_sub = 600

    job_id = str(uuid.uuid4())[:8]
    voice_audio_path = os.path.join(TEMP_DIR, f"{job_id}_voice.mp3")
    final_video_name = f"video_{job_id}.mp4"
    final_video_path = os.path.join(OUTPUT_DIR, final_video_name)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 1. สร้างเสียงพากย์
        loop.run_until_complete(generate_voice(story_script, voice, voice_audio_path))
        voice_duration = get_audio_duration(voice_audio_path)

        # 2. ดาวน์โหลด/เจนภาพธรรมะครบทุกฉาก
        image_files = loop.run_until_complete(generate_all_images(prompts, job_id, hf_token, width, height))
        loop.close()

        if len(image_files) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(voice_duration / len(image_files), 1.5)

        # 3. จัดทำ Concat List
        concat_file = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
        with open(concat_file, "w") as f:
            for img in image_files:
                f.write(f"file '{img}'\n")
                f.write(f"duration {duration_per_image:.2f}\n")
            f.write(f"file '{image_files[-1]}'\n")

        # 4. คำบรรยายภาษาไทยตัดบรรทัดให้อ่านง่าย
        clean_text = story_script.replace("'", "").replace('"', '').replace("\n", " ")
        wrapped = textwrap.wrap(clean_text, width=30)
        display_sub = "\n".join(wrapped[:2])
        if len(wrapped) > 2:
            display_sub += "..."

        sub_file = os.path.join(TEMP_DIR, f"{job_id}_sub.txt")
        with open(sub_file, "w", encoding="utf-8") as f:
            f.write(display_sub)

        font_arg = f":fontfile='{FONT_PATH}'" if os.path.exists(FONT_PATH) else ""

        # 5. ประกอบคลิปด้วย FFmpeg
        vf_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"drawtext=text='{watermark_text}'{font_arg}:fontcolor=yellow:fontsize={font_size_wm}:box=1:boxcolor=black@0.45:boxborderw=8:x=(w-text_w)/2:y=90,"
            f"drawtext=textfile='{sub_file}'{font_arg}:fontcolor=white:fontsize={font_size_sub}:line_spacing=10:box=1:boxcolor=black@0.6:boxborderw=12:x=(w-text_w)/2:y={y_sub},"
            f"format=yuv420p"
        )

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", voice_audio_path,
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
            "duration_seconds": voice_duration,
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
