"""Native itemInfoStruct[27] names confirmed by the Tracker producer.

Do not confuse this field with item definition fields or other rarity enums.
Unknown native tiers retain their number instead of receiving a guessed name.
"""
import math
RARITIES = {4:'Set',6:'Satanic',7:'Angelic',9:'Heroic',10:'Unholy'}


def rarity_name(value):
    if type(value) in (int,float) and math.isfinite(value) and value==int(value):
        return RARITIES.get(int(value), f'Tier {int(value)}')
    return 'Unknown'
