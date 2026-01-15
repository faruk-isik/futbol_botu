import tweepy
import schedule
import time
import os
import threading
import logging
from groq import Groq
import feedparser
from datetime import datetime
from flask import Flask, jsonify, request
from difflib import SequenceMatcher
import hashlib

# --- LOGLAMA AYARLARI ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- VERSİYON ---
VERSION = "12.1 - Türkiye Gündemi + Tweet Geçmişi"
logger.info(f"VERSION: {VERSION}")

# --- AYARLAR ---
X_API_KEY = os.getenv("X_API_KEY")
X_API_SECRET = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "default_secret_change_this")

# NTV Son Dakika RSS
NTV_SON_DAKIKA_RSS = "https://www.ntv.com.tr/son-dakika.rss"

SIMILARITY_THRESHOLD = 0.75  # %75 benzerlik
MAX_RETRIES = 3
RETRY_DELAY = 60

# --- GLOBAL DEĞİŞKENLER ---
last_news_summary = ""
last_tweet_time = "Henüz tweet atılmadı"
tweeted_news_hashes = set()  # Hash ile tekrar kontrolü
recent_news_titles = []  # Son 20 haber başlığı
tweet_log = []  # Tweet geçmişi (en son 10 tweet)
is_busy = False

# --- WEB SUNUCUSU ---
app = Flask(__name__)

