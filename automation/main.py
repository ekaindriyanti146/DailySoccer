import os
import json
import requests
import feedparser
import time
import re
import random
from datetime import datetime
from slugify import slugify
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps, ImageFilter
from groq import Groq, APIError, RateLimitError, BadRequestError

# --- CONFIGURATION ---
GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: Groq API Key is missing!")
    exit(1)

# --- CATEGORY RSS FEED (GLOBAL/ENGLISH) ---
CATEGORY_URLS = {
    "Transfer News": "https://news.google.com/rss/search?q=football+transfer+news+Fabrizio+Romano+here+we+go+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Premier League": "https://news.google.com/rss/search?q=Premier+League+news+match+result+highlights+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Champions League": "https://news.google.com/rss/search?q=UEFA+Champions+League+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "La Liga": "https://news.google.com/rss/search?q=La+Liga+Real+Madrid+Barcelona+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "International Football": "https://news.google.com/rss/search?q=International+Football+news+FIFA+World+Cup+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Match Predictions": "https://news.google.com/rss/search?q=football+match+prediction+preview+predicted+lineup+when:1d&hl=en-GB&gl=GB&ceid=GB:en"
}

CONTENT_DIR = "content/articles"
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"
AUTHOR_NAME = "Soccer Daily Editorial"

TARGET_PER_CATEGORY = 1 

# --- MEMORY SYSTEM ---
def load_link_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_link_to_memory(keyword, slug):
    os.makedirs(DATA_DIR, exist_ok=True)
    memory = load_link_memory()
    clean_key = keyword.lower().strip()
    memory[clean_key] = f"/articles/{slug}"
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def get_internal_links_context():
    memory = load_link_memory()
    items = list(memory.items())
    if len(items) > 30:
        items = random.sample(items, 30)
    return json.dumps(dict(items))

# --- DISCOVER-READY IMAGE ENGINE (DIRECT STREAM) ---
def add_subtle_noise(img, intensity=0.02):
    width, height = img.size
    pixels = img.load()
    for x in range(width):
        for y in range(height):
            if random.random() < 0.1:
                r, g, b = pixels[x, y]
                noise = random.randint(int(-255 * intensity), int(255 * intensity))
                r = max(0, min(255, r + noise))
                g = max(0, min(255, g + noise))
                b = max(0, min(255, b + noise))
                pixels[x, y] = (r, g, b)
    return img

def download_and_optimize_image(query, filename):
    clean_query = query.replace(" ", "+")
    # Request HD Image for Discover (1280x720)
    image_url = f"https://tse2.mm.bing.net/th?q={clean_query}+football+match+photo&w=1280&h=720&c=7&rs=1&p=0"
    
    print(f"      🔍 Fetching Discover Image: {query}...")
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
        response = requests.get(image_url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            if "image" not in response.headers.get("content-type", ""):
                return False

            img = Image.open(BytesIO(response.content))
            img = img.convert("RGB")
            
            # 1. Smart Crop & Resize for Discover (1200x675 is the Golden Ratio 16:9)
            width, height = img.size
            img = img.crop((width*0.1, height*0.1, width*0.9, height*0.9)) 
            img = img.resize((1200, 675), Image.Resampling.LANCZOS)
            
            # 2. Mirror & Hash Busting (SEO Unique Image)
            img = ImageOps.mirror(img) 
            img = add_subtle_noise(img, intensity=0.05)
            
            # 3. Enhance
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.3)
            enhancer_col = ImageEnhance.Color(img)
            img = enhancer_col.enhance(1.1)
            
            output_path = f"{IMAGE_DIR}/{filename}"
            img.save(output_path, "JPEG", quality=92, optimize=True)
            return True
    except Exception as e:
        print(f"      ⚠️ Image Fail: {e}")
    
    return False

# --- AI WRITER ENGINE (HIGH SEO STRATEGY) ---
def parse_ai_response(text):
    try:
        parts = text.split("|||BODY_START|||")
        if len(parts) < 2: return None
        json_part = parts[0].strip()
        body_part = parts[1].strip()
        json_part = re.sub(r'```json\s*', '', json_part)
        json_part = re.sub(r'```', '', json_part)
        data = json.loads(json_part)
        data['content'] = body_part
        return data
    except Exception as e:
        print(f"      ❌ Parse Error: {e}")
        return None

