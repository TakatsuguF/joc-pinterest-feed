#!/usr/bin/env python3
"""JOC Goods - Pinterest catalog feed builder (per-country).

JOC Goods の Shopify (基軸=JPY) から商品を取得し、国別(通貨別)の Pinterest
カタログ CSV を feeds/ に生成する。US宛は既存のShopify連携がカバーするので
このビルダーは US 以外の人気国フィードだけを作る。

価格の考え方（重要）:
  JOCは全商品を**一定レート ≈ 131 JPY/USD**でUSD価格に設定している（実測で
  ¥37,900→$287, ¥3,000→$23 等、実効130-132で高度に一定）。実勢FX(≈162)より
  約23%高い＝JOCの意図する価格水準。よってフィード価格は素の為替換算ではなく
  「JOCのUSD価格を基準に各国通貨へ換算」する:
    usd  = jpy / JOC_JPY_PER_USD
    price(country) = usd * (1 USD あたりの当該国通貨)   ← 市場FX(USD基軸)
  これで各国価格がJOCのUS価格水準と揃う。通貨コードは付けない(数値のみ。
  Komorebiで通貨コード付与は警告180を招くと実測したため)。

必要な環境変数:
  JOC_GOODS_SHOPIFY_DOMAIN        例: 11a11b-45.myshopify.com
  JOC_GOODS_SHOPIFY_ACCESS_TOKEN  Admin API アクセストークン
"""
import csv
import html
import json
import os
import re
import sys
import time
import urllib.request

API_VERSION = "2026-01"
BRAND = "JOC Goods"
# JOCの実効価格レート（JPY→USD）。実測で商品横断ほぼ一定(130-132)。
JOC_JPY_PER_USD = 131.0

# 国別フィード: (出力ファイル名, 通貨コード) — Pinterest人気国順(US除く=Shopify連携済)。
# FXはUSD基軸(1 USD = rate 通貨)。open.er-api.com が対応する通貨のみ。
COUNTRIES = {
    "brazil": "BRL",
    "mexico": "MXN",
    "germany": "EUR",
    "france": "EUR",
    "united-kingdom": "GBP",
    "argentina": "ARS",
    "india": "INR",
    "canada": "CAD",
    "italy": "EUR",
    "spain": "EUR",
    "poland": "PLN",
    "turkey": "TRY",
    "australia": "AUD",
    "netherlands": "EUR",
    "colombia": "COP",
    "chile": "CLP",
    "saudi-arabia": "SAR",
    "sweden": "SEK",
    "switzerland": "CHF",
    "belgium": "EUR",
    "austria": "EUR",
    "portugal": "EUR",
    "philippines": "PHP",
    "indonesia": "IDR",
    "thailand": "THB",
    "south-korea": "KRW",
    "new-zealand": "NZD",
    "ireland": "EUR",
    # JOCの実客国だがPinterest人気上位ではない国（2026-07-08 実売データで追加）。
    # Shopify注文の発送先実績: SG=7位/NO/HK/DK/IL 各10-38注文。
    "singapore": "SGD",
    "norway": "NOK",
    "hong-kong": "HKD",
    "denmark": "DKK",
    "israel": "ILS",
}

# 小数を持たない通貨(整数で出す)
ZERO_DECIMAL = {"IDR", "KRW", "CLP", "JPY", "COP", "HUF"}

HEADERS = [
    "id", "item_group_id", "variant_names", "variant_values", "title",
    "link", "image_link", "price", "product_type", "google_product_category",
    "availability", "brand", "description", "condition",
]

ROOT = os.path.dirname(os.path.abspath(__file__))
FEEDS_DIR = os.path.join(ROOT, "feeds")
RATES_CACHE = os.path.join(ROOT, "fx_rates.json")
TAXONOMY_PATH = os.path.join(ROOT, "google_taxonomy.json")


def http_json(url, data=None, headers=None, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1} after error: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))


def fetch_fx_rates():
    """USD基準の為替レート(1 USD = rate 通貨)。失敗時は前回成功分にフォールバック。"""
    needed = sorted(set(COUNTRIES.values()))
    try:
        data = http_json("https://open.er-api.com/v6/latest/USD")
        if data.get("result") != "success":
            raise RuntimeError(f"er-api result={data.get('result')}")
        rates = {c: data["rates"][c] for c in needed}
        with open(RATES_CACHE, "w") as f:
            json.dump({"date": data.get("time_last_update_utc"), "rates": rates}, f, indent=1)
        print(f"FX (USD base) fetched ({data.get('time_last_update_utc')})")
        return rates
    except Exception as e:
        print(f"WARN: FX fetch failed ({e}); falling back to cached fx_rates.json", file=sys.stderr)
        with open(RATES_CACHE) as f:
            cached = json.load(f)
        print(f"FX rates from cache ({cached.get('date')})")
        return cached["rates"]


