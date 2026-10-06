"""Provider capabilities and dated context. No substituted execution observations."""
from datetime import datetime, timezone
import math
import requests
import streamlit as st


def _read(url, params=None):
    r = requests.get(url, params=params, timeout=12)
    if r.status_code != 200:
        return None, f'HTTP {r.status_code}'
    d = r.json()
    if isinstance(d, dict) and (d.get('status') == 'error' or 'error_code' in d):
        return None, 'Sağlayıcı hata kodu ' + str(d.get('code', d.get('error_code', '?')))
    return d, None


@st.cache_data(ttl=3600, show_spinner=False)
def daily_context(key):
    rows=[]
    for symbol, title, unit in [('DGS2','ABD 2Y','%'),('DGS10','ABD 10Y','%'),('DTWEXBGS','Geniş dolar endeksi (DXY değildir)','endeks')]:
        row={'Veri':title,'Kaynak':'FRED / '+symbol,'Sıklık':'Günlük','Değer':None,'Tarih':'','Birim':unit,'Durum':'Erişim doğrulanamadı'}
        try:
            if not key: raise ValueError('missing key')
            d,error=_read('https://api.stlouisfed.org/fred/series/observations',{
                'series_id':symbol,'api_key':key,'file_type':'json','sort_order':'desc','limit':10})
            if error:raise ValueError('provider')
            for obs in d['observations']:
                if obs['value'] == '.':continue
                value=float(obs['value'])
                if not math.isfinite(value):raise ValueError('nonfinite')
                date=datetime.strptime(obs['date'],'%Y-%m-%d').date()
                age=(datetime.now(timezone.utc).date()-date).days
                if age < 0:raise ValueError('future')
                row.update(Değer=value,Tarih=date.isoformat(),Durum='Bağlı' if age <= 5 else 'Eski veri')
                break
        except Exception: pass
        rows.append(row)
    return rows


@st.cache_data(ttl=60, show_spinner=False)
def reference_gold():
    """Independent spot check, not bid/ask and not an automatic substitute in signals."""
    try:
        d,error=_read('https://api.gold-api.com/price/XAU')
        if error:raise ValueError('provider')
        when=datetime.fromisoformat(d['updatedAt'].replace('Z','+00:00'))
        if when.tzinfo is None:raise ValueError('timezone')
        age=(datetime.now(timezone.utc)-when).total_seconds()
        value=float(d['price'])
        if d['symbol'] != 'XAU' or d.get('currency') != 'USD' or not math.isfinite(value) or value <= 0 or not 0 <= age <= 120:
            raise ValueError('invalid or stale')
        return {'price':value,'observed_at':when.isoformat(),'source':'Gold-API.com','execution_quote':False}
    except Exception:return None


