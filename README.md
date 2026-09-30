# MASTER Mobile Update Channel

Bu public repo yalnız **MASTER Android veri güncelleme kanalıdır**. Android kaynak kodu ve ana MASTER SQLite veritabanı burada tutulmaz.

## Akış

`Sahadan günlük bülten -> doğrulama -> canonical delta -> manifest -> APK ↻`

## Yayın dosyaları

- `updates/manifest.json` — APK'nın kontrol ettiği kanal durumu
- `updates/releases/master_delta_current.csv.gz` — APK baseline'ından sonraki doğrulanmış, kümülatif canonical delta
- `tools/master_update_pipeline.py` — Sahadan verisini canonical formata dönüştüren üretici
- `.github/workflows/update-master.yml` — **günde 1 kez** çalışan yayın workflow'u (04:17 UTC / Türkiye saatiyle yaklaşık 07:17)

## Canonical sütunlar

`match_id,date,time,league_id,league,home,away,home_score,away_score,ms1,msx,ms2,kg_var,kg_yok,alt25,ust25`

## Kalite kuralları

- MASTER tarihsel analiz havuzuna **yalnız FT skoru belli olan maçlar** yayınlanır.
- Maç kimliği öncelikle Sahadan `Maç_ID` (`match_id`) ile tutulur.
- `Maç_ID` yoksa geçici fallback: `tarih + lig + ev sahibi + deplasman`.
- Kaynakta daha sonra `Maç_ID` görünürse fallback kayıt gerçek ID'ye taşınır.
- Aynı maç tekrar gelirse yeni satır oluşturmak yerine mevcut canonical kayıt güncellenir.
- Yeni çekimde bazı oran alanları boş gelirse önceki dolu değerler silinmez.
- Maç_ID kapsaması beklenmedik şekilde düşerse veya MS marketleri tamamen çözümlenemezse workflow yayın yapmadan hata verir.
- Her paket SHA256 ile doğrulanır.
- Workflow ilk yayında APK baseline tarihinin ertesi gününden başlar; sonraki çalışmalarda gecikmiş skor/oran düzeltmeleri için son 7 günü yeniden kontrol eder.

## APK baseline

İlk kanal baseline'ı:

- Son tarih: `2026-09-16`
- Toplam maç: `404435`

Yeni APK baseline sürümü çıkarıldığında bu iki değer kontrollü olarak ileri taşınabilir.
