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
from PIL import Image, ImageEnhance, ImageOps
from groq import Groq, APIError, RateLimitError, BadRequestError

# --- CONFIGURATION ---
GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

if not GROQ_API_KEYS:
    print("❌ FATAL ERROR: Groq API Key is missing!")
    exit(1)

# --- CATEGORY RSS FEED ---
CATEGORY_URLS = {
    "Transfer News": "https://news.google.com/rss/search?q=football+transfer+news+Fabrizio+Romano+here+we+go+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Premier League": "https://news.google.com/rss/search?q=Premier+League+news+match+result+analysis+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Champions League": "https://news.google.com/rss/search?q=UEFA+Champions+League+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "La Liga": "https://news.google.com/rss/search?q=La+Liga+Real+Madrid+Barcelona+news+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "International": "https://news.google.com/rss/search?q=International+Football+news+FIFA+World+Cup+when:1d&hl=en-GB&gl=GB&ceid=GB:en",
    "Tactical Analysis": "https://news.google.com/rss/search?q=football+tactical+analysis+prediction+preview+when:1d&hl=en-GB&gl=GB&ceid=GB:en"
}

# --- AUTHORITY SOURCES ---
AUTHORITY_SOURCES = [
    "Transfermarkt", "Sky Sports", "The Athletic", "Opta Analyst",
    "WhoScored", "BBC Sport", "The Guardian", "UEFA Official", "ESPN FC"
]

# --- FALLBACK IMAGES (JIKA BING BLOKIR/GAGAL) ---
FALLBACK_IMAGES = [
    "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=1200&q=80", # Stadium
    "https://images.unsplash.com/photo-1431324155629-1a6deb1dec8d?auto=format&fit=crop&w=1200&q=80", # Ball grass
    "https://images.unsplash.com/photo-1556056504-5c7696c4c28d?auto=format&fit=crop&w=1200&q=80", # Fans
    "https://images.unsplash.com/photo-1579952363873-27f3bade9f55?auto=format&fit=crop&w=1200&q=80", # Ball generic
    "https://images.unsplash.com/photo-1518605348487-73d9d3dc2345?auto=format&fit=crop&w=1200&q=80"  # Silhouette
]

CONTENT_DIR = "content/articles"
IMAGE_DIR = "static/images"
DATA_DIR = "automation/data"
MEMORY_FILE = f"{DATA_DIR}/link_memory.json"
AUTHOR_NAME = "Dave Harsya (Senior Analyst)"

TARGET_PER_CATEGORY = 1 

# --- MEMORY SYSTEM ---
def load_link_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_link_to_memory(title, slug):
    os.makedirs(DATA_DIR, exist_ok=True)
    memory = load_link_memory()
    memory[title] = f"/articles/{slug}"
    if len(memory) > 50:
        memory = dict(list(memory.items())[-50:])
    with open(MEMORY_FILE, 'w') as f: json.dump(memory, f, indent=2)

def get_formatted_internal_links():
    memory = load_link_memory()
    items = list(memory.items())
    if not items: return ""
    if len(items) > 3: items = random.sample(items, 3)
    formatted_links = []
    for title, url in items:
        formatted_links.append(f"* [{title}]({url})")
    return "\n".join(formatted_links)

