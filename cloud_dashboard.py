"""Cloud-only read view. Collection and notifications run independently in Cloudflare."""
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title="XAU v43 · Bulut", layout="wide")
st.title("XAU v43 · Bulut izleyicisi")
st.caption("Mac ve MT5 kapalıyken çalışır · Beş dakikalık kontrol · Otomatik emir yok")

@st.cache_data(ttl=30, show_spinner=False)
def read_state(url, token):
    response = requests.post(url.rstrip('/') + '/cloud-state', headers={'Authorization': 'Bearer ' + token}, timeout=15, allow_redirects=False)
    response.raise_for_status()
    return response.json()

@st.fragment(run_every="30s")
def panel():
    url = st.secrets.get('TELEGRAM_RELAY_URL', '')
    token = st.secrets.get('TELEGRAM_RELAY_TOKEN', '')
    if not url or not token:
        st.error('Bulut bağlantı ayarları henüz eklenmedi.')
        return
    try:
        state = read_state(url, token)
    except Exception:
        st.error('Bulut durumuna erişilemiyor. Son karar güncel kabul edilmemeli.')
        return
    checked = state.get('checked_at')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(checked.replace('Z', '+00:00'))).total_seconds() if checked else float('inf')
    st.caption('Son bulut kontrolü: ' + str(checked) + ' · Kaynak: Twelve Data XAU/USD')
    st.info('Referans fiyatlarla kapanmış M5 mumları izlenir. XM bid/ask, gerçek spread ve açık pozisyonlar bu sürümde izlenmez. Bu, yerel MT5 sürümünden ayrı bir bulut profilidir.')
    engine = state.get('engine', {})
    green = engine.get('green') if age <= 480 and state.get('data_ready') else None
    if age > 480:
        st.error('Bulut kontrolü gecikti; eski sinyal geçerli gösterilmiyor.')
    elif green:
        st.success(f"🟢 GİRİŞ ADAYI · {green['direction']} · {green['kind']}")
        st.write(f"Referans giriş: {green['entry']:.2f} · Hedef: {green['target']:.2f} · Teknik iptal: {green['invalidation']:.2f}")
        st.warning('Teknik iptal broker stopu değildir. Bu sinyal işlem açmaz.')
    else:
        st.warning('BEKLE · ' + engine.get('reason', state.get('signal_reason', 'Başlatılıyor')))
    bars = state.get('series', {}).get('5min', {}).get('bars', [])
    if bars:
        import pandas as pd
        frame = pd.DataFrame(bars[-100:])
        frame['Zaman'] = pd.to_datetime(frame['close_at'], unit='ms', utc=True)
        st.line_chart(frame.set_index('Zaman')['close'])
    st.subheader('İzlenen seviyeler')
    st.dataframe(engine.get('zones', []), use_container_width=True)
    st.subheader('Son kapanışta değerlendirilen adaylar')
    st.dataframe(engine.get('candidates', []), use_container_width=True)
    st.subheader('Yeşil sinyal geçmişi — güncel giriş önerisi değildir')
    st.dataframe(state.get('history', []), use_container_width=True)
    with st.expander('Çalışma ve veri durumu'):
        st.json({'veri_hazır': state.get('data_ready'), 'motor_hazır': state.get('signal_engine_ready'), 'profil': state.get('profile'), 'hatalar': state.get('errors'), 'son_bildirim': state.get('delivery'), 'emir_iletimi': False})
panel()
