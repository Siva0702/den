# models/news/news_fetcher.py
import xml.etree.ElementTree as ET
import requests

class RealtimeNewsFetcher:
    def __init__(self):
        self.yahoo_rss = "https://finance.yahoo.com/news/rssindex"
        self.coindesk_rss = "https://www.coindesk.com/arc/outboundfeeds/rss/"

    def fetch_latest_headlines(self, limit: int = 5) -> list:
        """
        Fetches breaking headlines from TradFi, Commodity, and Crypto RSS feeds.
        """
        headlines = []
        
        # 1. Parse Yahoo Finance RSS (TradFi, Commodities, Fed Macro)
        try:
            resp = requests.get(self.yahoo_rss, timeout=5)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:limit]:
                    title_elem = item.find("title")
                    if title_elem is not None and title_elem.text:
                        headlines.append({"source": "Yahoo Finance (TradFi/Macro)", "headline": title_elem.text})
        except Exception as e:
            print(f"[!] Yahoo RSS Fetch Error: {e}")

        # 2. Parse CoinDesk RSS (Crypto)
        try:
            resp = requests.get(self.coindesk_rss, timeout=5)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:limit]:
                    title_elem = item.find("title")
                    if title_elem is not None and title_elem.text:
                        headlines.append({"source": "CoinDesk (Crypto)", "headline": title_elem.text})
        except Exception as e:
            print(f"[!] CoinDesk RSS Fetch Error: {e}")

        return headlines

if __name__ == "__main__":
    fetcher = RealtimeNewsFetcher()
    headlines = fetcher.fetch_latest_headlines(limit=3)
    print("=" * 60)
    print("      REAL-TIME BREAKING NEWS WIRE      ")
    print("=" * 60)
    for h in headlines:
        print(f"[{h['source']}] {h['headline']}")