def shopify_graphql(query, variables=None):
    domain = os.environ["JOC_GOODS_SHOPIFY_DOMAIN"]
    token = os.environ["JOC_GOODS_SHOPIFY_ACCESS_TOKEN"]
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    data = http_json(
        f"https://{domain}/admin/api/{API_VERSION}/graphql.json",
        data=body,
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
    )
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes {
      legacyResourceId
      isGiftCard
      title
      tags
      onlineStoreUrl
      productType
      descriptionHtml
      featuredMedia { preview { image { url } } }
      catFb: metafield(namespace: "mc-facebook", key: "google_product_category") { value }
      catGs: metafield(namespace: "mm-google-shopping", key: "google_product_category") { value }
      variants(first: 100) {
        nodes {
          legacyResourceId
          sku
          price
          inventoryQuantity
          inventoryPolicy
          selectedOptions { name value }
          image { url }
        }
      }
    }
  }
}
"""

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def html_to_text(s):
    if not s:
        return ""
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|br)>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", " ", s)
    s = TAG_RE.sub("", s)
    s = html.unescape(s)
    return WS_RE.sub(" ", s).strip()


def resolve_category(value, taxonomy):
    """metafield値をPinterest用カテゴリ文字列に正規化(数値コード→タクソノミー文字列)。"""
    value = (value or "").strip()
    if "\t" in value:
        parts = [p.strip() for p in value.split("\t") if ">" in p]
        value = parts[-1] if parts else value.split("\t")[-1].strip()
    if value.isdigit():
        return taxonomy.get(value, "")
    return value


def build_base_rows():
    """通貨換算前(USD)の行を組み立てる。price(USD)を後で国別通貨に換算する。"""
    with open(TAXONOMY_PATH) as f:
        taxonomy = json.load(f)
    rows = []
    cursor = None
    n_products = 0
    while True:
        data = shopify_graphql(PRODUCTS_QUERY, {"cursor": cursor})
        page = data["products"]
        for p in page["nodes"]:
            if not p.get("onlineStoreUrl"):
                continue  # オンラインストア未公開は除外
            if p.get("isGiftCard"):
                continue  # ギフトカードは除外
            n_products += 1
            category = (resolve_category((p["catFb"] or {}).get("value"), taxonomy)
                        or resolve_category((p["catGs"] or {}).get("value"), taxonomy))
            desc = html_to_text(p.get("descriptionHtml"))
            fm = p.get("featuredMedia") or {}
            product_image = ((fm.get("preview") or {}).get("image") or {}).get("url", "")
            for v in p["variants"]["nodes"]:
                opts = v.get("selectedOptions") or []
                names = ", ".join(o["name"] for o in opts if o["name"] != "Title")
                values = ", ".join(o["value"] for o in opts if o["value"] != "Default Title")
                qty = v.get("inventoryQuantity") or 0
                if qty >= 1:
                    availability = "in stock"
                elif v.get("inventoryPolicy") == "CONTINUE":
                    availability = "preorder"
                else:
                    availability = "out of stock"
                image = (v.get("image") or {}).get("url") or product_image
                usd = float(v["price"]) / JOC_JPY_PER_USD  # JOCのUSD価格水準
                rows.append({
                    "id": v["legacyResourceId"],
                    "item_group_id": p["legacyResourceId"],
                    "variant_names": names,
                    "variant_values": values,
                    "title": p["title"],
                    "link": p["onlineStoreUrl"],
                    "image_link": image,
                    "price_usd": usd,
                    "product_type": p.get("productType") or "",
                    "google_product_category": category,
                    "availability": availability,
                    "brand": BRAND,
                    "description": desc,
                    "condition": "new",
                })
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    print(f"Shopify: {n_products} published products / {len(rows)} variants")
    return rows


def fmt_price(usd, rate, currency):
    val = usd * rate
    if currency in ZERO_DECIMAL:
        return str(int(round(val)))
    return f"{round(val, 2)}"


def write_feeds(rows, rates):
    os.makedirs(FEEDS_DIR, exist_ok=True)
    for slug, currency in COUNTRIES.items():
        rate = rates[currency]  # 1 USD = rate currency
        path = os.path.join(FEEDS_DIR, f"{slug}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(HEADERS)
            for r in rows:
                w.writerow([
                    r["id"], r["item_group_id"], r["variant_names"], r["variant_values"],
                    r["title"], r["link"], r["image_link"], fmt_price(r["price_usd"], rate, currency),
                    r["product_type"], r["google_product_category"],
                    r["availability"], r["brand"], r["description"], r["condition"],
                ])
    print(f"Wrote {len(COUNTRIES)} feeds x {len(rows)} rows to feeds/")


def main():
    rates = fetch_fx_rates()
    rows = build_base_rows()
    if len(rows) < 800:
        raise RuntimeError(f"Sanity check failed: only {len(rows)} variants fetched")
    write_feeds(rows, rates)
    with open(os.path.join(ROOT, "meta.json"), "w") as f:
        json.dump({
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "variants": len(rows),
            "countries": len(COUNTRIES),
            "jpy_per_usd": JOC_JPY_PER_USD,
        }, f, indent=1)


if __name__ == "__main__":
    main()
