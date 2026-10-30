# DSL HELP (@DSLHELPbot)

Logistika kompaniyasi uchun bitta Telegram bot: Dispatch, Maintenance, ELD, Safety,
Accounting, HR bo'limlari bir botda. Bot **guruhni** taniydi (bo'limni emas) — har bir
guruh `/setgroup` orqali bitta bo'limga bog'lanadi, driverlar o'z guruhida ishlaydi.

Hozirgi holat: **1, 2, 3 va 4-bosqich to'liq ishlaydi** (rollar, audit log, til,
zaxira, web panel, va Claude API orqali BOL OCR + avto-yo'naltirish) — 45
avtomatlashtirilgan test bilan tekshirilgan (soxta/fake Telegram API bilan, tarmoqsiz;
AI qismi soxta Claude javobi bilan sinalgan, haqiqiy API chaqiruvisiz). Ovozli
xabarni matnga aylantirish qo'shilmadi — sababi pastda yozilgan.

## BotFather sozlamalari (MUHIM)

Bot guruh ichida ishlashi uchun:

1. [@BotFather](https://t.me/BotFather) ga boring → `/mybots` → botingizni tanlang →
   **Bot Settings** → **Group Privacy** → **Turn off**.
   - Bu yoqilmasa, bot guruhdagi oddiy xabarlarni (masalan `/BOL` captionli rasm, yoki
     intake matnlari) ko'rmaydi, faqat `@botname` bilan yozilgan xabarlarni ko'radi.
2. Agar bot allaqachon biror guruhga qo'shilgan bo'lsa, Group Privacy'ni o'zgartirgandan
   keyin **botni guruhdan chiqarib, qayta qo'shing** (Telegram eski guruhlarga yangi
   privacy sozlamasini avtomatik tatbiq etmaydi).
3. Botni admin qilish shart emas, lekin guruhda xabar yuborishi va caption'larni
   o'qishi uchun oddiy a'zo bo'lishi kifoya.

