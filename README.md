# JOC Goods - Pinterest Catalog Feeds (per-country)

Pinterest カタログ用の国別商品フィード(CSV)を毎日自動生成するリポジトリ。
US宛は既存のShopify連携がカバーするので、ここはUS以外の人気国フィードを作る。

- データ源: JOC Goods の Shopify Admin API(基軸=JPY)
- 価格: JOCは全商品を一定レート ≈131 JPY/USD でUSD価格に設定している(実測)。
  よって `usd = jpy / 131` を基準に、各国通貨へ市場FX(USD基軸)で換算する。
  素の為替換算だとJOCのUS価格より約23%安くなり不適切なため、この方式にしている。
  価格に通貨コードは付けない(数値のみ。Komorebiで通貨コード付与は警告180を招くと実測)。
- 生成: GitHub Actions が毎日 10:00 JST に `build_feeds.py` を実行し `feeds/*.csv` を更新
- 配信: GitHub Pages

## フィードURL

`https://<user>.github.io/joc-pinterest-feed/feeds/<country>.csv`

Pinterest人気国順(US除く): brazil, mexico, germany, france, united-kingdom,
argentina, india, canada, italy, spain, poland, turkey, australia, netherlands,
colombia, chile, saudi-arabia, sweden, switzerland, belgium, austria, portugal,
philippines, indonesia, thailand, south-korea, new-zealand, ireland

## 手動実行

Actions タブ -> build-feeds -> Run workflow
