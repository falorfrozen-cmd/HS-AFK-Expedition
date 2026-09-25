"""The town's goods (0.9): the game items the town can hold, build with and trade.

Every good is a stackable game item keyed ``"type:id"`` as the Item Editor's
catalog names it: keys (12), shards and cards (13), materials (14) and
socketables (15). The town never makes one itself:
- **In:** a good enters the camp's stock from the Vault (the Item Editor's
  afk-take route, one receipt per request) or from a worker's haul.
- **Out:** a good leaves for the Vault only through the plugin, which makes it
  with the game's own ground-drop routine (a delivery like a worker's), and
  only goods on this list.

``value`` is AFK FARM's own price of one unit in gold: the anchor every market
moves around. The game's vendors buy these items for a token 10-250 gold and
sell none of them (STATIC 2026-09-25: no merchant stock routine lists keys,
fragments, materials or socketables), so the anchors follow how rarely each
drops and what it does, and are AFK FARM's own layer.

Standard library only.
"""
from __future__ import annotations

import re

_KEY = re.compile(r'(\d{1,3}):(\d{1,3})')
CATEGORIES = ('ore', 'material', 'dust', 'relic', 'key', 'shard', 'tarot', 'rune', 'gem', 'jewel', 'orb')
CATEGORY_NAMES = dict(ore='Ores', material='Jewelcrafting materials', dust='Dusts', relic='Rare consumables', key='Keys',
                      shard='Fragments and shards', tarot='Tarot cards', rune='Runes', gem='Gems', jewel='Jewels', orb='Orbs')


