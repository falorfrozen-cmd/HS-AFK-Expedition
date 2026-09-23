"""Independent reward policy. No game calls, ForgePact writes, or item creation."""
from copy import deepcopy
import math
import json


def load(path):
    if not path.exists():
        return normalize()
    try:
        return normalize(json.loads(path.read_bytes()))
    except (OSError, ValueError) as error:
        raise ValueError('Saved reward settings are invalid; choose and save valid modifiers') from error

FIELDS = (
    ('magic_find', 'Magic Find', 'Character Magic Find × this multiplier.'),
    ('experience', 'Experience', 'Native kill XP × this multiplier.'),
    ('gold', 'Gold', 'More native gold drops. Scales the average amount.'),
    ('dungeon', 'Dungeon Keys', 'Scales the native roll; opens missing dungeon-key rolls.'),
    ('angelic', 'Angelic Keys', 'Scales the native roll; opens missing angelic-key rolls.'),
    ('chaos', 'Chaos + Crystal Keys', 'Scales their existing native drop rolls.'),
    ('bifrost', 'Bifrost Key', 'Scales its existing native drop roll.'),
    ('relic', 'Relics', 'Improves native relic rolls and enables occasional extra relic attempts.'),
    ('rune', 'Runes', 'Only where the game already rolls runes.'),
    ('stone', 'Gems', 'Chipped through flawless; does not enable perfect-gem drops.'),
    ('bossgem', 'Boss Gems', 'Only where the game already rolls boss gems.'),
    ('orb', 'Orbs', 'Only where the game already rolls orbs.'),
    ('scrollofra', 'Scrolls of Ra', 'Only where the game already rolls Scrolls of Ra.'),
    ('dimshard', 'Dimensional Shards', 'Only where the game already rolls shards.'),
    ('battlefrag', 'Battle Fragments', 'Scales existing drop rolls; does not add event completion rewards.'),
    ('colosfrag', 'Colosseum Fragments', 'Only where the game already rolls fragments.'),
    ('ruby', 'Ruby Keys', 'Scales its existing native drop roll.'),
)
KEYS = tuple(row[0] for row in FIELDS)


def normalize(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(KEYS) - {'schema'}:
        raise ValueError('Unknown reward setting')
    if type(value.get('schema', 1)) is not int or value.get('schema', 1) != 1:
        raise ValueError('Unsupported reward settings version')
    result = {'schema': 1}
    for key in KEYS:
        v = value.get(key, 1)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 1 <= v <= 100:
            raise ValueError(f'{key}: choose a multiplier between 1 and 100')
        result[key] = float(v)
    return result


def pace_inputs(context):
    """Ignore reward-only settings, retain all unknown/combat inputs conservatively."""
    inputs = deepcopy((context or {}).get('inputs'))
    if not isinstance(inputs, dict):
        return None
    fp = inputs.get('forgepact')
    if not isinstance(fp, dict):
        return None
    for key in ('drops', 'keys', 'angelic_items', 'mod_filter_max_relics'):
        fp.pop(key, None)
    stats = fp.get('stats', {})
    if isinstance(stats, dict):
        for key in ('exp', 'magicfind', 'expgain', 'extragold'):
            stats.pop(key, None)
        if not stats:
            fp.pop('stats', None)
    return inputs


def context_matches(expected, actual, independent=False):
    if independent:
        a, b = pace_inputs(expected), pace_inputs(actual)
        return a is not None and b is not None and a == b
    return bool(expected and actual and expected.get('hash') and expected.get('hash') == actual.get('hash'))


def apply_to_plan(plan, profile, settings):
    """Freeze settings and normalized XP; never infer a baseline from a config file."""
    baseline = profile.get('reward_baseline') or {}
    if baseline.get('schema') != 1 or not baseline.get('complete'):
        raise ValueError('This profile needs a new calibration for independent rewards')
    mf = baseline.get('magic_find')
    if isinstance(mf, bool) or not isinstance(mf, (int, float)) or not math.isfinite(mf) or mf < 0:
        raise ValueError('Native Magic Find baseline is missing; recalibrate')
    normalized = normalize(settings)
    by_hash = {p['hash']: p for p in profile['packets']}
    for packet in plan['packets']:
        base = by_hash[packet['hash']].get('native_exp')
        if isinstance(base, bool) or not isinstance(base, (int, float)) or not math.isfinite(base) or base < 0:
            raise ValueError('Native XP baseline is missing; recalibrate')
        if base * normalized['experience'] >= 1e9:
            raise ValueError('Experience multiplier exceeds the supported per-kill XP limit; lower it')
        packet['exp'] = base * normalized['experience']
        packet['native_exp'] = base
    plan['reward_modifiers'] = normalized
    plan['reward_baseline'] = deepcopy(baseline)
    # Loot measured under a different policy is not an honest forecast.
    plan['estimate_rates'] = {'items_per_call': {}, 'gold_per_call': None}
    plan['preview']['items_estimate'] = None
    plan['preview']['gold_estimate'] = None
    plan['preview']['exp'] = int(sum(p['exp'] * p['count'] for p in plan['packets']))
    plan['effective_magic_find'] = mf * normalized['magic_find']
    return plan
