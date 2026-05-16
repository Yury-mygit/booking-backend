"""Seed 5 fictional hotels (~10 rooms each) with Unsplash photos.

Run inside container:
    docker cp scripts/seed_fictional.py booking_dev_app:/app/scripts/
    docker exec booking_dev_app python scripts/seed_fictional.py

Idempotent: re-running skips already-seeded hotels (matched by name_en) and only
re-downloads missing photo files.

Photo sources: Unsplash (free for commercial use, no attribution required).
Coordinates: anchored on real hotel-district points from OSM Overpass.
"""
import asyncio
import urllib.request
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import (
    Hotel,
    HotelService,
    HotelStatus,
    Lang,
    Room,
    User,
    UserRole,
)
from app.utils import gen_unique_hotel_slug

STORAGE = Path(settings.storage_path)
UNSPLASH = "https://images.unsplash.com/photo-{id}?w=1200&q=80&auto=format&fit=crop"
UA = "Mozilla/5.0 (booking-seed)"


def download(photo_id: str, dest: Path) -> str:
    """Download an Unsplash photo by ID. Returns the filename used."""
    fname = f"{photo_id}.jpg"
    out = dest / fname
    if out.exists() and out.stat().st_size > 1024:
        return fname
    dest.mkdir(parents=True, exist_ok=True)
    url = UNSPLASH.format(id=photo_id)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        out.write_bytes(resp.read())
    return fname


# ─── Static config ────────────────────────────────────────────────────────────

PARTNERS = [
    {"telegram_id": 999100, "first_name": "Aibek (test)", "company": "Ala-Too Hospitality LLC"},
    {"telegram_id": 999101, "first_name": "Gulnara (test)", "company": "Tien-Shan Stays LLC"},
]

