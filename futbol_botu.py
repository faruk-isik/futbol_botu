import tweepy
import time
import os
import threading
import logging
from groq import Groq
import feedparser
from datetime import datetime
import pytz
from flask import Flask, jsonify, request
from difflib import SequenceMatcher
import hashlib
import requests  # Ekledik, resim indirme için

# --- TÜRKIYE SAAT DİLİMİ ---
TR_TZ = pytz.timezone('Europe/Istanbul')

def get_tr_time():
    """Türkiye saatini döndür"""
    return datetime.now(TR_TZ)

def get_tr_time_str():
    """Türkiye saatini string olarak döndür"""
    return get_tr_time().strftime("%Y-%m-%d %H:%M:%S")

# --- LOGLAMA AYARLARI ---
class TurkeyTimeFormatter(logging.Formatter):
    """Türkiye saati ile log formatter"""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, TR_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

formatter = TurkeyTimeFormatter('%(asctime)s - %(levelname)s - %(message)s')

file_handler = logging.FileHandler('bot.log')
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

# --- VERSİYON ---
VERSION = "13.1 - Gelişmiş Tekrar Kontrolü"
logger.info(f"VERSION: {VERSION}")

# --- AYARLAR ---
X_API_KEY = os.getenv("X_API_KEY")
X_API_SECRET = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "default_secret_change_this")
CRON_SECRET = os.getenv("CRON_SECRET", SECRET_TOKEN)  # Cron için ayrı token

# MYNET Son Dakika RSS
MYNET_SON_DAKIKA_RSS = "https://www.mynet.com/haber/rss/sondakika"

SIMILARITY_THRESHOLD = 0.75
MAX_RETRIES = 3

# --- GLOBAL DEĞİŞKENLER ---
last_news_summary = ""
last_tweet_time = "Henüz tweet atılmadı"
tweeted_news_hashes = set()
recent_news_titles = []
tweet_log = []
is_busy = False
total_requests = 0
last_cron_trigger = "Henüz tetiklenmedi"

# --- WEB SUNUCUSU ---
app = Flask(__name__)

@app.route('/')
def home():
    status_emoji = '🔴 Meşgul' if is_busy else '🟢 Hazır'
    trigger_url = f"/trigger?token={SECRET_TOKEN}"
    # (HTML içeriği burada aynen kalabilir, uzun olduğu için eklemedim)
    return "HTML içeriği burada..."

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "version": VERSION,
        "uptime": "running",
        "total_requests": total_requests
    })

@app.route('/ping')
def ping():
    global total_requests
    total_requests += 1
    return jsonify({"status": "pong", "timestamp": get_tr_time_str()})

@app.route('/status')
def status():
    return jsonify({
        "version": VERSION,
        "last_tweet_time": last_tweet_time,
        "last_tweet_content": last_news_summary[:100] + "..." if last_news_summary else "Yok",
        "is_busy": is_busy,
        "processed_news_count": len(tweeted_news_hashes),
        "recent_titles_count": len(recent_news_titles),
        "tweet_log": tweet_log,
        "last_cron_trigger": last_cron_trigger,
        "total_requests": total_requests
    })

@app.route('/cron', methods=['GET', 'POST'])
def cron_trigger():
    global is_busy, last_cron_trigger
    secret = request.args.get('secret') or request.headers.get('X-Cron-Secret')
    if secret != CRON_SECRET:
        logger.warning(f"❌ Yetkisiz cron denemesi! IP: {request.remote_addr}")
        return jsonify({"success": False, "error": "Invalid secret"}), 401
    if is_busy:
        logger.info("⏭️ Bot meşgul, cron atlandı")
        return jsonify({"success": False, "message": "Bot busy, skipped"}), 200
    last_cron_trigger = get_tr_time_str()
    thread = threading.Thread(target=job, kwargs={"source": "CRON"})
    thread.start()
    return jsonify({"success": True, "message": "Tweet job started", "timestamp": last_cron_trigger}), 202

@app.route('/trigger', methods=['POST', 'GET'])
def trigger_tweet():
    global is_busy
    token = request.args.get('token') if request.method == 'GET' else request.headers.get('X-Secret-Token') or (request.json and request.json.get('secret_token'))
    if SECRET_TOKEN != "default_secret_change_this" and token != SECRET_TOKEN:
        return "<html><body>❌ Yetkisiz erişim</body></html>", 401
    if is_busy:
        return "<html><body>⏳ Bot meşgul</body></html>", 429
    thread = threading.Thread(target=job, kwargs={"source": "MANUEL"})
    thread.start()
    return "<html><body>✅ İşlem başlatıldı</body></html>", 200

