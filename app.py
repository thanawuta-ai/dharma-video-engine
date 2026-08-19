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
AUDIO_DIR = os.path.join(BASE_DIR, "assets")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(FONT_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

FONT_PATH = os.path.join(FONT_DIR, "Sarabun-Bold.ttf")
BGM_PATH = os.path.join(AUDIO_DIR, "meditation_bgm.mp3")

# 1. ดาวน์โหลดฟอนต์ภาษาไทย และเพลงบรรเลงธรรมะคลอเบาๆ
def ensure_assets():
    if not os.path.exists(FONT_PATH):
        try:
            r = requests.get("https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Bold.ttf", timeout=30)
            if r.status_code == 200:
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"Font Error: {e}")

    if not os.path.exists(BGM_PATH):
        try:
            # เพลงบรรเลงเปียโน+ธรรมชาติ สงบนุ่มนวล (Royalty Free)
            r = requests.get("https://cdn.pixabay.com/download/audio/2022/05/16/audio_c89b022b7a.mp3?filename=meditation-piano-9679.mp3", timeout=30)
            if r.status_code == 200:
                with open(BGM_PATH, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"BGM Error: {e}")

ensure_assets()

# 2. ฟังก์ชันสร้างเสียงพากย์
async def generate_voice(text, voice_name, output_path):
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate="-5%", pitch="-1Hz")
    await communicate.save(output_path)
    return output_path

# 3. เจนภาพพระและธรรมะสวยงาม คมชัด ไม่หลุดธีม
async def fetch_image(session, prompt, filepath, hf_token, idx, width, height):
    aspect_hint = "vertical 9:16 portrait" if height > width else "horizontal 16:9 widescreen"
    # เสริมคีย์เวิร์ดบังคับให้ได้ภาพพระ วัด และบรรยากาศธรรมะที่งดงาม
    enhanced_prompt = f"Buddhism dharma art, {prompt}, serene Buddhist monastery, golden aura, peaceful monk or Buddha, cinematic lighting, 8k masterpiece, photorealistic --no modern buildings, cars, distorted, watermark, text"
    
    # ดึงภาพจาก FLUX คุณภาพสูงโดยตรง
    try:
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        pollinations_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true&model=flux&seed={5550 + (idx * 211)}"
        async with session.get(pollinations_url, timeout=aiohttp.ClientTimeout(total=40)) as response:
            if response.status == 200:
                content = await response.read()
                if len(content) > 10000:
                    with open(filepath, "wb") as f:
                        f.write(content)
                    return filepath
    except Exception as e:
        print(f"Flux Error scene {idx+1}: {e}")

    # สำรองผ่าน Hugging Face
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
        except Exception:
            pass

    return None

async def generate_all_images(prompts, job_id, hf_token, width, height):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for idx, prompt in enumerate(prompts):
            img_path = os.path.join(TEMP_DIR, f"{job_id}_img_{idx:02d}.jpg")
            tasks.append(fetch_image(session, prompt, img_path, hf_token, idx, width, height))
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
    return jsonify({"status": "healthy", "service": "Dharma Video Engine with BGM & Subs"}), 200

@app.route("/render", methods=["POST"])
def render_video():
    ensure_assets()
    data = request.get_json(force=True)
    prompts = data.get("prompts", [])
    hf_token = data.get("hf_token", "")
    mode = data.get("mode", "short")
    watermark_text = data.get("watermark", "- บารมี พระใหม่ -")
    story_script = data.get("story_script")
    voice = data.get("voice", "th-TH-PremwadeeNeural")

    if mode == "short":
        width, height = 1080, 1920
        font_size_wm = 50
        font_size_sub = 44
        y_sub = 1400  # วางซับไตเติลให้อยู่กึ่งกลางล่างระดับสายตา
    else:
        width, height = 1920, 1080
        font_size_wm = 44
        font_size_sub = 40
        y_sub = 900

    if not prompts or not story_script:
        return jsonify({"error": "prompts and story_script are required"}), 400

    job_id = str(uuid.uuid4())[:8]
    voice_audio_path = os.path.join(TEMP_DIR, f"{job_id}_voice.mp3")
    final_video_name = f"video_{job_id}.mp4"
    final_video_path = os.path.join(OUTPUT_DIR, final_video_name)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 1. สร้างเสียงคนพากย์
        loop.run_until_complete(generate_voice(story_script, voice, voice_audio_path))
        voice_duration = get_audio_duration(voice_audio_path)

        # 2. เจนภาพพระ/ธรรมะแยกฉาก
        image_files = loop.run_until_complete(generate_all_images(prompts, job_id, hf_token, width, height))
        loop.close()

        if len(image_files) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(voice_duration / len(image_files), 2.0)

        # 3. ไฟล์ Concat สำหรับเปลี่ยนรูปภาพตามจังหวะ
        concat_file = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
        with open(concat_file, "w") as f:
            for img in image_files:
                f.write(f"file '{img}'\n")
                f.write(f"duration {duration_per_image:.2f}\n")
            f.write(f"file '{image_files[-1]}'\n")

        # 4. เตรียมข้อความซับไตเติลภาษาไทย (ตัดบรรทัดให้อ่านง่าย)
        clean_sub_text = story_script.replace("'", "").replace('"', '').replace("\n", " ")
        # ย่อข้อความให้แสดงผลเป็นท่อนๆ หรือแสดงสรุปข้อคิด
        if len(clean_sub_text) > 120:
            clean_sub_text = clean_sub_text[:115] + "..."

        font_arg = f":fontfile='{FONT_PATH}'" if os.path.exists(FONT_PATH) else ""

        # 5. รวม Video Filter (ภาพ + ลายน้ำบน + ซับไตเติลล่าง)
        vf_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            # ลายน้ำบน
            f"drawtext=text='{watermark_text}'{font_arg}:fontcolor=yellow@0.9:fontsize={font_size_wm}:box=1:boxcolor=black@0.5:boxborderw=12:x=(w-text_w)/2:y=120,"
            # คำบรรยายซับไตเติลล่าง
            f"drawtext=text='{clean_sub_text}'{font_arg}:fontcolor=white:fontsize={font_size_sub}:box=1:boxcolor=black@0.6:boxborderw=15:x=(w-text_w)/2:y={y_sub},"
            f"format=yuv420p"
        )

        # 6. ประกอบเสียงพากย์ + เพลงบรรเลงคลอเบาๆ (-20dB)
        if os.path.exists(BGM_PATH):
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_file,
                "-i", voice_audio_path,
                "-stream_loop", "-1", "-i", BGM_PATH,
                "-filter_complex",
                f"[0:v]{vf_filter}[v];[2:a]volume=0.18[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]",
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-movflags", "+faststart",
                final_video_path
            ]
        else:
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
