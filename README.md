# plugin.video.supermediago v2.0.0

Nieoficjalny dodatek Kodi dla usługi **SupermediaGO** (supermediago.pl),
oparty na analizie pliku HAR z przeglądarki.

---

## Wymagania

| Wymaganie | Uwagi |
|---|---|
| **Kodi 22 (Piers) lub nowszy** | Python 3 wymagany |
| **Aktywne konto SupermediaGO** | Tylko abonenci internetu Supermedia |
| **inputstream.adaptive** | Dostarczany z Kodi, włączyć w Menedżerze dodatków |
| **Widevine CDM** | Zainstalować przez [InputStream Helper](https://github.com/emilsvennesson/script.module.inputstreamhelper) |
| **script.module.requests** | Zazwyczaj już zainstalowany w Kodi |

---

## Instalacja

1. Pobierz `plugin.video.supermediago.zip`
2. Kodi → **Dodatki → Zainstaluj z pliku zip** → wybierz plik
3. Otwórz ustawienia dodatku → wpisz **login** i **hasło**
   (te same dane co na https://portal.supermedia.pl)
4. Przejdź do **SupermediaGO → Telewizja na żywo**

> **Ważne:** Przed pierwszym uruchomieniem zainstaluj Widevine CDM.
> W Kodi zainstaluj dodatek *InputStream Helper*, a następnie uruchom go
> i wybierz *Install Widevine*. Bez Widevine kanały nie będą odtwarzane.

---

## Funkcje

| Funkcja | Status |
|---|---|
| Telewizja na żywo | ✅ |
| Nakładka EPG (aktualny program) | ✅ |
| Replay TV / Catch-up (ostatnie 3 dni) | ✅ |
| Nagrania nPVR (odtwarzanie, usuwanie) | ✅ |
| Heartbeat CAP/Ping podczas odtwarzania | ✅ |
| DRM Widevine | ✅ |
| MPEG-DASH + HLS | ✅ |

---

## Szczegóły techniczne (analiza HAR)

### Potwierdzone endpointy API

Wszystkie endpointy zweryfikowane z pliku HAR przechwyconego podczas
sesji www.supermediago.pl (2026-06-04).

| Endpoint | Metoda | Opis |
|---|---|---|
| `POST /v1/InsysGoAccount/Authenticate` | POST | Logowanie → token UUID |
| `POST /v1/Devices/RegisterDevice` | POST | Rejestracja urządzenia (wymagane przed AcquireContent) |
| `GET /v2/InsysGoBootstrap/DeviceBootstrap` | GET | Bootstrap platformy |
| `POST /v1/EpgTile/FilterChannelTiles` | POST | Lista kanałów (ID + codename) |
| `POST /v2/Tile/GetTiles` | POST | Pełne metadane kafelków (nazwa, logo, kategorie) |
| `GET /v1/EpgTile/FilterNowOnTvTiles` | GET | Aktualny program na wszystkich kanałach |
| `POST /v1/EpgTile/FilterProgramTiles` | POST | Ramówka EPG dla zakresu dat |
| `GET /v1/Player/AcquireContent` | GET | URL strumienia + dane DRM + sesja CAP |
| `POST /v1/CAP/Ping` | POST | Heartbeat (co 60s) na cap-ha.app.insysgo.pl |
| `GET /v1/IpottTransaction/GetUserProducts` | GET | Produkty użytkownika |

### Przepływ autoryzacji

```
1. POST Authenticate
   Body: { platformCodename:"www", login, password, longExpiration:false }
   Response: { token:"549b1ec2-...", userId, tokenExpirationTime }

2. POST RegisterDevice (przed pierwszym AcquireContent)
   Body: { platformCodename, deviceKey, userToken, generalDeviceType:"2",
           deviceName, userAgent, operatingSystem, versionOs }
   Response: { isNew:true/false, result:{success:true} }
   Błąd 9122 = invalid_device_key → trzeba zarejestrować urządzenie

3. GET AcquireContent?platformCodename&deviceKey&token&codename&t=<msTimestamp>
   Response:
     DrmInfo: [ {DrmSystem:"Widevine", LicenseServerUrl, DrmChallengeCustomData} ]
     MediaFiles: [ { Formats: [
       {Type:9, Url:"...fhd.mpd"},   ← MPEG-DASH
       {Type:2, Url:"...fhd.m3u8"}   ← HLS
     ]}]
     Cap: { SessionId, CAPPublicUrl, CAPIntervalSeconds:60, SessionTimeoutSeconds:181 }
```

### Strumienie

- **CDN:** `supermedia3.cf.insyscd.net` (JWT w URL, ważność ~24h)
- **DASH:** `.../wd/wigo-<kanal>-fhd.mpd` (Type=9, Protection=4)
- **HLS:** `.../wh/wigo-<kanal>-fhd.m3u8` (Type=2, Protection=5)
- **DRM:** Widevine + PlayReady + FairPlay przez `wigo.la.drm.cloud`
- **Kodi używa:** tylko Widevine (`com.widevine.alpha`)

### CAP (Concurrent Access Protection)

Po rozpoczęciu odtwarzania, co 60 sekund musi być wysyłany heartbeat:
```
POST https://cap-ha.app.insysgo.pl/v1/CAP/Ping
Body: { SessionId, DurationSeconds, ProgressSeconds:-1, Counter, Status:"Play" }
```
Brak heartbeatu przez 181s = wymuszenie zakończenia sesji.

---

## Dostosowanie do innych operatorów InsysGO

W ustawieniach → Zaawansowane zmień:
- **URL API** → np. `https://api-petrus.app.insysgo.pl`
- **Kod platformy** → np. `android`

Wszystkie operatory InsysGO używają identycznego API.

---

## Znane ograniczenia

1. **Widevine CDM wymagany** – bez niego żaden kanał nie będzie działał.
   Kanały używają DRM (Protection=4 dla DASH, Protection=5 dla HLS).

2. **Limit urządzeń** – InsysGO ogranicza liczbę zarejestrowanych urządzeń.
   Błąd kodu 9120 = `device_limit_exceeded`. Odłącz inne urządzenie w portalu.

3. **Token ważny ~24h** – przy dłuższej przerwie wymagane ponowne logowanie.

4. **Replay TV** – tytuły programów wymagają dodatkowego zapytania GetTiles
   (program tiles mają tylko id/codename/from/to, nie tytuł).

---

## Zastrzeżenie

Nieoficjalny dodatek stworzony przez społeczność. Niepowiązany z:
- Supermedia Interactive sp. z o.o.
- Insys Video Technologies Co. Sp. z o.o. (Big Blue Marble)

Używaj zgodnie z Regulaminem SupermediaGO.