def _build() -> dict:
    g = {}

    def add(key, name, category, value):
        g[key] = (name, category, int(value))

    for i, (name, value) in enumerate((('Copper Ore', 60), ('Iron Ore', 130), ('Gold Ore', 260), ('Ruby Ore', 480), ('Jade Ore', 800),
                                       ('Tarethium Ore', 1_400))):
        add(f'14:{27 + i}', name, 'ore', value)
    materials = ('Bloodstone', 'Snake Tooth', 'Copperstone', 'Liquate', 'Darkstone', 'Lesser Impstone', 'Iron Opal', 'Shadow Stone',
                 'Twilight Citrine', 'Ocean Agate', 'Greater Impstone', 'Huge Spessarite', 'Cardinal Ruby', 'Flaming Core', 'Demon Tooth',
                 'Ketamineral', 'Jadenium Powder', 'Satans Nail', 'Inferno Stone', 'Hellstar', 'Tarethium Core', 'Dark Matter',
                 'Demon Soulstone', 'Storm Opal')
    for i, name in enumerate(materials):
        add(f'14:{i}', name, 'material', (400, 900, 2_000, 4_500, 9_000, 18_000)[i // 4])
    add('14:44', 'Enchanted Sigil', 'material', 40_000)
    for i, name in enumerate(('Minor Satanic Dust', 'Lesser Satanic Dust', 'Satanic Dust', 'Rich Satanic Dust', 'Empowered Satanic Dust',
                              'Purified Satanic Dust')):
        add(f'14:{33 + i}', name, 'dust', 1_000 * 2 ** i)
    add('14:39', 'Angelic Dust', 'dust', 50_000)
    for i, name in enumerate(('Lesser Unstable Dust', 'Unstable Dust', 'Greater Unstable Dust')):
        add(f'14:{49 + i}', name, 'dust', (500, 1_500, 4_000)[i])
    for i, name in enumerate(('Minor Mythic Dust', 'Lesser Mythic Dust', 'Mythic Dust', 'Empowered Mythic Dust', 'Rich Mythic Dust')):
        add(f'14:{53 + i}', name, 'dust', (800, 1_600, 3_200, 8_000, 20_000)[i])
    for key, name, value in (('14:43', 'Satanic Dice', 90_000), ('14:58', 'Satanic Crystal', 150_000), ('14:60', 'Satanic Crystal Fragment', 300),
                             ('14:61', 'Codex Page', 20_000), ('14:62', "Prophet's Wisdom", 70_000), ('14:63', "Gypsy's Prophecy", 120_000),
                             ('14:64', 'Destiny Shard', 45_000), ('14:65', "Blacksmith's Mallet", 80_000), ('14:66', 'Destiny Shard Fragment', 2_500),
                             ('14:68', 'Essence of Chaos', 60_000), ('14:69', 'Infernal Codex Page', 50_000), ('14:70', "Angel's Wisdom", 150_000)):
        add(key, name, 'relic', value)
    for key, name, value in (('12:0', 'Basic Key', 1_000), ('12:1', 'Crystal Key', 4_000), ('12:2', 'Bifröst Key', 30_000),
                             ('12:7', 'Ruby Key', 250_000), ('12:8', 'Angelic Key', 60_000), ('12:33', 'Chaos Key', 15_000),
                             ('12:28', 'Helflame Torch', 40_000)):
        add(key, name, 'key', value)
    dungeon = {9: 'Smelly Cheese', 10: 'Cellar Key', 11: 'Tower Key', 12: 'Frosted Key', 13: 'Copper Key', 14: 'Mystic Key',
               15: 'Rusted Key', 16: 'Shovel Key', 17: 'Ancient Key', 18: 'Tomb Key', 19: "Devil's Key", 21: 'Battle Key',
               22: 'Garden Key', 23: 'Golden Key', 24: 'Axe Key', 25: 'Valor Key', 26: 'Naga Scale Key', 27: 'Magma Key',
               29: 'Warp Key', 30: 'Storage Key'}
    for i, name in dungeon.items():
        add(f'12:{i}', name, 'key', 10_000)
    for key, name, value in (('13:0', 'Battle Fragment', 1_500), ('13:1', 'Dimensional Shard', 2_500), ('13:18', 'Colosseum Fragment', 3_000),
                             ('13:41', 'Infernal Battle Fragment', 6_000), ('13:42', 'Infernal Dimensional Shard', 10_000)):
        add(key, name, 'shard', value)
    tarot = ('Death', 'The Tower', 'The Fool', 'The Magician', 'The World', 'The Wheel of Fortune', 'The High Priestess', 'The Empress',
             'The Emperor', 'The Chariot', 'The Lovers', 'Justice', 'The Hermit', 'Temperance', 'The Devil', 'The Moon', 'The Sun',
             'The Star', 'Judgement', 'Strength', 'The Hanged Man', 'The Hierophant')
    for i, name in enumerate(tarot):
        add(f'13:{19 + i}', name, 'tarot', 8_000)
    add('13:54', 'The Divine Sun', 'tarot', 150_000)
    add('13:55', 'The Divine Moon', 'tarot', 150_000)
    runes = ('Ol', 'Old', 'Tor', 'Naf', 'Eth', 'Uth', 'Tul', 'Rex', 'Ert', 'Thal', 'Ymn', 'Sal', 'Nut', 'Del', 'Hel', 'Io', 'Lum', 'Co',
             'Fel', 'Lem', 'Pul', 'Um', 'Mal', 'Ist', 'Gul', 'Vex', 'Qi', 'Xo', 'Sur', 'Ber', 'Jah', 'Drax', 'Zed')
    for i, name in enumerate(runes):
        add(f'15:{1 + i}', f'{name} Rune', 'rune', round(500 * 1000 ** (i / 32), -1))
    grades = ('Chipped', 'Flawed', '', 'Flawless', 'Perfect', 'Pristine')
    for base, colour in ((34, 'Amethyst'), (40, 'Emerald'), (46, 'Ruby'), (52, 'Sapphire'), (58, 'Skull'), (64, 'Topaz'), (130, 'Diamond')):
        for i, grade in enumerate(grades):
            add(f'15:{base + i}', f'{grade} {colour}'.strip(), 'gem', (300, 900, 2_500, 7_000, 20_000, 60_000)[i])
    jewels = {78: 'Angelic Gem', 79: 'Chaos Gem', 80: 'Elemental Gem', 81: 'Moonstone Gem', 82: 'Exan Jewel', 83: 'Wilrden Jewel',
              84: 'Volcon Jewel', 85: 'Aether Jewel', 86: 'Helmon Jewel', 87: 'Mariane Jewel', 88: 'Lyrcon Jewel', 89: 'Fieryzen Jewel',
              90: 'Lapis-Lazuli Jewel', 91: 'Omnipearl Jewel', 92: 'Agathetheum Jewel', 93: 'Tramal Jewel', 94: 'Pearlescento Jewel',
              95: 'Mythgonlion Jewel', 96: 'Clean Cut Jewel'}
    for i, name in jewels.items():
        add(f'15:{i}', name, 'jewel', 40_000 if i <= 81 else 25_000 + 2_500 * (i - 82))
    orbs = ('Goblin', 'Runeforge', 'Kobold', 'Heroism', 'Angel', 'Swiftness', 'Agility', 'Magister', 'Brute', 'Wisdom', 'Relic',
            'Treasure', 'Midas', 'Gladiator', 'Earth', 'Doom', 'Fatality', 'Ancient')
    for i, name in enumerate(orbs):
        add(f'15:{112 + i}', f'Orb of {name}', 'orb', 30_000)
    return g


# key: (name, category, value in gold)
GOODS = _build()


def parse(key) -> tuple[int, int] | None:
    match = _KEY.fullmatch(str(key))
    return (int(match.group(1)), int(match.group(2))) if match else None


def known(key) -> bool:
    return str(key) in GOODS


def name(key) -> str:
    row = GOODS.get(str(key))
    return row[0] if row else str(key)


def category(key) -> str | None:
    row = GOODS.get(str(key))
    return row[1] if row else None


def value(key) -> int:
    row = GOODS.get(str(key))
    if not row:
        raise ValueError(f'Unknown good {key}.')
    return row[2]


def of(category_: str) -> list[str]:
    return [k for k, (_, cat, _) in GOODS.items() if cat == category_]


def describe(items: dict) -> str:
    return ', '.join(f'{n:,} {name(k)}' for k, n in sorted(items.items(), key=lambda kv: (name(kv[0]), kv[0])))


def catalog() -> list[dict]:
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return [dict(key=k, name=v[0], category=v[1], value=v[2])
            for k, v in sorted(GOODS.items(), key=lambda kv: (order[kv[1][1]], kv[1][2], kv[0]))]