# Coordinates anchored on real OSM hotel-district points (offset slightly so they
# don't sit exactly on top of any real hotel).
HOTELS = [
    {
        "key": "ala-too-boutique-stay",
        "owner_tg": 999100,
        "city": "Bishkek",
        "address_ru": "ул. Эркиндик, 12",
        "address_en": "12 Erkindik Blvd",
        "lat": 42.8746,
        "lng": 74.6068,
        "name_ru": "Ала-Тоо Бутик-Стэй",
        "name_ky": "Ала-Тоо Бутик-Стай",
        "name_en": "Ala-Too Boutique Stay",
        "desc_ru": "Камерный бутик-отель в центре Бишкека, в шаге от площади Ала-Тоо и Дубового парка. Авторские интерьеры, завтрак во внутреннем дворе, прокат велосипедов.",
        "desc_ky": "Бишкектин борборундагы, Ала-Тоо аянты менен Эмен паркынан бир кадам алыс жайгашкан кичинекей бутик-мейманкана. Авторлук интерьерлер, эртең мененки тамак ички короодо, велосипед ижара берилет.",
        "desc_en": "Cosy boutique hotel in central Bishkek, steps from Ala-Too Square and Oak Park. Designer interiors, breakfast in the courtyard, complimentary bike rental.",
        "exterior": ["1551016043-06ec2173531b", "1607320895054-c5c543e9a069"],
        "lobby": "1625244724120-1fd1d34d00f6",
    },
    {
        "key": "manas-garden-hotel",
        "owner_tg": 999100,
        "city": "Bishkek",
        "address_ru": "пр. Манаса, 78",
        "address_en": "78 Manas Ave",
        "lat": 42.8754,
        "lng": 74.6248,
        "name_ru": "Манас Гарден Отель",
        "name_ky": "Манас Гарден Мейманкана",
        "name_en": "Manas Garden Hotel",
        "desc_ru": "Семейный отель в зелёной зоне на проспекте Манаса. Просторные номера, ресторан кыргызской кухни, большая летняя терраса с видом на горы Ала-Арча.",
        "desc_ky": "Манас проспектиндеги жашыл зонада жайгашкан үй-бүлөлүк мейманкана. Кенен бөлмөлөр, кыргыз тамак-аш ресторан, Ала-Арча тоолоруна көрүнүш бере турган чоң жайкы террасса.",
        "desc_en": "Family-friendly hotel in a leafy stretch of Manas Avenue. Spacious rooms, a restaurant serving Kyrgyz cuisine, and a large summer terrace overlooking the Ala-Archa range.",
        "exterior": ["1668480441891-3744c25337a3", "1536269404660-0a8d4e88bf1b"],
        "lobby": "1495365200479-c4ed1d35e1aa",
    },
    {
        "key": "karakol-alpine-lodge",
        "owner_tg": 999101,
        "city": "Karakol",
        "address_ru": "ул. Гагарина, 24",
        "address_en": "24 Gagarin St",
        "lat": 42.4897,
        "lng": 78.3912,
        "name_ru": "Каракол Альпийский Лодж",
        "name_ky": "Каракол Альпы Лодж",
        "name_en": "Karakol Alpine Lodge",
        "desc_ru": "База для горных походов в нескольких минутах от центра Каракола. Сушилка для снаряжения, гид-сервис, сауна после трекинга, домашний завтрак.",
        "desc_ky": "Каракол борборунан бир нече мүнөттө жайгашкан тоо жортуулдары үчүн база. Жабдууларды кургатуу бөлмөсү, гид кызматы, жортуулдан кийинки сауна, үй эртең мененки.",
        "desc_en": "Base camp for mountain trekking, minutes from central Karakol. Gear drying room, guide service, post-hike sauna, home-cooked breakfast.",
        "exterior": ["1611892440504-42a792e24d32", "1607320879139-1bb689f6a68f"],
        "lobby": "1573052905904-34ad8c27f0cc",
    },
    {
        "key": "issyk-kul-pearl-resort",
        "owner_tg": 999101,
        "city": "Cholpon-Ata",
        "address_ru": "Прибрежная ул., 5",
        "address_en": "5 Pribrezhnaya St",
        "lat": 42.6489,
        "lng": 77.0825,
        "name_ru": "Жемчужина Иссык-Куля",
        "name_ky": "Ысык-Көлдүн Берметинин Резорту",
        "name_en": "Issyk-Kul Pearl Resort",
        "desc_ru": "Курортный комплекс на берегу Иссык-Куля. Свой пляж, бассейн с подогревом, прокат катамаранов, детская площадка. Открыт круглый год.",
        "desc_ky": "Ысык-Көлдүн жээгиндеги курорттук комплекс. Өзүнүн жээги, ысытылуучу бассейн, катамарандарды ижарага берүү, балдар аянтчасы. Жыл бою иштейт.",
        "desc_en": "Lakeside resort on the shore of Issyk-Kul. Private beach, heated pool, catamaran rental, kids' playground. Open year-round.",
        "exterior": ["1472510771109-39b92752a6b9", "1570206986634-afd7cccb68d3"],
        "lobby": "1660557989695-14fac79c086d",
    },
    {
        "key": "sulayman-stone-inn",
        "owner_tg": 999101,
        "city": "Osh",
        "address_ru": "ул. Курманжан Датки, 14",
        "address_en": "14 Kurmanjan Datka St",
        "lat": 40.5283,
        "lng": 72.7985,
        "name_ru": "Сулайман Стоун Инн",
        "name_ky": "Сулайман Стоун Инн",
        "name_en": "Sulayman Stone Inn",
        "desc_ru": "Уютная гостиница у подножия горы Сулайман-Тоо в Оше. Восточный завтрак, чайхана во дворе, экскурсии по Великому шёлковому пути.",
        "desc_ky": "Оштогу Сулайман-Тоо тоосунун этегиндеги жайлуу мейманкана. Чыгыш эртең мененки, короодо чайкана, Улуу Жибек жолу боюнча экскурсиялар.",
        "desc_en": "Cosy inn at the foot of Sulayman-Too mountain in Osh. Eastern-style breakfast, a chaikhana in the courtyard, Silk Road excursions.",
        "exterior": ["1652348716053-3447e551dd1f", "1693146842813-be42935ecdbd"],
        "lobby": "1611048267451-e6ed903d4a38",
    },
]

