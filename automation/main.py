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

def get_internal_links_context():
    memory = load_link_memory()
    items = list(memory.items())
    if len(items) < 1: return "No previous articles available yet."
    if len(items) > 5: items = random.sample(items, 5)
    return json.dumps(dict(items))

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

# --- CLEANING FUNCTION ---
def clean_text(text):
    if not text: return ""
    cleaned = text.replace("**", "").replace("__", "").replace("##", "")
    cleaned = cleaned.replace('"', "'") 
    cleaned = cleaned.strip()
    return cleaned

# --- IMAGE ENGINE ---
def download_and_optimize_image(query, filename):
    clean_query = query.replace(" ", "+")
    image_url = f"https://tse2.mm.bing.net/th?q={clean_query}+football+match+action+photo&w=1280&h=720&c=7&rs=1&p=0"
    
    print(f"      🔍 Fetching High-Res Image: {query}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
        response = requests.get(image_url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            if "image" not in response.headers.get("content-type", ""): return False

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
            return True
    except: pass
    return False

# --- AI WRITER ENGINE (STRICT UNIQUE HEADERS) ---
def parse_ai_response(text):
    try:
        parts = text.split("|||BODY_START|||")
        if len(parts) < 2: return None
        json_part = parts[0].strip()
        body_part = parts[1].strip()
        json_part = re.sub(r'```json\s*', '', json_part)
        json_part = re.sub(r'```', '', json_part)
        data = json.loads(json_part)
        
        data['title'] = clean_text(data.get('title', ''))
        data['description'] = clean_text(data.get('description', ''))
        data['image_alt'] = clean_text(data.get('image_alt', data['title']))
        data['content'] = body_part
        return data
    except Exception as e:
        print(f"      ❌ Parse Error: {e}")
        return None

def get_groq_article_seo(title, summary, link, internal_links_map, target_category):
    AVAILABLE_MODELS = ["llama-3.3-70b-versatile"]
    
    # --- PROMPT: BANNED WORDS STRATEGY ---
    system_prompt = f"""
    You are Dave Harsya, a Senior Football Analyst for 'Soccer Daily'.
    TARGET CATEGORY: {target_category}
    
    GOAL: Write a 1200+ word article with 100% UNIQUE HEADERS.
    
    OUTPUT FORMAT (JSON):
    {{
        "title": "Headline (NO MARKDOWN)",
        "description": "Meta description",
        "category": "{target_category}",
        "main_keyword": "Entity Name",
        "lsi_keywords": ["keyword1"],
        "image_alt": "Descriptive text"
    }}
    |||BODY_START|||
    [Markdown Content]

    # 🚫 BANNED HEADER WORDS (CRITICAL):
    You are FORBIDDEN from using these words in H2/H3 headers:
    - "Introduction"
    - "Conclusion"
    - "Analysis"
    - "Tactical Analysis"
    - "Key Stats"
    - "Statistics"
    - "Summary"
    - "Background"
    - "Verdict"
    
    # ✅ RULE: EVERY HEADER MUST BE A MINI-HEADLINE:
    Instead of "Tactical Analysis", use "How Slot's Midfield Overloaded The Flanks".
    Instead of "Key Stats", use "The Numbers Behind Salah's Dominance".
    Instead of "Conclusion", use "Why This Result Change the Title Race".

    # MANDATORY CONTENT BLOCKS:
    1. **Executive Summary**: Blockquote (`>`) with 3 bullet points.
    2. **Deep Dive**: 300 words analyzing the event.
    3. **Data Section (Markdown Table)**: MUST include a table. Header must be unique (e.g. "Head-to-Head History").
    4. **Also Read**: List 3 links from: {internal_links_map}.
    5. **Expert Opinion**: Quotes and deep interpretation.
    6. **External Link**: One link to BBC/Transfermarkt.
    7. **FAQ**: 3 Questions.

    # STYLE:
    - NO EMOJIS.
    - Professional, Analytical.
    """

    user_prompt = f"""
    News Topic: {title}
    Summary: {summary}
    Link: {link}
    
    Write now. Remember: BANNED WORDS apply to all headers. Make every H2 unique to the story.
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
                    temperature=0.75, # Kreativitas Tinggi untuk Judul Unik
                    max_tokens=7500,
                )
                return completion.choices[0].message.content
            except RateLimitError: continue
            except Exception: continue
            
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
            
            # 3. Save
            date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
            tags_list = data.get('lsi_keywords', [])
            if data.get('main_keyword'): tags_list.append(data['main_keyword'])
            tags_str = json.dumps(tags_list)
            
            img_alt = data.get('image_alt', data['title']).replace('"', "'")
            
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
