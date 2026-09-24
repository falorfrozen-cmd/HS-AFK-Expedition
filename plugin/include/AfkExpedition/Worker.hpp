#pragma once
// Workers (0.7.0): the plain rules the plugin applies when it delivers a
// worker's haul or takes a hire payment. The panel decides the haul (tools/
// workers.py); the plugin creates it through the game's own ground-drop
// routine and rolls the Gem Sense share with the game's Prospector table and
// dice. Everything here is data and decisions, testable without the game.
#include <AfkExpedition/Conversion.hpp>
#include <cctype>
#include <string>

namespace AfkExpedition {

constexpr int kMaterialType = 14;                   // SDK ItemType::Material
constexpr long long kMaxWorkerAmount = 999LL * 500;  // per material kind in one delivery
constexpr double kMaxWorkerPayment = 500000000.0;    // the game's gold cap

// What a worker delivery may create: mining ores (27-32), the jewelcrafting
// materials the Prospector turns them into (0-23), and the finds of the
// Prospecting branch: Satanic Crystal (58), its fragment (60), Destiny Shard
// Fragment (66). Anything else in a delivery file is refused.
inline bool WorkerMaterial(int type, int id)
{
    if (type != kMaterialType) return false;
    return (id >= 0 && id <= 23) || (id >= 27 && id <= 32) || id == 58 || id == 60 || id == 66;
}
inline bool WorkerOre(int id) { return id >= 27 && id <= 32; }

// The Prospector walks a recipe's outputs and rolls irandom(99) for each one
// that is not certain; the first hit wins (STATIC 2026-09-24, the prospect
// button's loop calls the random routine inside the walk). Ore recipes list
// four outputs at 1, 7, 15 and 25 (Copper) or 1, 5, 12 and 20: a Copper Ore
// unit gives a material about 41% of the time.
template <class Roll, class Pick>
inline bool ProspectYieldEach(const ProspectRecipe& r, Roll roll, Pick pick, int& type, int& id, long long& amount)
{
    for (const auto& o : r.outputs) {
        if (o.ids.empty()) continue;
        if (!(o.chance >= 100 || roll() < o.chance)) continue;
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

// The ore recipe for one material base id: a non-unique recipe taking exactly
// that material.
inline const ProspectRecipe* FindOreRecipe(int oreId, const std::vector<ProspectRecipe>& recipes)
{
    for (const auto& r : recipes) {
        if (r.unique || r.baseId != oreId || r.outputs.empty()) continue;
        if (std::find(r.types.begin(), r.types.end(), kMaterialType) == r.types.end()) continue;
        return &r;
    }
    return nullptr;
}

// Request and delivery identifiers become file names: letters, digits, _ and -.
inline bool SafeIdentifier(const std::string& text, size_t minimum = 1, size_t maximum = 120)
{
    if (text.size() < minimum || text.size() > maximum) return false;
    for (unsigned char c : text) if (!(std::isalnum(c) || c == '_' || c == '-')) return false;
    return true;
}

// A hire or skill-reset price: a whole number of gold within the game's cap.
inline bool ValidPayment(double amount)
{
    return std::isfinite(amount) && amount >= 1 && amount <= kMaxWorkerPayment && std::floor(amount) == amount;
}

// The Jeweler (0.8): the game's jewelcrafting recipes (the craft cube's table,
// result types 37-41) make socketables 15:78-96 (jewels, gems) from the jewel
// materials 14:0-23 and the Enchanted Sigil 14:44. The panel plans crafts from
// the recipes the plugin read live; the plugin checks every recipe again, reads
// it from the game once more and creates only its output.
constexpr int kSocketableType = 15;
inline bool JewelRecipeType(int resultType) { return resultType >= 37 && resultType <= 41; }
inline bool JewelOutput(int type, int id) { return type == kSocketableType && id >= 78 && id <= 96; }
inline bool JewelInput(int type, int id) { return type == kMaterialType && ((id >= 0 && id <= 23) || id == 44); }
struct JewelPart { int type = -1; int id = -1; long long amount = 0; };
struct JewelRecipe { int index = -1; int resultType = -1; JewelPart output; std::vector<JewelPart> inputs; };
// A recipe the Jeweler may use: a jewel result, a socketable output and jewel
// inputs only, every amount a whole number within a native stack.
inline bool UsableJewelRecipe(const JewelRecipe& r)
{
    if (!JewelRecipeType(r.resultType) || !JewelOutput(r.output.type, r.output.id)) return false;
    if (r.output.amount < 1 || r.output.amount > kNativeStackMax || r.inputs.empty()) return false;
    for (const auto& p : r.inputs)
        if (!JewelInput(p.type, p.id) || p.amount < 1 || p.amount > kNativeStackMax) return false;
    return true;
}

}  // namespace AfkExpedition