def get_groq_article_seo(title, summary, link, internal_links_map, target_category):
    MODEL_NAME = "llama-3.3-70b-versatile"
    
    # --- SUPER SEO PROMPT ---
    system_prompt = f"""
    You are an Expert SEO Football Journalist for 'Soccer Daily'.
    TARGET CATEGORY: {target_category}
    
    TASK: Write a Google-Discover-Ready article (800+ words) in ENGLISH.
    
    OUTPUT FORMAT (JSON REQUIRED):
    {{"title": "High CTR Headline (Max 60 chars)", "description": "Meta Description with keywords (Max 155 chars)", "category": "{target_category}", "main_keyword": "Primary Keyword", "lsi_keywords": ["keyword1", "keyword2"]}}
    |||BODY_START|||
    [Markdown Content]

    SEO RULES (STRICT):
    1. **Keyword Placement**: The 'main_keyword' MUST appear in the First Paragraph and at least one H2 header.
    2. **Structure**: 
       - H1 (Title)
       - **Key Takeaways** (Bullet points)
       - H2: Introduction (Context)
       - H2: Deep Analysis / Tactical View
       - H2: {target_category} Implications
       - H3: Player/Manager Quotes (Simulated/Paraphrased)
       - H2: Conclusion & Prediction
    3. **Internal Linking**: Naturally weave in links from: {internal_links_map}. Format: [Anchor Text](/articles/slug).
    4. **Engagement**: Use short paragraphs (max 3 sentences). Ask a question at the end to trigger comments.
    """

    user_prompt = f"""
    Breaking News: {title}
    Summary: {summary}
    Link: {link}
    
    Write the SEO-optimized article now.
    """

    for index, api_key in enumerate(GROQ_API_KEYS):
        try:
            print(f"      🤖 AI Writing SEO Content ({target_category})...")
            client = Groq(api_key=api_key)
            completion = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=6000,
            )
            return completion.choices[0].message.content
        except Exception as e:
            print(f"      ⚠️ Error (Key #{index+1}): {e}")
            continue
            
    return None

# --- MAIN LOOP ---
def main():
    os.makedirs(CONTENT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    total_generated = 0

    for category_name, rss_url in CATEGORY_URLS.items():
        print(f"\n📡 Fetching Category: {category_name}...")
        try:
            feed = feedparser.parse(rss_url)
        except Exception as e:
            print(f"   ⚠️ RSS Error: {e}")
            continue
        
        if not feed.entries:
            print(f"   ⚠️ Empty.")
            continue

        cat_success_count = 0
        
        for entry in feed.entries:
            if cat_success_count >= TARGET_PER_CATEGORY:
                break

            clean_title = entry.title.split(" - ")[0]
            # SEO Friendly Slug (Short & Clean)
            slug = slugify(clean_title)
            filename = f"{slug}.md"

            if os.path.exists(f"{CONTENT_DIR}/{filename}"):
                continue

            print(f"   🔥 Processing: {clean_title[:40]}...")
            
            # 1. AI Text
            context = get_internal_links_context()
            raw_response = get_groq_article_seo(clean_title, entry.summary, entry.link, context, category_name)
            
            if not raw_response: continue

            data = parse_ai_response(raw_response)
            if not data: continue

            # 2. Image
            img_name = f"{slug}.jpg"
            has_img = download_and_optimize_image(data['main_keyword'], img_name)
            final_img = f"/images/{img_name}" if has_img else "/images/default-football.jpg"
            
            # 3. Save (With Extended Frontmatter for SEO)
            date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
            
            # Menyiapkan Tags/Keywords untuk Frontmatter
            tags_list = data.get('lsi_keywords', [])
            tags_list.append(data['main_keyword'])
            tags_str = str(tags_list).replace("'", '"') # Format JSON array string
            
            md = f"""---
title: "{data['title'].replace('"', "'")}"
date: {date}
author: "{AUTHOR_NAME}"
categories: ["{data['category']}"]
tags: {tags_str}
featured_image: "{final_img}"
description: "{data['description'].replace('"', "'")}"
slug: "{slug}"
draft: false
---

{data['content']}

---
*Source: Analysis by Soccer Daily based on international reports and [Original Story]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f: f.write(md)
            
            if 'main_keyword' in data: 
                save_link_to_memory(data['main_keyword'], slug)
            
            print(f"   ✅ Published: {filename}")
            cat_success_count += 1
            total_generated += 1
            
            print("   zzz... Cooling down 5s...")
            time.sleep(5)

    print(f"\n🎉 DONE! Total generated: {total_generated}")

if __name__ == "__main__":
    main()
