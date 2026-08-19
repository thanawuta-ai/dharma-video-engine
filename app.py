import os
import uuid
import urllib.parse
import asyncio
import aiohttp
import requests
import subprocess
import textwrap
import edge_tts
from PIL import Image, ImageDraw, ImageFont
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

# 1. ดาวน์โหลดฟอนต์ Sarabun แท้ 100%
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

# 2. ฟังก์ชันเสียงพากย์นุ่มนวล
async def generate_voice(text, voice_name, output_path):
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate="-10%", pitch="-1Hz")
    await communicate.save(output_path)
    return output_path

# 3. เจนภาพพระ/วัด/บรรยากาศธรรมะแท้ๆ
async def fetch_image(session, prompt, filepath, idx, width, height):
    dharma_themes = [
        "majestic golden Buddha statue meditating in tranquil rainforest, morning golden sunbeams, spiritual aura, highly detailed, photorealistic 8k",
        "Thai Buddhist monk in saffron robe walking in ancient bamboo forest path, warm sunset light, calm and serene, 8k",
        "ancient golden Buddhist temple pagoda on mountain summit above white mist, dramatic sunrise sky, masterpiece 8k",
        "sacred blooming pink lotus flower in temple pond, crystal water reflection, tranquility, 8k",
        "peaceful golden Buddha face in serene wooden monastery, glowing oil lamps, atmospheric and peaceful, 8k"
    ]
    theme = dharma_themes[idx % len(dharma_themes)]
    clean_prompt = urllib.parse.quote(f"{theme}, {prompt}")
    
    urls = [
        f"https://image.pollinations.ai/prompt/{clean_prompt}?width={width}&height={height}&nologo=true&seed={30000 + idx * 179}",
        f"https://picsum.photos/{width}/{height}?random={idx + 100}"
    ]

    for url in urls:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    if len(content) > 5000:
                        with open(filepath, "wb") as f:
                            f.write(content)
                        return filepath
        except Exception:
            continue
    return None

async def generate_all_images(prompts, job_id, width, height):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for idx in range(len(prompts)):
            p = prompts[idx] if idx < len(prompts) else f"Scene {idx+1}"
            img_path = os.path.join(TEMP_DIR, f"{job_id}_raw_{idx:02d}.jpg")
            tasks.append(fetch_image(session, p, img_path, idx, width, height))
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

# 4. ฟังก์ชันวาดตัวหนังสือภาษาไทยด้วย Pillow (รองรับฟอนต์ไทย 100% สระไม่ลอย ไม่เป็นสี่เหลี่ยม)
def burn_text_on_image(img_path, watermark_text, subtitle_text, output_path, width, height):
    img = Image.open(img_path).convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)

    wm_size = 40 if height > width else 32
    sub_size = 34 if height > width else 28

    try:
        font_wm = ImageFont.truetype(FONT_PATH, wm_size)
        font_sub = ImageFont.truetype(FONT_PATH, sub_size)
    except Exception:
        font_wm = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    # 4.1 วาดลายน้ำด้านบน
    wm_bbox = draw.textbbox((0, 0), watermark_text, font=font_wm)
    wm_w = wm_bbox[2] - wm_bbox[0]
    wm_h = wm_bbox[3] - wm_bbox[1]
    wm_x = (width - wm_w) // 2
    wm_y = 120 if height > width else 50

    pad = 12
    draw.rounded_rectangle([wm_x - pad, wm_y - pad, wm_x + wm_w + pad, wm_y + wm_h + pad], radius=10, fill=(0, 0, 0, 140))
    draw.text((wm_x, wm_y), watermark_text, font=font_wm, fill=(255, 220, 50, 255))

    # 4.2 วาดคำบรรยายด้านล่าง (ตัดบรรทัดให้อ่านง่าย สบายตา)
    if subtitle_text:
        wrapped_lines = textwrap.wrap(subtitle_text, width=28)
        full_sub_text = "\n".join(wrapped_lines[:2])
        
        sub_bbox = draw.multiline_textbbox((0, 0), full_sub_text, font=font_sub, spacing=10)
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_h = sub_bbox[3] - sub_bbox[1]
        sub_x = (width - sub_w) // 2
        sub_y = height - sub_h - (220 if height > width else 80)

        draw.rounded_rectangle([sub_x - 16, sub_y - 12, sub_x + sub_w + 16, sub_y + sub_h + 12], radius=12, fill=(0, 0, 0, 160))
        draw.multiline_text((sub_x, sub_y), full_sub_text, font=font_sub, fill=(255, 255, 255, 255), align="center", spacing=10)

    img.convert("RGB").save(output_path, "JPEG", quality=95)

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "healthy", "service": "Unified Dharma Video Engine"}), 200

@app.route("/render", methods=["POST"])
def render_video():
    ensure_font()
    data = request.get_json(force=True)
    prompts = data.get("prompts", [])
    mode = data.get("mode", "short")
    watermark_text = data.get("watermark", "- บารมี พระใหม่ -")
    story_script = data.get("story_script")
    voice = data.get("voice", "th-TH-PremwadeeNeural")

    if not prompts or not story_script:
        return jsonify({"error": "prompts and story_script are required"}), 400

    if mode == "short":
        width, height = 720, 1280
    else:
        width, height = 1280, 720

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

        # 2. ดาวน์โหลดรูปภาพ
        raw_images = loop.run_until_complete(generate_all_images(prompts, job_id, width, height))
        loop.close()

        if len(raw_images) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(voice_duration / len(raw_images), 1.5)

        # 3. ตัดแบ่งประโยคคำบรรยายให้แต่ละฉาก
        sentences = [s.strip() for s in story_script.replace("...", ",").replace(";", ",").split(",") if s.strip()]
        if not sentences:
            sentences = [story_script]

        # 4. วาดตัวหนังสือภาษาไทยลงในทุกรูปภาพ
        ready_images = []
        for i, raw_img in enumerate(raw_images):
            out_img = os.path.join(TEMP_DIR, f"{job_id}_ready_{i:02d}.jpg")
            scene_sub = sentences[i % len(sentences)]
            burn_text_on_image(raw_img, watermark_text, scene_sub, out_img, width, height)
            ready_images.append(out_img)

        # 5. ประกอบคลิปด้วย Concat
        concat_file = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
        with open(concat_file, "w") as f:
            for img in ready_images:
                f.write(f"file '{img}'\n")
                f.write(f"duration {duration_per_image:.2f}\n")
            f.write(f"file '{ready_images[-1]}'\n")

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", voice_audio_path,
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast",
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
            "images_rendered": len(ready_images)
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