## O'rnatish

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` ichida to'ldiring:

| O'zgaruvchi | Izoh |
|---|---|
| `BOT_TOKEN` | BotFather bergan token |
| `DATABASE_URL` | Supabase Postgres URL (`postgres://...`) yoki test uchun `sqlite:///dslhelp.db` |
| `ADMIN_IDS` | Admin Telegram user ID'lari, vergul bilan: `123456,789012` |
| `TIMEZONE` | Masalan `Asia/Tashkent` |
| `REMINDER_POLL_SECONDS` | Ochiq ticketlarni tekshirish oralig'i (default 60) |
| `WEBHOOK_URL` | Bo'sh qoldirilsa — polling rejimi (tavsiya etiladi) |
| `PORT` | Agar o'rnatilsa — UptimeRobot uchun health-check server shu portda ochiladi |
| `DOC_WARNING_DAYS` | Hujjat muddati tugashiga necha kun qolganda ogohlantirish (default 14) |
| `DAILY_REPORT_HOUR` / `DAILY_REPORT_MINUTE` | Kunlik hisobot vaqti (default 20:00, `TIMEZONE` bo'yicha) |
| `BACKUP_DIR` | Kunlik JSON backup fayllari qayerga yoziladi (default `backups/`) |
| `BACKUP_RETENTION_DAYS` | Nechta kunlik backup saqlanadi (default 14) |
| `BACKUP_HOUR` / `BACKUP_MINUTE` | Kunlik backup vaqti (default 03:00, `TIMEZONE` bo'yicha) |
| `WEB_PANEL_TOKEN` | Web panelga kirish uchun token (`?token=...`). **Bo'sh qoldirmang** — aks holda panel himoyasiz ochiq bo'ladi |
| `WEB_PANEL_PORT` | Web panel porti (default 8090) |
| `ANTHROPIC_API_KEY` | Bo'sh — AI (OCR/avto-yo'naltirish) o'chiq; to'ldirilsa — yoqiladi |
| `ANTHROPIC_MODEL` | Claude modeli (default `claude-haiku-5-5`) |

Ishga tushirish:

```bash
python -m dslhelp.main
```

Birinchi marta ishga tushganda kerakli jadvallar avtomatik yaratiladi (`init_db`).

## 2-bosqich: nima qo'shildi

- **Kunlik hisobot**: har kuni `DAILY_REPORT_HOUR:DAILY_REPORT_MINUTE`da Dispatch guruhiga
  va har bir adminga DM qilib yuboriladi: faol loadlar soni, ochiq ticketlar (bo'lim
  bo'yicha), bugun PTI qilmagan driverlar, PTI so'roviga umuman javob bermagan
  driverlar, muddati yaqinlashgan hujjatlar.
- **Qidiruv**: `/driver <ism>` — driver profili (guruhi, joriy load, ochiq ticketlari,
  oxirgi PTI holati, hujjatlari); bir nechta mos kelsa — tugmalar bilan tanlash.
  `/find <matn>` — driver, ticket va loadlar bo'yicha qidiradi, har bir natija tugma
  bo'lib chiqadi, bosilganda to'liq karta ko'rsatiladi.
- **Export**: `/export` → davr tanlash tugmalari (Bugun / Hafta / Oy) → shu davrdagi
  ticketlar, loadlar va PTI tarixi `.xlsx` fayl qilib yuboriladi (Google Sheets o'rniga
  — kredensial talab qilmaydigan muqobil, spec shart qilgan "yoki .xlsx" variantidan).
- **Ustuvorlik (Urgent)**: har bir ticket kartasida 🔴 Urgent tugmasi (bo'lim xodimi
  bosadi). Bosilganda: ticket darhol Dispatch guruhiga xabar qiladi (agar ticket
  Dispatch'ning o'zidan bo'lmasa), eslatma oralig'i 2 daqiqaga tushadi, kartada 🔴
  belgi doimiy ko'rinadi.
- **Load kartochkasi**: `/newload Ism Familiya | Load# | Broker | PU manzil | PU vaqt |
  DEL manzil | DEL vaqt` (Dispatch guruhida) — to'liq load karta yaratadi va driverga
  tayinlaydi; driver keyin shu kartaga `/BOL`/`/POD`/`/PU`/`/DEL` orqali yozadi.
  `/loads` — Dispatch guruhida faol loadlar ro'yxati (raqami, broker, driver, holat).

## 3-bosqich: nima qo'shildi

- **Dispatch**: load kartasida `pu_at`/`del_at` endi haqiqiy sana/vaqt (matn emas) —
  `/newload`dagi PU/DEL vaqt ustunlari erkin formatda yozilsa ham avtomatik o'qiladi
  (`python-dateutil`). Appointment vaqtidan 2 soat va 30 daqiqa oldin driverga eslatma;
  vaqt o'tib ketsa (15 daqiqa muddat bilan) Dispatch guruhiga Late alert. Driver
  menyusida 🗺 **Lokatsiya** tugmasi — bosilganda Telegram lokatsiya so'rov tugmasi
  chiqadi, yuborilgan lokatsiya saqlanadi va Dispatch guruhiga Google Maps havolasi
  bilan yuboriladi (`/driver` profilida ham ko'rinadi).
- **Maintenance — Truck**: `/truck <raqami>` (yangi truck qo'shadi yoki tarixini
  ko'rsatadi: driver, milya, oxirgi/keyingi PM, service va PTI tarixi), `/assigntruck
  <raqam> | <driver ism>`, `/mileage <raqam> <masofa>`. PM (moy almashtirish) milya
  yoki kun bo'yicha muddati kelganda Maintenance guruhiga avtomatik eslatma;
  `/pmdone <raqami>` muddatni yangilaydi. Har bir `/t` so'rovi va PTI yozuvi driverning
  joriy truckiga bog'lanadi (tarix aniq saqlanishi uchun).
- **Maintenance — xarajat**: endi maintenance ticketini ✅ Done bilan yopishdan oldin
  bot ish turi va summani so'raydi ("Moy almashtirish | 450000" formatida); shundan
  keyin ticket yopiladi va xarajat saqlanadi. `/costs` — joriy oy xarajatlari truck
  bo'yicha jamlanib ko'rsatiladi.
- **Safety**: Incident/Inspection ticketini Done qilishdan oldin natija matni so'raladi
  (inspection natijasi yoki accident log sifatida saqlanadi). `/csa <ball> [izoh]` — CSA
  ball yozish. `/safetystats` — joriy oy: Incident/Inspection soni va oxirgi 5 CSA ball.
- **HR — onboarding**: yangi driver `/register` qilganda bot avtomatik CDL, keyin
  MEDICAL hujjat rasmini va amal qilish muddatini so'raydi; har biri HR guruhiga
  Approve/Reject tugmasi bilan yuboriladi. Reject bosilsa, shu hujjat turi avtomatik
  qayta so'raladi.
- **Accounting**: hisob-kitob (PDF/rasm) xabariga reply qilib `/settlement <driver
  ism>` yozilsa, fayl to'g'ridan-to'g'ri shu driverning shaxsiy guruhiga yuboriladi.
  Intake menyusidagi "Receipt" turi endi chek turini (⛽ Fuel / ⚖️ Scale / 📦 Lumper) va
  summani so'raydi, keyin rasmni qabul qiladi; `/receipts` — joriy oy cheklari tur
  bo'yicha jamlanib ko'rsatiladi.

## 4-bosqich (TEXNIK): nima qo'shildi

- **Rollar**: uch daraja — **admin** (`ADMIN_IDS`), **bo'lim xodimi** (`/staffreg Ism
  Familiya` — bo'lim guruhida o'zini-o'zi ro'yxatdan o'tkazadi), **driver**
  (`/register`). Pul/hujjat/truck bilan bog'liq buyruqlar (`/newload`,
  `/assigntruck`, `/mileage`, `/pmdone`, `/csa`, `/settlement`, `/doc`) va ticket
  kartasidagi 🕐/✅/🔴 tugmalari, shuningdek HR Approve/Reject endi faqat shu bo'lim
  xodimi yoki admin uchun ishlaydi; boshqa birov bossa, "faqat ... xodimlari uchun"
  deb rad etiladi. Sof o'qish buyruqlari (`/find`, `/driver`, `/pending`, `/costs`,
  h.k.) ochiq qoldirildi.
- **Audit log**: muhim amallar (`/setgroup`, `/staffreg`, ticket yopish/urgent,
  `/newload`, `/assigntruck`, `/mileage`, `/pmdone`, `/csa`, `/settlement`, `/doc`,
  HR approve/reject) `AuditLog` jadvaliga yoziladi. `/auditlog [N]` (admin, default 20)
  — oxirgi yozuvlarni vaqt, kim va nima qilgani bilan ko'rsatadi.
- **Til**: driver `/til` yoki menyudagi 🌐 **Til** tugmasi bilan o'zbek/rus/ingliz
  tanlaydi (`Driver.language`). Tarjima jadvali [i18n.py](dslhelp/i18n.py)da; hozir
  asosiy menyu, ro'yxatdan o'tish va BOL tasdiqlash shu orqali ishlaydi — qolgan
  matnlarni shu jadvalga qo'shish mexanik ish (qolgan modullar hali faqat o'zbek
  tilida, bu bilib turilgan cheklov).
- **Zaxira**: har kuni (`BACKUP_HOUR:BACKUP_MINUTE`) butun bazani (har bir jadval)
  bitta JSON faylga yozadi — Postgres va SQLite uchun bir xil ishlaydi (SQLAlchemy
  orqali, `pg_dump`ga bog'liq emas). `BACKUP_RETENTION_DAYS`dan eski fayllar
  avtomatik o'chiriladi.
- **Web panel**: [webapp.py](dslhelp/webapp.py) — FastAPI asosida o'qish-uchun-panel
  (drivers, loads, tickets, trucks, audit log), botning o'zi bilan bir bazani
  o'qiydi. `python -m dslhelp.webapp` bilan alohida ishga tushiriladi (`WEB_PANEL_PORT`,
  default 8090). **`WEB_PANEL_TOKEN`ni albatta o'rnating** — aks holda panel
  himoyasiz: har kim driver ismlari va lokatsiyalarini ko'radi.
  - **USDELD integratsiyasi haqida**: spec "USDELD ilovasi bilan integratsiya"ni
    talab qiladi, lekin USDELD API/format haqida hech qanday ma'lumot berilmagan —
    shu sababli integratsiya qilinmadi. Agar kerak bo'lsa, USDELD'ning API
    hujjatlarini (yoki endpoint/kalit) bering, shu asosda qo'shamiz.

## 4-bosqich (AI): nima qo'shildi, nima qo'shilmadi

AI qismi `ANTHROPIC_API_KEY` o'rnatilgandagina ishlaydi (bo'sh bo'lsa, bu funksiyalar
sezilmasdan o'chiq turadi, qolgan bot odatdagidek ishlaydi). Model: `ANTHROPIC_MODEL`
(default `claude-haiku-5-5`). Barcha chaqiruvlar [ai.py](dslhelp/ai.py)da.

- **BOL OCR**: driver `/BOL` rasmini yuborganda (agar load'da Load# hali yo'q bo'lsa),
  bot rasmni Claude'ga yuborib Load#/manzil/summani o'qishga harakat qiladi, so'ng
  "🤖 ... Tasdiqlaysizmi?" deb driverdan tasdiq so'raydi — faqat tasdiqlangandan
  keyin (bo'sh maydonlarga) load kartochkasiga yoziladi. Xatolik yoki javob bo'lmasa
  — jim o'tkazib yuboriladi, BOL qabul qilish o'zi buzilmaydi.
  - Eslatma: "summa" faqat ko'rsatiladi (load kartochkasida narx/rate ustuni yo'q),
    saqlanmaydi.
- **Avto-yo'naltirish**: driver guruhida hech qanday faol oqim yo'q paytda erkin matn
  yozsa (8 belgidan uzun), Claude matnni ELD/Safety/Accounting/HR'ning mavjud
  turlaridan biriga moslashtirishga harakat qiladi (Maintenance'ning `/t` bundan
  mustasno — uning "ikkinchi marta so'ralmasin" mantig'i bu yerga tozo qo'shilmaydi).
  Mos kelsa — "Ticket ochaymi?" deb so'raydi, Ha/Yo'q tugmasi bilan; faqat "Ha"
  bosilgandagina ticket yaratiladi.
- **Ovozli xabar → matn**: **qo'shilmadi**. Spec "Claude API orqali" deb so'ragan,
  lekin Claude API'da audio/nutqni matnga aylantirish imkoniyati yo'q — shuning uchun
  bu talabni aynan yozilganidek bajarib bo'lmaydi. Foydalanuvchi bilan kelishilgancha,
  hozircha ovozli xabarlar hech qanday maxsus ishlov qilinmaydi (oddiy audio fayl
  sifatida qoladi). Agar kerak bo'lsa, alohida nutqni-matnga-aylantiruvchi xizmat
  (masalan OpenAI Whisper, Google Speech-to-Text) ulanishi mumkin — shu uchun o'z
  API kalitingiz kerak bo'ladi.

OCR va avto-yo'naltirish `tests/test_phase4_ai.py`da soxta Anthropic mijoz (fake
client) bilan sinalgan — bu haqiqiy tarmoq chaqiruvisiz, lekin `ai.py`ning JSON
parslash/tekshirish mantiqini to'liq tekshiradi.

## Test qilish (soxta Telegram API bilan)

Hech qanday real Telegram tarmog'iga ulanmasdan, barcha handlerlar `tests/fakes.py`
dagi soxta Bot/Update/CallbackQuery orqali sinaladi:

```bash
pip install pytest pytest-asyncio
pytest tests -v
```

`tests/test_phase1.py` quyidagilarni tekshiradi: `/setgroup` + `/register`, ELD
malfunction → Dispatch+Safety forward, Safety Incident → Dispatch forward, Maintenance
`/t` ikki marta so'ralganda "asked again" xatti-harakati, ticket Done tugmasi driver
guruhiga xabar yuborishi, Dispatch BOL→POD→Accounting zanjiri, BOBTAIL holati, PTI
to'liq aylanishi (tanlash → yuborish → rasm → Done → Approve/Redo), HR `/doc`/`/docs`,
va eslatma (reminder) ishlashi.

`tests/test_phase2.py` qo'shimcha tekshiradi: `/newload` + `/loads` + keyingi
`/BOL`ning mavjud load kartaga yozilishi, 🔴 Urgent bosilganda Dispatch'ga darhol
xabar va 2-daqiqalik eslatma, kunlik hisobotdagi barcha metrikalar, `/driver`/`/find`
qidiruvi va natija tugmalari, `/export` orqali `.xlsx` fayl yuborilishi.

`tests/test_phase3.py` qo'shimcha tekshiradi: PU appointment eslatmalari (2 soat → 30
daqiqa → Late alert) bosqichma-bosqich, lokatsiya yuborish va Dispatch'ga Maps havolasi
borishi, truck yaratish/tayinlash/milya + PM eslatmasi (va `/pmdone`dan keyin
tovlanmasligi), maintenance xarajat oqimi (Done bosilganda ticket darhol yopilmasligi,
keyin yopilishi) + `/costs`, safety natija oqimi + `/csa` + `/safetystats`, HR
onboarding to'liq aylanishi (CDL → Approve → MEDICAL → yakun) va Reject qayta
so'ralishi, `/settlement` forward va Receipt oqimi + `/receipts`.

`tests/test_phase4.py` qo'shimcha tekshiradi: `/staffreg` qilmagan odam `/newload`ni
ishlata olmasligi (keyin ro'yxatdan o'tgach ishlay boshlashi), ticket tugmalari xuddi
shunday faqat shu bo'lim xodimi/admin uchun ishlashi, `/auditlog`da `/setgroup` yozuvi
ko'rinishi, `/til` orqali tanlangan til menyu matnini o'zgartirishi, zaxira job'i
to'g'ri JSON yozishi, va web panel: token berilmasa/noto'g'ri bo'lsa 403, to'g'ri
bo'lsa barcha sahifalar (drivers/loads/tickets/trucks/audit) 200 va driver ismini
ko'rsatishi (`starlette.testclient.TestClient` bilan, haqiqiy HTTP server ishga
tushirmasdan).

`tests/test_phase4_ai.py` qo'shimcha tekshiradi: `ai.py`ning JSON parslash/variant
tekshirish mantiqi (soxta Anthropic client bilan — markdown fence, noto'g'ri JSON,
menyudan tashqari javob), BOL OCR Tasdiqlash/Bekor qilish oqimi (va `ANTHROPIC_API_KEY`
bo'sh bo'lsa butunlay o'chiq turishi), avto-yo'naltirish Ha/Yo'q oqimi va mos kelmasa
hech narsa qilmasligi.

> Eslatma: bu mashinada Python 3.9 bor edi (loyiha Python 3.11+ talab qiladi). Kod
> `from __future__ import annotations` bilan 3.9'da ham ishlaydi, lekin productionda
> 3.11+ ishlatishni tavsiya qilamiz (Render/Railway/VPS — deploy muhitida odatda yangi
> Python mavjud bo'ladi).

## Qisqacha arxitektura

```
config.py      — bo'limlar ro'yxati (yangi bo'lim = shu yerga 1 qator qo'shish)
models.py      — SQLAlchemy jadvallar
db.py          — async engine (Postgres yoki SQLite, DATABASE_URL orqali)
core.py        — umumiy yordamchi funksiyalar (menyu, state, rol tekshirish, audit log)
i18n.py        — tarjima jadvali (uz/ru/en) + tr()
ai.py          — Claude API: BOL OCR, matn klassifikatsiyasi (ANTHROPIC_API_KEY orqali)
tickets.py     — umumiy ticket mexanizmi (yaratish/yopish/eslatma/bo'limlararo forward/rol)
jobs.py        — davriy vazifalar (eslatmalar, kunlik hisobot, PM, zaxira)
main.py        — Application, handlerlar, job queue, health-check server
webapp.py      — FastAPI o'qish-uchun web panel (alohida process, /dslhelp.webapp)
modules/
  admin.py        — /setgroup, /register, /staffreg, /menu, /auditlog, /til, panel
  dispatch.py     — /BOL (+OCR), /POD, /PU, /DEL, /status, /newload, /loads, lokatsiya
  maintenance.py  — /t, PTI oqimi, truck (/truck /assigntruck /mileage /pmdone), xarajat+/costs
  safety.py       — Incident/Inspection natija oqimi, /csa, /safetystats
  intake.py       — ELD/Safety/Accounting/HR uchun umumiy "matn+rasm+Submit" oqimi
  hr.py           — /doc, /docs, muddat ogohlantirishi, driver onboarding
  accounting.py   — /settlement, Receipt oqimi, /receipts
  autoroute.py    — erkin matndan Claude orqali bo'lim aniqlash + tasdiqlash
  search.py       — /find, /driver
  export.py       — /export (.xlsx)
```

## Qolgan ish

Spec'dagi barcha bo'limlar qo'shildi, faqat bitta aniq cheklov bilan: **ovozli
xabarni matnga aylantirish** Claude API orqali bajarilmaydi (yuqoridagi AI
bo'limida sabab yozilgan). Buni istasangiz, alohida nutq-tanish xizmati (Whisper,
Google Speech-to-Text va h.k.) tanlang — shu bo'yicha qo'shib beramiz.

Shuningdek, bilib turilgan, kichik qolgan ishlar:
- Tarjima jadvali ([i18n.py](dslhelp/i18n.py)) hozir asosiy menyu/registratsiya/BOL
  bilan cheklangan; qolgan modullardagi matnlarni shu jadvalga ko'chirish mexanik ish.
- PTI boshlash/yuborish tugmalari (`maintenance:pti_new`, `pti:send`) hali rol bilan
  cheklanmagan — faqat ticket tugmalari va pul/hujjat buyruqlari cheklangan.
- Web panelda USDELD integratsiyasi yo'q (ma'lumot berilmagan).

Har bosqich alohida so'ralganda, shu loyihaning ustiga qo'shiladi (texnik talablarga
ko'ra, modul tuzilmasi buzilmaydi).