# --- GROQ CLIENT ---
client_ai = Groq(api_key=GROQ_API_KEY)

# --- TWITTER BAĞLANTISI ---
def get_twitter_conn():
    try:
        return tweepy.Client(
            consumer_key=X_API_KEY,
            consumer_secret=X_API_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_SECRET
        )
    except Exception as e:
        logger.error(f"Twitter bağlantı hatası: {e}")
        return None

# --- HABER HASH OLUŞTUR ---
def create_news_hash(title, description):
    content = f"{title}|{description}".lower()
    return hashlib.md5(content.encode()).hexdigest()

# --- BENZERLİK KONTROLÜ ---
def is_similar_to_recent(title, threshold=SIMILARITY_THRESHOLD):
    for recent_title in recent_news_titles:
        ratio = SequenceMatcher(None, title.lower(), recent_title.lower()).ratio()
        if ratio > threshold:
            return True
    return False

def is_duplicate_tweet(new_tweet_text, threshold=0.80):
    if not tweet_log:
        return False
    for log_entry in tweet_log:
        old_tweet = log_entry['tweet']
        ratio = SequenceMatcher(None, new_tweet_text.lower(), old_tweet.lower()).ratio()
        if ratio > threshold:
            return True
    return False

# --- HTML TEMİZLEME ---
def clean_html_content(html_text):
    import re
    text = re.sub(r'<[^>]+>', '', html_text)
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&quot;', '"')
    text = text.replace('&amp;', '&')
    text = text.replace('&#39;', "'")
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# --- RSS'den Haberleri Çek ---
def get_image_url_from_entry(entry):
    import re
    if hasattr(entry, 'media_content') and entry.media_content:
        return entry.media_content[0]['url']
    elif hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0]['url']
    else:
        content_str = ''
        if hasattr(entry, 'description'):
            content_str += entry.description
        if hasattr(entry, 'summary'):
            content_str += entry.summary
        match = re.search(r'<img[^>]+src="([^">]+)"', content_str)
        if match:
            return match.group(1)
    return None

def fetch_ntv_breaking_news():
    logger.info("📺 MYNET Son Dakika haberleri çekiliyor...")
    try:
        feed = feedparser.parse(MYNET_SON_DAKIKA_RSS)
        if not feed.entries:
            logger.error("MYNET RSS'den haber alınamadı!")
            return []
        news_list = []
        for entry in feed.entries[:15]:
            title = entry.get('title', '').strip()
            content = ""
            if hasattr(entry, 'content') and entry.content:
                content = entry.content[0].get('value', '')
            if not content:
                content = entry.get('summary', entry.get('description', ''))
            full_content = clean_html_content(content)
            link = entry.get('link', '')
            pub_date = entry.get('published', '')
            if not title or len(title) < 15:
                continue
            news_hash = create_news_hash(title, full_content[:200])
            news_list.append({
                'title': title,
                'full_content': full_content,
                'link': link,
                'pub_date': pub_date,
                'hash': news_hash,
                'entry': entry  # ekleniyor
            })
        logger.info(f"✅ {len(news_list)} adet MYNET haberi bulundu")
        return news_list
    except Exception as e:
        logger.error(f"MYNET RSS hatası: {e}")
        return []

# --- HABER SEÇİMİ ---
def select_untweeted_news(news_list):
    suitable_news = []
    for news in news_list:
        if news['hash'] in tweeted_news_hashes:
            continue
        if is_similar_to_recent(news['title']):
            continue
        suitable_news.append(news)
    if not suitable_news:
        return None
    return suitable_news[0]

# --- GRAFİK VE TWEET OLUŞTUR ---
def create_tweet_with_groq(news):
    try:
        content_to_use = news.get('full_content', '')
        if not content_to_use or len(content_to_use) < 50:
            content_to_use = news['title']
        if len(content_to_use) > 2000:
            content_to_use = content_to_use[:2000] + "..."
        prompt = f"""
Haber Başlığı: {news['title']}
Haber İçeriği:
{content_to_use}
Yukarıdaki haberi TAM 280 karakter kullanarak özetle.
KURALLAR:
1. TAM 280 karaktere yakın kullan (270-280 arası ideal)
2. Haberin ÖNEMLİ detaylarını içer
3. Sayılar, isimler, yerler gibi somut bilgileri ekle
4. Gereksiz kelime kullanma
5. Hashtag KULLANMA
6. Sadece haber özeti yaz, başka hiçbir şey yazma
"""
        completion = client_ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": """Sen profesyonel bir haber editörüsün. 