# --- RSS FETCHER ---
def fetch_rss_feed(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.google.com/'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200: return None
        return feedparser.parse(response.content)
    except: return None

# --- CLEANING ---
def clean_text(text):
    if not text: return ""
    cleaned = text.replace("**", "").replace("__", "").replace("##", "")
    cleaned = cleaned.replace('"', "'") 
    cleaned = cleaned.strip()
    return cleaned

# --- IMAGE ENGINE (ANTI-DUPLICATE & FALLBACK) ---
def download_and_optimize_image(query, filename):
    # 1. Randomize Query agar hasil tidak selalu sama
    suffixes = ["stadium atmosphere", "match action", "fans cheering", "soccer field", "night match"]
    clean_query = f"{query} {random.choice(suffixes)}".replace(" ", "+")
    
    image_url = f"https://tse2.mm.bing.net/th?q={clean_query}&w=1280&h=720&c=7&rs=1&p=0"
    print(f"      🔍 Fetching Image: {clean_query}...")
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
        response = requests.get(image_url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            if "image" not in response.headers.get("content-type", ""): 
                print("      ⚠️ Not an image. Using fallback.")
                return random.choice(FALLBACK_IMAGES)

            img = Image.open(BytesIO(response.content))
            img = img.convert("RGB")
            
            width, height = img.size
            img = img.crop((width*0.1, height*0.1, width*0.9, height*0.9)) 
            img = img.resize((1200, 675), Image.Resampling.LANCZOS)
            
            img = ImageOps.mirror(img) 
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.4)
            enhancer_col = ImageEnhance.Color(img)
            img = enhancer_col.enhance(1.1)
            
            output_path = f"{IMAGE_DIR}/{filename}"
            img.save(output_path, "JPEG", quality=92, optimize=True)
            
            return f"/images/{filename}" # Berhasil download lokal
            
    except Exception as e:
        print(f"      ⚠️ Image Error: {e}")
    
    # 2. Jika Gagal, Return Random Fallback URL
    print("      ⚠️ Using Random Fallback Image.")
    return random.choice(FALLBACK_IMAGES)

# --- AI WRITER ENGINE (BULLETPROOF PARSER) ---
def parse_ai_response(text, fallback_title, fallback_desc):
    try:
        parts = text.split("|||BODY_START|||")
        if len(parts) >= 2:
            json_part = parts[0].strip()
            body_part = parts[1].strip()
            json_part = re.sub(r'```json\s*', '', json_part)
            json_part = re.sub(r'```', '', json_part)
            data = json.loads(json_part)
            data['title'] = clean_text(data.get('title', fallback_title))
            data['description'] = clean_text(data.get('description', fallback_desc))
            data['image_alt'] = clean_text(data.get('image_alt', data['title']))
            data['content'] = body_part
            return data
    except Exception: pass
    
    # Fallback jika JSON rusak
    clean_body = re.sub(r'\{.*\}', '', text, flags=re.DOTALL).replace("|||BODY_START|||", "").strip()
    return {
        "title": clean_text(fallback_title),
        "description": clean_text(fallback_desc),
        "image_alt": clean_text(fallback_title),
        "category": "General",
        "main_keyword": "Football",
        "lsi_keywords": [],
        "content": clean_body
    }

def get_groq_article_seo(title, summary, link, internal_links_block, target_category):
    # DAFTAR MODEL (PRIORITAS + CADANGAN)
    AVAILABLE_MODELS = ["llama-3.3-70b-versatile"]
    selected_sources = ", ".join(random.sample(AUTHORITY_SOURCES, 3))
    
    system_prompt = f"""
    You are Dave Harsya, a Senior Football Analyst for 'Soccer Daily'.
    TARGET CATEGORY: {target_category}
    
    GOAL: Write a 1200+ word article with UNIQUE HEADERS & DIVERSE SOURCES.
    
    OUTPUT FORMAT (JSON):
    {{
        "title": "Headline (NO MARKDOWN)",
        "description": "Meta description",
        "category": "{target_category}",
        "main_keyword": "Entity Name",
        "lsi_keywords": ["keyword1"],
        "image_alt": "Descriptive text for image"
    }}
    |||BODY_START|||
    [Markdown Content]

    # RULES:
    - NO GENERIC HEADERS (e.g. "Introduction"). Use creative sub-headlines.
    - NO EMOJIS.
    
    # INTERNAL LINKING:
    BLOCK START:
    ### Read More
    {internal_links_block}
    BLOCK END.

    # STRUCTURE:
    1. Executive Summary (Blockquote).
    2. Deep Dive Analysis (Unique H2).
    3. Mandatory Data Table (Unique H2).
    4. **Read More** (Paste Block Above).
    5. Quotes & Reaction (Unique H2).
    6. External Authority Link (Source: {selected_sources}).
    7. FAQ.
    """

    user_prompt = f"""
    News Topic: {title}
    Summary: {summary}
    Link: {link}
    
    Write the 1200-word masterpiece now.
    """

    for api_key in GROQ_API_KEYS:
        client = Groq(api_key=api_key)
        for model in AVAILABLE_MODELS:
            try:
                print(f"      🤖 AI Writing ({target_category}) using {model}...")
                completion = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.75, 
                    max_tokens=7500,
                )
                return completion.choices[0].message.content
            except RateLimitError:
                print(f"      ⚠️ Limit hit on {model}, switching...")
                continue
            except Exception as e:
                print(f"      ⚠️ Error: {e}")
                continue
            
    return None

# --- MAIN LOOP ---
def main():
    os.makedirs(CONTENT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    total_generated = 0

    for category_name, rss_url in CATEGORY_URLS.items():
        print(f"\n📡 Fetching: {category_name}...")
        feed = fetch_rss_feed(rss_url)
        if not feed or not feed.entries: continue

        cat_success_count = 0
        for entry in feed.entries:
            if cat_success_count >= TARGET_PER_CATEGORY: break

            clean_title = entry.title.split(" - ")[0]
            slug = slugify(clean_title, max_length=60, word_boundary=True)
            filename = f"{slug}.md"

            if os.path.exists(f"{CONTENT_DIR}/{filename}"): continue

            print(f"   🔥 Processing: {clean_title[:40]}...")
            
            links_block = get_formatted_internal_links()
            raw_response = get_groq_article_seo(clean_title, entry.summary, entry.link, links_block, category_name)
            
            if not raw_response: continue

            data = parse_ai_response(raw_response, clean_title, entry.summary)
            if not data: continue

            # IMAGE PROCESSING (WITH FALLBACK)
            img_name = f"{slug}.jpg"
            keyword_for_image = data.get('main_keyword') or clean_title
            
            # Variable ini berisi Path Lokal ATAU URL Remote (jika fallback)
            final_img = download_and_optimize_image(keyword_for_image, img_name)
            
            date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
            tags_list = data.get('lsi_keywords', [])
            if data.get('main_keyword'): tags_list.append(data['main_keyword'])
            tags_str = json.dumps(tags_list)
            img_alt = data.get('image_alt', clean_title).replace('"', "'")
            
            md = f"""---
title: "{data['title']}"
date: {date}
author: "{AUTHOR_NAME}"
categories: ["{data['category']}"]
tags: {tags_str}
featured_image: "{final_img}"
featured_image_alt: "{img_alt}"
description: "{data['description']}"
slug: "{slug}"
draft: false
---

{data['content']}

---
*Source: Analysis by {AUTHOR_NAME} based on international reports and [Original Story]({entry.link}).*
"""
            with open(f"{CONTENT_DIR}/{filename}", "w", encoding="utf-8") as f: f.write(md)
            
            if 'title' in data: save_link_to_memory(data['title'], slug)
            
            print(f"   ✅ Published: {filename}")
            cat_success_count += 1
            total_generated += 1
            time.sleep(5)

    print(f"\n🎉 DONE! Total: {total_generated}")

if __name__ == "__main__":
    main()