@app.route('/')
def home():
    status_emoji = '🔴 Meşgul' if is_busy else '🟢 Hazır'
    trigger_url = f"/trigger?token={SECRET_TOKEN}"
    
    # Tweet log'unu HTML'e çevir
    tweet_log_html = ""
    if tweet_log:
        for log_entry in reversed(tweet_log):  # En yeni üstte
            tweet_log_html += f"""
            <div class="tweet-log-item">
                <div class="tweet-time">🕐 {log_entry['time']}</div>
                <div class="tweet-text">{log_entry['tweet']}</div>
            </div>
            """
    else:
        tweet_log_html = '<p style="color: #999; text-align: center; padding: 20px;">Henüz tweet atılmadı</p>'
    
    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Türkiye Gündemi Botu</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }}
            h1 {{
                color: #667eea;
                margin-bottom: 10px;
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 28px;
            }}
            .status-badge {{
                display: inline-block;
                padding: 8px 16px;
                border-radius: 20px;
                font-size: 14px;
                font-weight: bold;
                background: {'#ff4444' if is_busy else '#00C851'};
                color: white;
            }}
            .info-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin: 30px 0;
            }}
            .info-card {{
                background: #f8f9fa;
                padding: 20px;
                border-radius: 10px;
                border-left: 4px solid #667eea;
            }}
            .info-card h3 {{
                color: #667eea;
                font-size: 14px;
                margin-bottom: 8px;
            }}
            .info-card p {{
                color: #333;
                font-size: 16px;
                font-weight: bold;
            }}
            .trigger-button {{
                display: block;
                width: 100%;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                text-align: center;
                text-decoration: none;
                border-radius: 10px;
                font-size: 20px;
                font-weight: bold;
                transition: all 0.3s;
                border: none;
                cursor: pointer;
                margin: 20px 0;
            }}
            .trigger-button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
            }}
            .tweet-log {{
                background: #f8f9fa;
                padding: 25px;
                border-radius: 10px;
                margin-top: 30px;
            }}
            .tweet-log h2 {{
                color: #667eea;
                margin-bottom: 20px;
                font-size: 20px;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .tweet-log-item {{
                background: white;
                padding: 15px;
                border-radius: 8px;
                margin-bottom: 12px;
                border-left: 4px solid #667eea;
                transition: all 0.2s;
            }}
            .tweet-log-item:hover {{
                transform: translateX(5px);
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .tweet-time {{
                color: #999;
                font-size: 13px;
                margin-bottom: 6px;
            }}
            .tweet-text {{
                color: #333;
                font-size: 15px;
                line-height: 1.5;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🇹🇷 Türkiye Gündemi Botu</h1>
            <span class="status-badge">{status_emoji}</span>
            
            <div class="info-grid">
                <div class="info-card">
                    <h3>📌 Versiyon</h3>
                    <p>{VERSION.split(' - ')[0]}</p>
                </div>
                <div class="info-card">
                    <h3>🕐 Son Tweet</h3>
                    <p>{last_tweet_time}</p>
                </div>
                <div class="info-card">
                    <h3>📊 İşlenmiş Haber</h3>
                    <p>{len(tweeted_news_hashes)} adet</p>
                </div>
            </div>

            <a href="{trigger_url}" class="trigger-button">
                🚀 ŞİMDİ TWEET AT
            </a>

            <div class="tweet-log">
                <h2>📜 Tweet Geçmişi</h2>
                {tweet_log_html}
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "version": VERSION,
        "last_tweet": last_tweet_time,
        "is_busy": is_busy,
        "processed_news": len(tweeted_news_hashes)
    })

@app.route('/status')
def status():
    return jsonify({
        "version": VERSION,
        "last_tweet_time": last_tweet_time,
        "last_tweet_content": last_news_summary[:100] + "..." if last_news_summary else "Yok",
        "is_busy": is_busy,
        "processed_news_count": len(tweeted_news_hashes),
        "recent_titles_count": len(recent_news_titles),
        "tweet_log": tweet_log
    })

@app.route('/debug-token')
def debug_token():
    return jsonify({
        "secret_token_set": bool(SECRET_TOKEN and SECRET_TOKEN != "default_secret_change_this"),
        "env_vars_loaded": {
            "X_API_KEY": bool(X_API_KEY),
            "GROQ_API_KEY": bool(GROQ_API_KEY),
            "SECRET_TOKEN": bool(SECRET_TOKEN)
        }
    })

@app.route('/test-content')
def test_content():
    """RSS içeriğini test et - DEBUG için"""
    try:
        feed = feedparser.parse(NTV_SON_DAKIKA_RSS)
        if not feed.entries:
            return "RSS'den veri alınamadı"
        
        first_entry = feed.entries[0]
        
        # Content alanını kontrol et
        content_html = ""
        if hasattr(first_entry, 'content') and first_entry.content:
            content_html = first_entry.content[0].get('value', '')
        
        summary = first_entry.get('summary', '')
        description = first_entry.get('description', '')
        
        # Temizlenmiş içerik
        clean = clean_html_content(content_html if content_html else summary)
        
        return f"""
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: monospace; padding: 20px;">
        <h2>İlk Haber - İçerik Test</h2>
        
        <h3>Başlık:</h3>
        <p>{first_entry.get('title', '')}</p>
        
        <h3>Content (HTML - {len(content_html)} kar):</h3>
        <pre style="background: #f0f0f0; padding: 10px; overflow-x: auto;">{content_html[:500]}</pre>
        
        <h3>Summary ({len(summary)} kar):</h3>
        <pre style="background: #f0f0f0; padding: 10px;">{summary[:300]}</pre>
        
        <h3>Temizlenmiş İçerik ({len(clean)} kar):</h3>
        <p style="background: #e8f5e9; padding: 15px; border-left: 4px solid #4caf50;">{clean[:500]}</p>
        
        <a href="/" style="display: inline-block; margin-top: 20px; padding: 10px 20px; background: #667eea; color: white; text-decoration: none; border-radius: 5px;">← Ana Sayfa</a>
        </body>
        </html>
        """
    except Exception as e:
        return f"Hata: {e}"

@app.route('/trigger', methods=['POST', 'GET'])
def trigger_tweet():
    global is_busy
    
    # Token kontrolü
    if request.method == 'GET':
        token = request.args.get('token')
    else:
        token = request.headers.get('X-Secret-Token') or request.json.get('secret_token') if request.json else None
    
    if SECRET_TOKEN and SECRET_TOKEN != "default_secret_change_this":
        if token != SECRET_TOKEN:
            logger.warning(f"Yetkisiz tetikleme! IP: {request.remote_addr}")
            return """
            <html>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1>❌ Yetkisiz Erişim</h1>
                <p>Geçersiz token!</p>
            </body>
            </html>
            """, 401
    
    if is_busy:
        return """
        <html>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h1>⏳ Bot Meşgul</h1>
            <p>Şu anda bir tweet işlemi devam ediyor...</p>
        </body>
        </html>
        """, 429
    
    thread = threading.Thread(target=job, kwargs={"manual": True})
    thread.start()
    
    logger.info(f"Manuel tetikleme! IP: {request.remote_addr}")
    
    if request.method == 'GET':
        return """
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .container {
                    background: white;
                    padding: 40px;
                    border-radius: 20px;
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    text-align: center;
                }
                .success-icon { font-size: 80px; }
                h1 { color: #667eea; margin: 20px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="success-icon">✅</div>
                <h1>Tweet İşlemi Başlatıldı!</h1>
                <p>NTV Son Dakika haberi işleniyor...</p>
                <p style="color: #999;">~30-60 saniye</p>
                <a href="/status" style="display: inline-block; margin-top: 20px; padding: 12px 30px; background: #667eea; color: white; text-decoration: none; border-radius: 25px;">📊 Durumu Kontrol Et</a>
            </div>
        </body>
        </html>
        """
    
    return jsonify({
        "success": True,
        "message": "Tweet işlemi başlatıldı",
        "timestamp": datetime.now().isoformat()
    }), 202

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
    """Haberin benzersiz hash'ini oluştur"""
    content = f"{title}|{description}".lower()
    return hashlib.md5(content.encode()).hexdigest()

# --- BENZERLİK KONTROLÜ ---
def is_similar_to_recent(title, threshold=SIMILARITY_THRESHOLD):
    """Son tweet'lenen haberlerle benzerlik kontrolü"""
    for recent_title in recent_news_titles:
        ratio = SequenceMatcher(None, title.lower(), recent_title.lower()).ratio()
        if ratio > threshold:
            logger.info(f"Benzer haber bulundu: {ratio:.2f} benzerlik")
            return True
    return False

# --- HTML TEMİZLEME ---
def clean_html_content(html_text):
    """HTML etiketlerini temizle ve düz metin al"""
    import re
    # HTML etiketlerini kaldır
    text = re.sub(r'<[^>]+>', '', html_text)
    # Özel karakterleri düzelt
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&quot;', '"')
    text = text.replace('&amp;', '&')
    text = text.replace('&#39;', "'")
    # Çoklu boşlukları tek boşluğa indir
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# --- NTV SON DAKİKA HABERLER ---
def fetch_ntv_breaking_news():
    """NTV Son Dakika RSS'den haberleri çek"""
    logger.info("📺 NTV Son Dakika haberleri çekiliyor...")
    
    try:
        feed = feedparser.parse(NTV_SON_DAKIKA_RSS)
        
        if not feed.entries:
            logger.error("NTV RSS'den haber alınamadı!")
            return []
        
        news_list = []
        for entry in feed.entries[:15]:  # İlk 15 haber
            title = entry.get('title', '').strip()
            
            # İçerik alanlarını dene (content > summary > description)
            content = ""
            if hasattr(entry, 'content') and entry.content:
                content = entry.content[0].get('value', '')
            if not content:
                content = entry.get('summary', entry.get('description', ''))
            
            # HTML'i temizle
            full_content = clean_html_content(content)
            
            link = entry.get('link', '')
            pub_date = entry.get('published', '')
            
            if not title or len(title) < 15:
                continue
            
            # Hash için başlık + içerik kullan
            news_hash = create_news_hash(title, full_content[:200])
            
            news_list.append({
                'title': title,
                'full_content': full_content,  # TAM İÇERİK
                'link': link,
                'pub_date': pub_date,
                'hash': news_hash
            })
        
        logger.info(f"✅ {len(news_list)} adet NTV haberi bulundu")
        return news_list
        
    except Exception as e:
        logger.error(f"NTV RSS hatası: {e}")
        return []

# --- TWEET İÇİN HABER SEÇ ---
def select_untweeted_news(news_list):
    """Daha önce tweet'lenmemiş haberi seç"""
    
    for news in news_list:
        # Hash kontrolü
        if news['hash'] in tweeted_news_hashes:
            logger.info(f"Atlandı (hash): {news['title'][:50]}...")
            continue
        
        # Benzerlik kontrolü
        if is_similar_to_recent(news['title']):
            logger.info(f"Atlandı (benzer): {news['title'][:50]}...")
            continue
        
        # Bu haber uygun!
        logger.info(f"✅ Seçildi: {news['title'][:50]}...")
        return news
    
    logger.warning("Hiçbir yeni haber bulunamadı, en günceli tekrar işlenecek...")
    return news_list[0] if news_list else None

# --- GROQ İLE TWEET OLUŞTUR ---
def create_tweet_with_groq(news):
    """Groq AI ile haberi tweet formatına dönüştür"""
    
    try:
        # İçerik varsa onu kullan, yoksa başlık
        content_to_use = news.get('full_content', '')
        if not content_to_use or len(content_to_use) < 50:
            content_to_use = news['title']
        
        # Çok uzunsa kısalt (Groq'a gönderirken)
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

ÖRNEK FORMAT:
"Ekonomi Bakanı Mehmet Şimşek, enflasyonla mücadele kapsamında yeni teşvik paketini açıkladı. Pakette KOBİ'lere 5 milyar TL destek, ihracatçılara vergi indirimi ve tarım sektörüne ucuz kredi imkanı yer alıyor. Paket 1 Şubat'ta yürürlüğe girecek."
"""
        
        completion = client_ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": """Sen profesyonel bir haber editörüsün. 
Haberleri 280 karakterlik tweet formatında özetliyorsun.
Her karakteri verimli kullan, gereksiz kelime ekleme.
Somut bilgileri (sayı, isim, yer) mutlaka ekle.
Okuyucu haberin tüm önemli detaylarını anlamalı."""
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.7,
            max_tokens=400
        )
        
        tweet_text = completion.choices[0].message.content.strip()
        
        # Tırnak işaretlerini kaldır (bazen AI tırnak içinde yazar)
        tweet_text = tweet_text.strip('"').strip("'")
        
        # Karakter limiti kontrolü - SERT
        if len(tweet_text) > 280:
            logger.warning(f"Tweet çok uzun ({len(tweet_text)} kar), kısaltılıyor...")
            # Cümle sonunda kes
            tweet_text = tweet_text[:277].rsplit('.', 1)[0] + '...'
            # Eğer hala uzunsa, zorla kes
            if len(tweet_text) > 280:
                tweet_text = tweet_text[:277] + '...'
        
        char_count = len(tweet_text)
        logger.info(f"✅ Tweet oluşturuldu ({char_count} karakter): {tweet_text[:60]}...")
        
        return tweet_text
        
    except Exception as e:
        logger.error(f"Groq hatası: {e}")
        return None

# --- ANA GÖREV FONKSİYONU ---
def job(manual=False):
    global last_news_summary, last_tweet_time, is_busy, tweeted_news_hashes, recent_news_titles, tweet_log
    
    if is_busy:
        logger.warning("Bot meşgul, görev atlandı")
        return
    
    is_busy = True
    trigger_type = "MANUEL" if manual else "OTOMATİK"
    
    try:
        logger.info("=" * 60)
        logger.info(f"{trigger_type} GÖREV BAŞLATILDI: {datetime.now()}")
        
        # 1. NTV haberlerini çek
        news_list = fetch_ntv_breaking_news()
        if not news_list:
            logger.error("Haber alınamadı, görev iptal")
            return
        
        # 2. Tweet'lenmemiş haber seç
        selected_news = select_untweeted_news(news_list)
        if not selected_news:
            logger.error("Uygun haber bulunamadı")
            return
        
        # 3. Groq ile tweet oluştur
        tweet_text = create_tweet_with_groq(selected_news)
        if not tweet_text:
            logger.error("Tweet oluşturulamadı")
            return
        
        # 4. Twitter'a gönder
        client = get_twitter_conn()
        if not client:
            logger.error("Twitter bağlantısı kurulamadı")
            return
        
        response = client.create_tweet(text=tweet_text)
        
        # 5. Başarılı! Kayıtları güncelle
        tweeted_news_hashes.add(selected_news['hash'])
        recent_news_titles.append(selected_news['title'])
        
        # Son 20 başlığı tut
        if len(recent_news_titles) > 20:
            recent_news_titles.pop(0)
        
        # Tweet log'una ekle
        tweet_log.append({
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'tweet': tweet_text
        })
        
        # Son 10 tweet'i tut
        if len(tweet_log) > 10:
            tweet_log.pop(0)
        
        last_news_summary = tweet_text
        last_tweet_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        logger.info("=" * 60)
        logger.info(f"✅ {trigger_type} TWEET GÖNDERİLDİ!")
        logger.info(f"📰 Haber: {selected_news['title'][:60]}...")
        logger.info(f"📝 İçerik uzunluğu: {len(selected_news.get('full_content', ''))} karakter")
        logger.info(f"🐦 Tweet ({len(tweet_text)} kar): {tweet_text}")
        logger.info("=" * 60)
        
    except tweepy.errors.TooManyRequests:
        logger.error("Twitter rate limit aşıldı!")
        
    except Exception as e:
        logger.error(f"Hata: {e}")
        
    finally:
        is_busy = False

# --- WEB SUNUCUSU ---
def run_web_server():
    app.run(host='0.0.0.0', port=8000)

# --- ANA PROGRAM ---
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("SİSTEM BAŞLATILIYOR")
    logger.info("=" * 60)
    
    # API key kontrolü
    required_keys = [X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET, GROQ_API_KEY]
    if not all(required_keys):
        logger.critical("Eksik API anahtarları!")
        exit(1)
    
    # Web sunucusu
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("✅ Web sunucusu başlatıldı (port 8000)")
    
    # İlk görev
    logger.info("🚀 İlk görev çalıştırılıyor...")
    job()
    
    # Zamanlanmış görevler - 1 SAAT
    schedule.every(1).hour.do(job)
    logger.info("⏰ Bot 1 saatlik döngüye alındı")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot durduruldu")
    except Exception as e:
        logger.critical(f"Kritik hata: {e}")
        raise
