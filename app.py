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
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate="-10%", pitch="-1Hz")
    await communicate.save(output_path)
    return output_path

# ฟังก์ชันเจนภาพ FLUX พุทธศิลป์ สวย คมชัด อลังการ
async def fetch_image(session, user_prompt, filepath, hf_token, idx, width, height):
    aspect_ratio = "9:16" if height > width else "16:9"
    
    # ธีมภาพพุทธศิลป์ 5 สไตล์ที่สร้างขึ้นมาอย่างประณีต
    dharma_masterpieces = [
        "Magnificent glowing golden Buddha statue meditating peacefully under ancient Bodhi tree, divine morning sun rays breaking through golden mist, lush jungle, ultra detailed, photorealistic 8k, cinematic lighting, masterpiece",
        "Serene wise Thai Buddhist monk in saffron robe walking mindfully along a mystical bamboo forest path, warm golden hour sunlight, tranquil atmospheric depth, 8k",
        "Ancient majestic Buddhist pagoda temple perched on high mountain peak above sea of white clouds, spectacular sunrise sky, spiritual aura, award winning photography, 8k",
        "Close up shot of sacred glowing pink lotus blooming on crystal clear pond, golden water ripple reflections, tranquil zen meditation vibe, highly detailed 8k",
        "Sacred ancient wooden Buddhist monastery hall, glowing candlelight illuminating tranquil Buddha face, spiritual atmosphere, masterpiece 8k"
    ]
    
    base_theme = dharma_masterpieces[idx % len(dharma_masterpieces)]
    clean_user = user_prompt.replace('"', '').replace("'", "") if user_prompt else ""
    full_prompt = f"{base_theme}, {clean_user}, masterpiece, highly detailed, photorealistic, 8k, cinematic composition --no modern, cars, text, ugly, blurry, watermark"

    # ลำดับ 1: ดึงผ่าน Hugging Face FLUX.1
    if hf_token:
        hf_urls = [
            "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
            "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell",
            "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"
        ]
        headers = {"Authorization": f"Bearer {hf_token}", "x-wait-for-model": "true"}
        payload = {"inputs": f"{full_prompt}, {aspect_ratio}"}

        for url in hf_urls:
            try:
                async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        if len(content) > 10000:
                            with open(filepath, "wb") as f:
                                f.write(content)
                            return filepath
            except Exception:
                pass

    # ลำดับ 2: Pollinations FLUX Engine (คมชัด สีสดสมจริง)
    try:
        encoded = urllib.parse.quote(full_prompt)
        polli_url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&model=flux&nologo=true&seed={50000 + idx * 313}"
        async with session.get(polli_url, timeout=aiohttp.ClientTimeout(total=35)) as resp:
            if resp.status == 200:
                content = await resp.read()
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
        for idx in range(len(prompts)):
            p = prompts[idx] if idx < len(prompts) else f"Scene {idx+1}"
            img_path = os.path.join(TEMP_DIR, f"{job_id}_raw_{idx:02d}.jpg")
            tasks.append(fetch_image(session, p, img_path, hf_token, idx, width, height))
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

# วาดตัวหนังสือภาษาไทย คมชัด สระไม่ลอย
def burn_text_on_image(img_path, watermark_text, subtitle_text, output_path, width, height):
    img = Image.open(img_path).convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)

    wm_size = 40 if height > width else 32
    sub_size = 32 if height > width else 26

    try:
        font_wm = ImageFont.truetype(FONT_PATH, wm_size)
        font_sub = ImageFont.truetype(FONT_PATH, sub_size)
    except Exception:
        font_wm = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    # ลายน้ำบน
    wm_bbox = draw.textbbox((0, 0), watermark_text, font=font_wm)
    wm_w = wm_bbox[2] - wm_bbox[0]
    wm_h = wm_bbox[3] - wm_bbox[1]
    wm_x = (width - wm_w) // 2
    wm_y = 120 if height > width else 50

    pad = 12
    draw.rounded_rectangle([wm_x - pad, wm_y - pad, wm_x + wm_w + pad, wm_y + wm_h + pad], radius=10, fill=(0, 0, 0, 150))
    draw.text((wm_x, wm_y), watermark_text, font=font_wm, fill=(255, 215, 0, 255))

    # ซับไตเติลล่าง
    if subtitle_text:
        wrapped_lines = textwrap.wrap(subtitle_text, width=26)
        full_sub_text = "\n".join(wrapped_lines[:2])
        
        sub_bbox = draw.multiline_textbbox((0, 0), full_sub_text, font=font_sub, spacing=8)
        sub_w = sub_bbox[2] - sub_bbox[0]
        sub_h = sub_bbox[3] - sub_bbox[1]
        sub_x = (width - sub_w) // 2
        sub_y = height - sub_h - (240 if height > width else 80)

        draw.rounded_rectangle([sub_x - 16, sub_y - 10, sub_x + sub_w + 16, sub_y + sub_h + 10], radius=12, fill=(0, 0, 0, 170))
        draw.multiline_text((sub_x, sub_y), full_sub_text, font=font_sub, fill=(255, 255, 255, 255), align="center", spacing=8)

    img.convert("RGB").save(output_path, "JPEG", quality=95)

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

        # 2. สร้างภาพธรรมะคุณภาพสูงระดับ 8K
        raw_images = loop.run_until_complete(generate_all_images(prompts, job_id, hf_token, width, height))
        loop.close()

        if len(raw_images) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(voice_duration / len(raw_images), 2.0)

        # 3. ตัดแบ่งประโยคคำบรรยาย
        sentences = [s.strip() for s in story_script.replace("...", ",").replace(";", ",").split(",") if s.strip()]
        if not sentences:
            sentences = [story_script]

        # 4. วาดตัวหนังสือลงบนภาพ
        ready_images = []
        for i, raw_img in enumerate(raw_images):
            out_img = os.path.join(TEMP_DIR, f"{job_id}_ready_{i:02d}.jpg")
            scene_sub = sentences[i % len(sentences)]
            burn_text_on_image(raw_img, watermark_text, scene_sub, out_img, width, height)
            ready_images.append(out_img)

        # 5. ประกอบคลิปพร้อมเอฟเฟกต์ซูมภาพอย่างนุ่มนวล (Ken Burns Zoom)
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
            "-vf", "zoompan=z='min(zoom+0.0015,1.15)':d=125:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=25",
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
