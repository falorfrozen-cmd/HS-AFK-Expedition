#pragma once
// Filtered items during delivery (0.6.2): an item the player's own loot filter
// hides is either sold for the gold a merchant pays for it (below Satanic) or
// broken down the way the Prospector does it (Satanic and above), when the
// player chose to convert filtered items. Everything here is plain data and
// decisions; the plugin reads the facts from the native item struct, the
// recipe table from the game's global prospect arrays, and performs the credit
// and the item creation through the game's own named routines.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace AfkExpedition {

constexpr int kSatanicRarity = 6;       // itemInfoStruct "27": 6 Satanic, 7 Angelic, 9 Heroic, 10 Unholy
constexpr int kAnyTier = 7;             // tierRequirement meaning "any tier"
constexpr long long kNativeStackMax = 999;

// What the item struct says, read once when the game builds the item.
struct ItemFacts {
    bool valid = false;
    int type = -1;          // itemType
    int rarity = -1;        // itemInfoStruct "27"
    int tier = -1;          // itemInfoStruct "32"
    double value = 0;       // itemInfoStruct "9": what a merchant pays for one unit
    double stack = 1;       // itemDefinitionStruct "o"; missing means 1
    bool corrupted = false; // itemDefinitionStruct "r"
    int baseId = -1;        // itemDefinitionStruct "b"
};

// A merchant pays ceil(value x stack) for an item; nothing else enters the sale.
inline double SellGold(const ItemFacts& f)
{
    if (!f.valid || !std::isfinite(f.value) || f.value <= 0) return 0;
    const double stack = std::isfinite(f.stack) && f.stack > 1 ? f.stack : 1.0;
    return std::ceil(f.value * stack);
}

struct ProspectOutput {
    int type = -1;
    std::vector<int> ids;   // one id, or several to pick one at random
    long long amount = 1;   // a random pick always yields one, as in the game
    double chance = 100;
};
struct ProspectRecipe {
    std::vector<int> types;           // accepted item types
    bool unique = false;              // the input must be Satanic or above
    int rarity = -1;                  // exact rarity for non-unique recipes; -1 when absent
    int tier = kAnyTier;              // required itemInfoStruct "32", or any
    int baseId = -1;                  // required base item, or any
    std::vector<ProspectOutput> outputs;
};

// The Prospector's rule for one input item: the first recipe whose type,
// rarity, tier, base and corruption checks pass.
inline const ProspectRecipe* FindProspectRecipe(const ItemFacts& f, const std::vector<ProspectRecipe>& recipes)
{
    if (!f.valid || f.corrupted) return nullptr;
    for (const auto& r : recipes) {
        if (std::find(r.types.begin(), r.types.end(), f.type) == r.types.end()) continue;
        if (r.unique ? f.rarity < kSatanicRarity : (r.rarity >= 0 && f.rarity != r.rarity)) continue;
        if (r.tier != kAnyTier && f.tier != r.tier) continue;
        if (r.baseId >= 0 && f.baseId != r.baseId) continue;
        if (r.outputs.empty()) continue;
        return &r;
    }
    return nullptr;
}

enum class Conversion { Keep, Sell, Prospect };

// Below Satanic: sell. Satanic and above: break down when a recipe takes it;
// otherwise the item stays in the records (never sold, it may be worth keeping).
inline Conversion DecideConversion(const ItemFacts& f, const std::vector<ProspectRecipe>& recipes, bool sellEnabled, bool prospectEnabled)
{
    if (!f.valid) return Conversion::Keep;
    if (f.rarity < kSatanicRarity) return sellEnabled && SellGold(f) > 0 ? Conversion::Sell : Conversion::Keep;
    return prospectEnabled && FindProspectRecipe(f, recipes) ? Conversion::Prospect : Conversion::Keep;
}

// The output a recipe gives for one unit: the first output whose chance hits
// (``roll`` like the game's irandom(99)), with ``pick(n)`` choosing one of n
// ids (the game's irandom(n - 1)). Returns false when no output hits.
template <class Pick>
inline bool ProspectYield(const ProspectRecipe& r, double roll, Pick pick, int& type, int& id, long long& amount)
{
    for (const auto& o : r.outputs) {
        if (o.ids.empty()) continue;
        if (!(o.chance >= 100 || roll < o.chance)) continue;
        type = o.type;
        if (o.ids.size() == 1) { id = o.ids[0]; amount = std::max<long long>(1, o.amount); }
        else {
            const int n = static_cast<int>(o.ids.size());
            id = o.ids[static_cast<size_t>(std::clamp(static_cast<int>(pick(n)), 0, n - 1))]; amount = 1;
        }
        return true;
    }
    return false;
}

// Output fragments are gathered per kind and created as full native stacks;
// the remainder waits in the checkpoint until the delivery ends.
inline std::vector<long long> StackSizes(long long total, bool includePartial)
{
    std::vector<long long> sizes;
    while (total >= kNativeStackMax) { sizes.push_back(kNativeStackMax); total -= kNativeStackMax; }
    if (includePartial && total > 0) sizes.push_back(total);
    return sizes;
}

inline std::string OutputKey(int type, int id) { return std::to_string(type) + ":" + std::to_string(id); }
inline bool ParseOutputKey(const std::string& key, int& type, int& id)
{
    const auto colon = key.find(':');
    if (colon == std::string::npos || colon == 0 || colon + 1 >= key.size()) return false;
    try {
        size_t a = 0, b = 0;
        type = std::stoi(key.substr(0, colon), &a); id = std::stoi(key.substr(colon + 1), &b);
        return a == colon && b == key.size() - colon - 1 && type >= 0 && id >= 0;
    } catch (...) { return false; }
}

}  // namespace AfkExpedition
