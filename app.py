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

FONT_PATH = os.path.join(FONT_DIR, "Sarabun-Bold.ttf")

def ensure_font():
    if not os.path.exists(FONT_PATH):
        try:
            r = requests.get("https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Bold.ttf", timeout=20)
            if r.status_code == 200:
                with open(FONT_PATH, "wb") as f:
                    f.write(r.content)
        except Exception as e:
            print(f"Font download error: {e}")

ensure_font()

# 1. ปรับเสียงพากย์ให้ช้าลง นุ่มลึก ฟังสบาย
async def generate_voice(text, voice_name, output_path):
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate="-15%", pitch="-2Hz")
    await communicate.save(output_path)
    return output_path

# 2. ฟังก์ชันดาวน์โหลดภาพพระและบรรยากาศธรรมะ คมชัดตรงปก
async def fetch_image(session, prompt, filepath, idx, width, height):
    dharma_prompts = [
        "majestic golden Buddha statue in meditation, serene rainforest, soft morning sunlight, cinematic lighting, photorealistic 8k",
        "Thai Buddhist monk in saffron robe walking peacefully in bamboo forest garden, warm sunset light, highly detailed 8k",
        "ancient golden Buddhist temple on mountain summit above white mist, dramatic sunrise sky, masterpiece 8k",
        "sacred pink lotus flower blooming in tranquil water pond, glowing light reflections, zen atmosphere 8k",
        "peaceful Buddha statue in wooden monastery, glowing oil lamps and candles, warm spiritual atmosphere 8k"
    ]
    selected_prompt = dharma_prompts[idx % len(dharma_prompts)]
    clean_prompt = urllib.parse.quote(f"{selected_prompt}, {prompt}")
    
    # ดึงภาพตรงจาก Unsplash / AI Image Delivery Network ที่โหลดผ่านแน่นอน 100%
    image_sources = [
        f"https://image.pollinations.ai/prompt/{clean_prompt}?width={width}&height={height}&nologo=true&seed={20000 + idx * 111}",
        f"https://picsum.photos/{width}/{height}?random={idx + 1}"
    ]

    for url in image_sources:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status == 200:
                    content = await response.read()
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
            img_path = os.path.join(TEMP_DIR, f"{job_id}_img_{idx:02d}.jpg")
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

# 3. สร้างคำบรรยาย Subtitles (.srt) เปลี่ยนข้อความตามช่วงเวลาของแต่ละฉาก
def create_dynamic_subtitles(story_script, total_duration, num_scenes, srt_path):
    sentences = [s.strip() for s in story_script.replace("...", ",").split(",") if s.strip()]
    if not sentences:
        sentences = [story_script]
    
    count = max(num_scenes, len(sentences))
    duration_per_part = total_duration / count

    def format_time(seconds):
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    with open(srt_path, "w", encoding="utf-8") as f:
        for i in range(count):
            start_t = format_time(i * duration_per_part)
            end_t = format_time((i + 1) * duration_per_part)
            text = sentences[i % len(sentences)]
            f.write(f"{i+1}\n{start_t} --> {end_t}\n{text}\n\n")

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
        font_size_wm = 32
        font_size_sub = 26
        margin_v = 180
    else:
        width, height = 1280, 720
        font_size_wm = 28
        font_size_sub = 22
        margin_v = 80

    job_id = str(uuid.uuid4())[:8]
    voice_audio_path = os.path.join(TEMP_DIR, f"{job_id}_voice.mp3")
    final_video_name = f"video_{job_id}.mp4"
    final_video_path = os.path.join(OUTPUT_DIR, final_video_name)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # สร้างเสียงพากย์ช้าลง นุ่มนวล
        loop.run_until_complete(generate_voice(story_script, voice, voice_audio_path))
        voice_duration = get_audio_duration(voice_audio_path)

        # ดาวน์โหลดภาพพระ/วัด/ธรรมชาติ
        image_files = loop.run_until_complete(generate_all_images(prompts, job_id, width, height))
        loop.close()

        if len(image_files) == 0:
            return jsonify({"error": "Failed to generate images"}), 500

        duration_per_image = max(voice_duration / len(image_files), 1.5)

        # สลับภาพตามจังหวะ
        concat_file = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
        with open(concat_file, "w") as f:
            for img in image_files:
                f.write(f"file '{img}'\n")
                f.write(f"duration {duration_per_image:.2f}\n")
            f.write(f"file '{image_files[-1]}'\n")

        # สร้างไฟล์คำบรรยายแยกตามเวลา
        srt_file = os.path.join(TEMP_DIR, f"{job_id}_sub.srt")
        create_dynamic_subtitles(story_script, voice_duration, len(image_files), srt_file)

        font_arg = f":fontfile='{FONT_PATH}'" if os.path.exists(FONT_PATH) else ""
        escaped_srt = srt_file.replace("\\", "/").replace(":", "\\:")

        # ตัวกรองวิดีโอ: ลายน้ำ + คำบรรยายตามช่วงเวลา
        vf_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"drawtext=text='{watermark_text}'{font_arg}:fontcolor=yellow:fontsize={font_size_wm}:box=1:boxcolor=black@0.45:boxborderw=8:x=(w-text_w)/2:y=90,"
            f"subtitles='{escaped_srt}':force_style='Fontname=Sarabun,FontSize={font_size_sub},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=2,Shadow=0,MarginV={margin_v}',"
            f"format=yuv420p"
        )

        # สร้างเสียงดนตรีบรรเลงสมาธิ/สายน้ำคลออัตโนมัติด้วย Sine Wave & White Noise ในตัว
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", voice_audio_path,
            "-f", "lavfi", "-i", "anoisesrc=c=pink:r=44100:a=0.015",
            "-filter_complex",
            f"[0:v]{vf_filter}[v];[2:a]lowpass=f=400,volume=0.2[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "[v]",
            "-map", "[a]",
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