# Room photo pools, used round-robin per hotel.
ROOM_PHOTOS = {
    "single": ["1551882547-ff40c63fe5fa", "1582719478250-c89cae4dc85b"],
    "double": ["1611892440504-42a792e24d32", "1631049307264-da0ec9d70304"],
    "twin":   ["1603072388139-565853396b38", "1603072387986-d6136328c664"],
    "triple": ["1629140727571-9b5c6f6267b4", "1583847268964-b28dc8f51f92"],
    "family": ["1578683010236-d716f9a3f461", "1568495248636-6432b97bd949"],
    "deluxe": ["1618773928121-c32242e63f39", "1496417263034-38ec4f0b665a"],
    "junior": ["1631049552057-403cdb8f0658", "1711059985570-4c32ed12a12c"],
    "suite":  ["1576354302919-96748cb8299e", "1621293954908-907159247fc8"],
    "penthouse": ["1566665797739-1674de7a421a", "1590490360182-c33d57733427"],
    "bath": ["1507652313519-d4e9174996dd", "1564540583246-934409427776",
             "1587527901949-ab0341697c1e", "1664917555352-f3f66e57ccc2"],
}

# Per-hotel room template. Prices vary by hotel "tier".
def room_template(tier_mult: float) -> list[dict]:
    M = tier_mult
    return [
        {"kind": "single", "name_ru": "Одноместный стандарт", "name_ky": "Бир кишилик стандарт",
         "name_en": "Single Standard", "capacity": 1, "beds": 1, "price": int(2200 * M), "floor": 1,
         "desc_ru": "Уютный номер на одного с двуспальной кроватью и письменным столом.",
         "desc_ky": "Бир кишилик жайлуу бөлмө, эки кишилик керебет жана жазуу үстөл менен.",
         "desc_en": "Cosy single room with a double bed and a writing desk."},
        {"kind": "double", "name_ru": "Двухместный стандарт", "name_ky": "Эки кишилик стандарт",
         "name_en": "Double Standard", "capacity": 2, "beds": 1, "price": int(3000 * M), "floor": 1,
         "desc_ru": "Стандартный двухместный номер с двуспальной кроватью.",
         "desc_ky": "Эки кишилик керебеттүү стандарттык бөлмө.",
         "desc_en": "Standard double room with one queen-size bed."},
        {"kind": "twin", "name_ru": "Твин (две кровати)", "name_ky": "Твин (эки керебет)",
         "name_en": "Twin Room", "capacity": 2, "beds": 2, "price": int(3000 * M), "floor": 2,
         "desc_ru": "Номер с двумя раздельными кроватями.",
         "desc_ky": "Эки өзүнчө керебеттүү бөлмө.",
         "desc_en": "Room with two separate single beds."},
        {"kind": "triple", "name_ru": "Трёхместный номер", "name_ky": "Үч кишилик бөлмө",
         "name_en": "Triple Room", "capacity": 3, "beds": 3, "price": int(4500 * M), "floor": 2,
         "desc_ru": "Просторный номер на троих с тремя односпальными кроватями.",
         "desc_ky": "Үч бир кишилик керебеттүү кенен бөлмө.",
         "desc_en": "Spacious triple room with three single beds."},
        {"kind": "family", "name_ru": "Семейный номер", "name_ky": "Үй-бүлөлүк бөлмө",
         "name_en": "Family Room", "capacity": 4, "beds": 2, "price": int(6000 * M), "floor": 2,
         "desc_ru": "Семейный номер с двумя зонами: спальня для родителей и комната для детей.",
         "desc_ky": "Эки бөлүктүү үй-бүлөлүк бөлмө: ата-эне үчүн уктоочу жана балдар бөлмөсү.",
         "desc_en": "Family suite split into two areas: a parents' bedroom and a kids' room."},
        {"kind": "deluxe", "name_ru": "Делюкс двухместный", "name_ky": "Делюкс эки кишилик",
         "name_en": "Deluxe Double", "capacity": 2, "beds": 1, "price": int(5000 * M), "floor": 3,
         "desc_ru": "Улучшенный двухместный номер с видом во двор и большой ванной.",
         "desc_ky": "Жакшыртылган эки кишилик бөлмө, короого караган көрүнүш жана чоң ваннасы менен.",
         "desc_en": "Upgraded double room with a courtyard view and a large bathroom."},
        {"kind": "junior", "name_ru": "Джуниор-сюит", "name_ky": "Джуниор-сюит",
         "name_en": "Junior Suite", "capacity": 2, "beds": 1, "price": int(7000 * M), "floor": 3,
         "desc_ru": "Полулюкс с зоной гостиной, кофемашиной и халатами.",
         "desc_ky": "Конок бөлмөсү, кофе-машина жана халаттары бар жарым-люкс.",
         "desc_en": "Junior suite with a sitting area, espresso machine and bathrobes."},
        {"kind": "suite", "name_ru": "Люкс", "name_ky": "Люкс",
         "name_en": "Suite", "capacity": 3, "beds": 1, "price": int(10000 * M), "floor": 4,
         "desc_ru": "Просторный люкс с отдельной гостиной и панорамными окнами.",
         "desc_ky": "Өзүнчө конок бөлмөсү жана панорамалык терезелери бар кенен люкс.",
         "desc_en": "Spacious suite with a separate living room and panoramic windows."},
        {"kind": "penthouse", "name_ru": "Пентхаус", "name_ky": "Пентхаус",
         "name_en": "Penthouse", "capacity": 4, "beds": 2, "price": int(16000 * M), "floor": 5,
         "desc_ru": "Двухкомнатный пентхаус на верхнем этаже с террасой.",
         "desc_ky": "Үстүңкү кабатта террасасы бар эки бөлмөлүү пентхаус.",
         "desc_en": "Two-bedroom penthouse on the top floor with a private terrace."},
        {"kind": "double", "name_ru": "Двухместный комфорт", "name_ky": "Эки кишилик комфорт",
         "name_en": "Comfort Double", "capacity": 2, "beds": 1, "price": int(3600 * M), "floor": 2,
         "desc_ru": "Двухместный номер увеличенной площади с рабочим столом.",
         "desc_ky": "Жазуу үстөлү бар чоңойтулган аянттагы эки кишилик бөлмө.",
         "desc_en": "Larger double room with a desk and reading chair."},
    ]