Haberleri 280 karakterlik tweet formatında özetliyorsun.
Her karakteri verimli kullan, gereksiz kelime ekleme.
Somut bilgileri (sayı, isim, yer) mutlaka ekle."""},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=400
        )
        tweet_text = completion.choices[0].message.content.strip()
        tweet_text = tweet_text.strip('"').strip("'")
        if len(tweet_text) > 280:
            logger.warning(f"Tweet çok uzun ({len(tweet_text)} kar), kısaltılıyor...")
            tweet_text = tweet_text[:277].rsplit('.', 1)[0] + '...'
            if len(tweet_text) > 280:
                tweet_text = tweet_text[:277] + '...'
        return tweet_text
    except Exception as e:
        logger.error(f"Groq hatası: {e}")
        return None

# --- ANA GÖREV ---
def job(source="MANUEL"):
    global last_news_summary, last_tweet_time, is_busy, tweeted_news_hashes, recent_news_titles, tweet_log
    if is_busy:
        logger.warning("Bot meşgul, görev atlandı")
        return
    is_busy = True
    max_attempts = 5
    try:
        logger.info("="*60)
        logger.info(f"{source} GÖREV BAŞLATILDI: {get_tr_time_str()}")
        news_list = fetch_ntv_breaking_news()
        if not news_list:
            logger.error("❌ Haber alınamadı, görev iptal")
            return
        for attempt in range(max_attempts):
            logger.info(f"--- Deneme {attempt+1}/{max_attempts} ---")
            selected_news = select_untweeted_news(news_list)
            if not selected_news:
                logger.error("❌ Uygun haber bulunamadı")
                return
            # Resmi al
            image_url = get_image_url_from_entry(selected_news['entry'])
            media_id = None
            if image_url:
                try:
                    response = requests.get(image_url, timeout=10)
                    with open('temp_image.jpg', 'wb') as f:
                        f.write(response.content)
                    client = get_twitter_conn()
                    if client:
                        media = client.create_media_upload('temp_image.jpg')
                        media_id = media.media_id
                except Exception as e:
                    logger.error(f"Resim indirme veya yükleme hatası: {e}")
            # Tweet oluştur
            tweet_text = create_tweet_with_groq(selected_news)
            if not tweet_text:
                logger.error("❌ Tweet oluşturulamadı")
                tweeted_news_hashes.add(selected_news['hash'])
                continue
            # Tekrar kontrol
            if is_duplicate_tweet(tweet_text):
                logger.warning("🔄 Bu tweet daha önce atıldı, başka haber deneniyor...")
                tweeted_news_hashes.add(selected_news['hash'])
                recent_news_titles.append(selected_news['title'])
                if len(recent_news_titles) > 20:
                    recent_news_titles.pop(0)
                continue
            # Tweet gönder
            client = get_twitter_conn()
            if not client:
                logger.error("❌ Twitter bağlantısı kurulamadı")
                return
            if media_id:
                response = client.create_tweet(text=tweet_text, media_ids=[media_id])
            else:
                response = client.create_tweet(text=tweet_text)
            # Kayıtlar
            tweeted_news_hashes.add(selected_news['hash'])
            recent_news_titles.append(selected_news['title'])
            if len(recent_news_titles) > 20:
                recent_news_titles.pop(0)
            tweet_log.append({'time': get_tr_time_str(), 'tweet': tweet_text})
            if len(tweet_log) > 10:
                tweet_log.pop(0)
            last_news_summary = tweet_text
            last_tweet_time = get_tr_time_str()
            logger.info(f"✅ Tweet gönderildi: {tweet_text}")
            return
        logger.error(f"❌ {max_attempts} denemede tweet atılamadı")
    except tweepy.errors.TooManyRequests:
        logger.error("❌ Twitter rate limit aşıldı")
    except Exception as e:
        logger.error(f"Hata: {e}")
    finally:
        is_busy = False

def run_web_server():
    app.run(host='0.0.0.0', port=8000)

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("SİSTEM BAŞLATILIYOR - CRON MODE")
    logger.info("="*60)
    required_keys = [X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET, GROQ_API_KEY]
    if not all(required_keys):
        logger.critical("Eksik API anahtarları!")
        exit(1)
    logger.info("✅ Bot Cron-Job modunda çalışıyor")
    logger.info(f"⏰ Cron endpoint: /cron?secret={CRON_SECRET}")
    logger.info("📍 Web sunucusu başlatılıyor...")
    run_web_server()
