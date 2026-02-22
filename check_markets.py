import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

url = 'https://gamma-api.polymarket.com/markets'
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
params = {'closed': 'false', 'limit': 200}

resp = httpx.get(url, params=params, headers=headers, timeout=30)
if resp.status_code == 200:
    markets = resp.json()
    now = datetime.now(ZoneInfo('UTC'))
    cutoff = now + timedelta(days=2)
    
    print('=== Markets ending within 48 hours ===\n')
    short_term = []
    for m in markets:
        end_str = m.get('endDate', '')
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                if now < end_dt < cutoff:
                    q = m.get('question', '')[:85]
                    hrs = (end_dt - now).total_seconds() / 3600
                    short_term.append((hrs, q))
            except Exception:
                pass
    
    # Sort by time remaining
    short_term.sort(key=lambda x: x[0])
    for hrs, q in short_term:
        print(f'{hrs:5.1f}h | {q}')
    
    if not short_term:
        print('No markets ending within 48 hours found.')
else:
    print(f'Error: {resp.status_code}')