HOTEL_TIER_MULT = {
    "ala-too-boutique-stay": 1.10,    # бутик в центре
    "manas-garden-hotel": 1.00,       # mid-range
    "karakol-alpine-lodge": 0.85,     # дешевле, дальше от Бишкека
    "issyk-kul-pearl-resort": 1.30,   # курорт
    "sulayman-stone-inn": 0.90,
}


SERVICES = {
    "ala-too-boutique-stay": [
        ("Завтрак", "Эртең мененки тамак", "Breakfast", 400),
        ("Трансфер из аэропорта", "Аэропорттон трансфер", "Airport transfer", 1500),
        ("Аренда велосипеда", "Велосипед ижарасы", "Bike rental", None),
    ],
    "manas-garden-hotel": [
        ("Завтрак", "Эртең мененки тамак", "Breakfast", 350),
        ("Парковка", "Унаа токтотуучу жай", "Parking", None),
        ("Прачечная", "Кир жуугуч", "Laundry (per item)", 200),
    ],
    "karakol-alpine-lodge": [
        ("Завтрак", "Эртең мененки тамак", "Breakfast", 300),
        ("Гид по треккингу", "Жортуул гиди", "Trekking guide (per day)", 2500),
        ("Сауна", "Сауна", "Sauna (per hour)", 800),
    ],
    "issyk-kul-pearl-resort": [
        ("Полный пансион", "Толук пансион", "Full board", 1800),
        ("Прокат катамарана", "Катамаран ижарасы", "Catamaran rental (per hour)", 1200),
        ("Детская анимация", "Балдар үчүн анимация", "Kids' activities", None),
    ],
    "sulayman-stone-inn": [
        ("Восточный завтрак", "Чыгыш эртең мененки", "Eastern breakfast", 350),
        ("Экскурсия Шёлковый путь", "Жибек жолу экскурсиясы", "Silk Road tour", 3500),
        ("Парковка", "Унаа токтотуучу жай", "Parking", None),
    ],
}


# ─── Main ─────────────────────────────────────────────────────────────────────


async def upsert_partner(db, spec: dict) -> User:
    u = (
        await db.execute(select(User).where(User.telegram_id == spec["telegram_id"]))
    ).scalar_one_or_none()
    if u is None:
        u = User(
            telegram_id=spec["telegram_id"],
            role=UserRole.partner,
            lang=Lang.ru,
            first_name=spec["first_name"],
        )
        db.add(u)
        await db.flush()
    return u