def audit_twelve(key, rates=False):
    """Manual, bounded probe. Never return error messages, credentials, or request URLs."""
    rows=[]
    checks=[('Hesap kotası','api_usage',{}),('XAU/USD quote','quote',{'symbol':'XAU/USD'}),
            ('DXY sembol arama','symbol_search',{'symbol':'DXY'}),
            ('US2Y sembol arama','symbol_search',{'symbol':'US2Y'}),
            ('US10Y sembol arama','symbol_search',{'symbol':'US10Y'})]
    if rates:
        checks=[('ABD tahvil kataloğu','bonds',{'country':'United States','show_plan':'true'}),
                ('US2Y fiyat erişimi','quote',{'symbol':'US2Y'}),
                ('US2Y gün içi mum','time_series',{'symbol':'US2Y','interval':'5min','outputsize':3,'timezone':'UTC'}),
                ('Dolar endeksi ad arama','symbol_search',{'symbol':'US Dollar Index'})]
    for name,endpoint,params in checks:
        try:
            if not key:raise ValueError('missing key')
            d,error=_read('https://api.twelvedata.com/'+endpoint,{**params,'apikey':key})
            detail=error
            if not error:
                if endpoint=='quote':
                    detail='Alanlar: '+', '.join(k for k in ('symbol','timestamp','datetime','close','price','bid','ask') if k in d)
                    detail+=' · saat/değer: '+str(d.get('datetime','?'))+' / '+str(d.get('close',d.get('price','?')))
                    detail+=' · bid/ask '+('var' if 'bid' in d and 'ask' in d else 'yok')
                elif endpoint=='bonds':
                    matches=d.get('result',{}).get('list',[])
                    detail='; '.join(str(m.get('symbol'))+' / '+str(m.get('name'))+' / '+str(m.get('access',{})) for m in matches[:20]) or 'Katalog sonucu yok'
                elif endpoint=='time_series':
                    detail='meta: '+str({k:d.get('meta',{}).get(k) for k in ('symbol','interval','type','exchange','currency')})+' · mumlar: '+str([{k:v.get(k) for k in ('datetime','close')} for v in d.get('values',[])[:3]])
                elif endpoint=='api_usage':
                    detail=' · '.join(f'{k}: {d[k]}' for k in ('current_usage','plan_limit','daily_usage','daily_limit') if k in d)
                else:
                    matches=d.get('data',[])
                    detail='; '.join(str(m.get('symbol'))+' / '+str(m.get('instrument_type',m.get('type','?')))+' / '+str(m.get('instrument_name',m.get('name','?'))) for m in matches[:5]) or 'Eşleşen sembol bulunamadı'
            rows.append({'Kontrol':name,'Durum':'Başarılı' if not error else 'Erişim yok','Sonuç':detail or 'Yanıt biçimi doğrulanamadı'})
        except Exception as e:rows.append({'Kontrol':name,'Durum':'Bağlantı/test hatası','Sonuç':type(e).__name__})
    return rows


def render_data_access(td_key, fred_key):
    with st.expander('Veri kaynakları: erişim ve gereksinimler',expanded=False):
        st.write('**Mevcut anahtarlarla günlük makro veriler**')
        st.dataframe(daily_context(fred_key),hide_index=True,use_container_width=True)
        st.caption('Günlük değerler bağlam içindir; 90/180 saniyelik işlem kontrollerini veya gerçek DXY ölçümünü karşılamaz.')
        ref=reference_gold()
        if ref:
            st.write(f"Bağımsız altın fiyat kontrolü: {ref['price']:.2f} USD/ons · {ref['observed_at']} · {ref['source']}")
            st.caption('Anahtarsız referans fiyat. Bid/ask ve XM işlem fiyatı değildir; sinyallerin fiyat kaynağı otomatik değiştirilmez.')
        else:st.caption('Bağımsız fiyat kontrolü: güncel veri doğrulanamadı.')
        st.write('**Bağlanan gün içi kaynaklar:** Swissquote XAU/USD referans bid/ask ve TradingView TVC DXY/2Y/10Y. Kaynak saatleri ve geçmiş yeterliliği Risk / Setup Gate tablosunda denetlenir. Başlangıçta spread ve eşzamanlı makro geçmişi toplanır; eski/eksik veri işlem onayı vermez.')
        if st.button('Mevcut Twelve Data erişimini test et'):
            st.session_state['twelve_access_audit']=audit_twelve(td_key)
        if 'twelve_access_audit' in st.session_state:
            st.dataframe(st.session_state['twelve_access_audit'],hide_index=True,use_container_width=True)
        if st.button('Tahvil ve endeks erişimini ayrıntılı test et'):
            st.session_state['rates_access_audit']=audit_twelve(td_key,rates=True)
        if 'rates_access_audit' in st.session_state:
            st.dataframe(st.session_state['rates_access_audit'],hide_index=True,use_container_width=True)
        st.caption('Test yalnız okuma yapar. Anahtarlar gösterilmez; Test başına en fazla 5 istek gönderilir; dakika kotası nedeniyle testler arasında en az 1 dakika bırakın. Abonelik satın alınmaz.')
