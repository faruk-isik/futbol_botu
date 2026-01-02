import tweepy
import os
import re
import logging
import textwrap
from flask import Flask
from google import genai
from google.genai import types

# --- Log Ayarları ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 1. X (Twitter) API Bağlantısı ---
def get_v2_client():
    try:
        client = tweepy.Client(
            consumer_key=os.environ.get('CONSUMER_KEY'),
            consumer_secret=os.environ.get('CONSUMER_SECRET'),
            access_token=os.environ.get('ACCESS_TOKEN'),
            access_token_secret=os.environ.get('ACCESS_TOKEN_SECRET')
        )
        logging.info("✅ X V2 İstemcisi Başarıyla Oluşturuldu!")
        return client
    except Exception as e:
        logging.error(f"❌ X API Bağlantı Hatası: {e}")
        return None

# --- 2. Metin Temizleme Mekanizması ---
def absolute_cleaner(text):
    """Metnin sonundaki noktadan sonra gelen her türlü ek kelimeyi siler."""
    if not text:
        return ""

    # 1. Hashtag ve Emojileri temizle
    text = re.sub(r'#\S+', '', text)
    text = text.encode('ascii', 'ignore').decode('ascii')

    # 2. Satır sonlarını boşluğa çevir ve temizle
    text = " ".join(text.split()).strip()

    # 3. NOKTA OPERASYONU: 
    # Metnin en sonundaki noktayı bulur ve sonrasındaki kelimeleri (etiketleri) atar.
    if "." in text:
        # Sağdan sola doğru ilk noktayı bul (son cümlenin sonu)
        parts = text.rsplit(".", 1)
        main_body = parts[0]
        after_dot = parts[1].strip()

        # Eğer noktadan sonra sadece 1-3 kelime varsa (örn: "Asgari Ücret" veya "Ekonomi")
        # Bunlar haber değil etikettir, onları çöpe atıyoruz.
        if len(after_dot.split()) <= 3:
            text = main_body + "."
        else:
            # Eğer noktadan sonra uzun bir cümle devam ediyorsa koru
            text = main_body + "." + after_dot

    return text.strip()

# --- 3. Gemini 2.0 İçerik Üretimi ---
def generate_gemini_tweet():
    fallback_text = "Türkiye gündemindeki gelişmeleri takip ediyoruz."
    
    try:
        client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
        
        # Modelin 'sosyal medya' alışkanlıklarını kırmak için sert talimatlar
        system_rules = (
            "Sadece ham haber metni yazarsın. "
            "Bugünlerde Türkiye'de gündem olmuş, çok fazla konuşulan, güncel futbol müsabakalarıyla da ilgili olabilir alaycı bir dille (küfür ve hakaret içermeyen) yazı yaz. . Yazı Türkçe olacak. Emoji kullanma. Yazının sonuna yazıyla ilgili bir hashtag ekle. Eklediğin hashtag dahil maksimum 230 karakter olsun."
            "Sadece düz metin gönder."
        )
        
        user_prompt = (
            "Sadece ham haber metni yazarsın. "
            "Bugünlerde Türkiye'de gündem olmuş, çok fazla konuşulan, güncel futbol müsabakalarıyla da ilgili olabilir alaycı bir dille (küfür ve hakaret içermeyen) yazı yaz. . Yazı Türkçe olacak. Emoji kullanma. Yazının sonuna yazıyla ilgili bir hashtag ekle. Eklediğin hashtag dahil maksimum 230 karakter olsun."
            "Sadece düz metin gönder."
        )
        
        logging.info("--- Gemini İçerik Üretimi Başladı ---")
        
        response = client.models.generate_content(
            model='gemini-2.0-flash', 
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_rules,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0 # Talimatlara maksimum sadakat
            )
        )
        
        raw_text = response.text.strip() if response.text else fallback_text
        
        # Kod seviyesinde filtreleme
        final_text = absolute_cleaner(raw_text)
        
        # Eğer temizlikten sonra metin boş kalırsa fallback kullan
        return final_text if final_text else fallback_text

    except Exception as e:
        logging.error(f"❌ Gemini Hatası: {e}")
        return fallback_text

# --- 4. Bot Çalıştırma ---
def run_bot():
    x_client = get_v2_client()
    if not x_client: return
    
    content = generate_gemini_tweet()
    
    try:
        # X'in karakter sınırına karşı son güvenlik önlemi
        content = textwrap.shorten(content, width=275, placeholder="...")
        
        x_client.create_tweet(text=content)
        logging.info(f"🚀 Tweet Başarıyla Atıldı: {content}")
    except Exception as e:
        logging.error(f"❌ Tweet Gönderim Hatası: {e}")

# --- 5. Flask Sunucu ---
app = Flask(__name__)

@app.route('/trigger')
def trigger():
    run_bot()
    return "Bot tetiklendi ve süreç tamamlandı.", 200

@app.route('/')
def home():
    return "Haber Botu Çalışıyor...", 200

if __name__ == "__main__":
    # Render veya diğer cloud platformları için port ayarı
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)