async def upsert_hotel(db, h_spec: dict, owner_id: int) -> tuple[Hotel, bool]:
    h = (
        await db.execute(select(Hotel).where(Hotel.name_en == h_spec["name_en"]))
    ).scalar_one_or_none()
    if h is not None:
        return h, False

    h = Hotel(
        slug="__pending__",
        owner_user_id=owner_id,
        name_ru=h_spec["name_ru"],
        name_ky=h_spec["name_ky"],
        name_en=h_spec["name_en"],
        description_ru=h_spec["desc_ru"],
        description_ky=h_spec["desc_ky"],
        description_en=h_spec["desc_en"],
        city=h_spec["city"],
        address=h_spec["address_en"],
        lat=h_spec["lat"],
        lng=h_spec["lng"],
        photos=[],
        status=HotelStatus.published,
    )
    db.add(h)
    await db.flush()
    h.slug = await gen_unique_hotel_slug(db, h.name_en, h.id, exclude_id=h.id)
    return h, True


def fetch_hotel_photos(h_spec: dict, hotel_id: int) -> list[str]:
    dest = STORAGE / "hotels" / str(hotel_id)
    urls = []
    for pid in h_spec["exterior"]:
        fname = download(pid, dest)
        urls.append(f"/api/v1/photos/hotels/{hotel_id}/{fname}")
    fname = download(h_spec["lobby"], dest)
    urls.append(f"/api/v1/photos/hotels/{hotel_id}/{fname}")
    return urls


def fetch_room_photos(kind: str, room_id: int, with_bath: bool) -> list[str]:
    dest = STORAGE / "rooms" / str(room_id)
    urls = []
    for pid in ROOM_PHOTOS[kind]:
        fname = download(pid, dest)
        urls.append(f"/api/v1/photos/rooms/{room_id}/{fname}")
    if with_bath:
        pid = ROOM_PHOTOS["bath"][room_id % len(ROOM_PHOTOS["bath"])]
        fname = download(pid, dest)
        urls.append(f"/api/v1/photos/rooms/{room_id}/{fname}")
    return urls


async def upsert_rooms(db, hotel: Hotel, tier: float) -> list[Room]:
    existing = (
        await db.execute(select(Room).where(Room.hotel_id == hotel.id))
    ).scalars().all()
    if existing:
        return existing

    rooms = []
    for r_spec in room_template(tier):
        r = Room(
            hotel_id=hotel.id,
            name_ru=r_spec["name_ru"],
            name_ky=r_spec["name_ky"],
            name_en=r_spec["name_en"],
            description_ru=r_spec["desc_ru"],
            description_ky=r_spec["desc_ky"],
            description_en=r_spec["desc_en"],
            capacity=r_spec["capacity"],
            beds=r_spec["beds"],
            price_kgs=r_spec["price"],
            floor=r_spec["floor"],
            photos=[],
        )
        db.add(r)
        rooms.append(r)
    await db.flush()
    return rooms


async def upsert_services(db, hotel: Hotel, key: str) -> None:
    existing = (
        await db.execute(select(HotelService).where(HotelService.hotel_id == hotel.id))
    ).scalars().all()
    if existing:
        return
    for name_ru, name_ky, name_en, price in SERVICES[key]:
        db.add(HotelService(
            hotel_id=hotel.id,
            name_ru=name_ru, name_ky=name_ky, name_en=name_en,
            price_kgs=price,
        ))


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # partners
        partners_by_tg = {}
        for p in PARTNERS:
            u = await upsert_partner(db, p)
            partners_by_tg[p["telegram_id"]] = u

        # hotels + rooms + services
        for h_spec in HOTELS:
            owner = partners_by_tg[h_spec["owner_tg"]]
            hotel, created = await upsert_hotel(db, h_spec, owner.id)
            tier = HOTEL_TIER_MULT[h_spec["key"]]
            rooms = await upsert_rooms(db, hotel, tier)
            await upsert_services(db, hotel, h_spec["key"])

            # photos: download outside of any transaction tightness; we'll commit
            # at the end. If a hotel/room already had photos, leave them.
            if not hotel.photos:
                hotel.photos = fetch_hotel_photos(h_spec, hotel.id)
            for r_spec, room in zip(room_template(tier), rooms):
                if room.photos:
                    continue
                kind = r_spec["kind"]
                with_bath = kind in {"deluxe", "junior", "suite", "penthouse", "family"}
                room.photos = fetch_room_photos(kind, room.id, with_bath)

            mark = "+" if created else "="
            print(f"  {mark} {hotel.slug:30s} id={hotel.id:3d}  rooms={len(rooms):2d}  photos={len(hotel.photos)}")

        await db.commit()
        print("done")


if __name__ == "__main__":
    asyncio.run(main())
