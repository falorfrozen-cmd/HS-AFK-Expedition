// Worker delivery, payment and credit rules (0.7.0 - 0.9), without the game.
#include <AfkExpedition/Worker.hpp>
#include <cstdio>
#include <limits>
#include <map>

int main() {
    int failed = 0, checks = 0;
    auto check = [&](bool ok, const char* label) { ++checks; if (!ok) ++failed; std::printf("%s: %s\n", ok ? "ok" : "FAIL", label); };
    using namespace AfkExpedition;

    check(WorkerMaterial(14, 27) && WorkerMaterial(14, 32) && WorkerMaterial(14, 0) && WorkerMaterial(14, 23)
          && WorkerMaterial(14, 58) && WorkerMaterial(14, 60) && WorkerMaterial(14, 66), "ores, jewel materials and the prospecting finds may be delivered");
    check(!WorkerMaterial(14, 43) && !WorkerMaterial(14, 24) && !WorkerMaterial(14, 67) && !WorkerMaterial(6, 27) && !WorkerMaterial(14, -1),
          "dice, framings, other materials and other item types are refused");
    check(WorkerOre(27) && WorkerOre(32) && !WorkerOre(26) && !WorkerOre(33), "only the six mining ores are prospected");

    ProspectRecipe copper; copper.types = {14}; copper.baseId = 27;
    for (auto [id, chance] : std::map<int, double>{{0, 1}, {1, 7}, {2, 15}, {3, 25}}) { ProspectOutput o; o.type = 14; o.ids = {id}; o.chance = chance; copper.outputs.push_back(o); }
    std::sort(copper.outputs.begin(), copper.outputs.end(), [](const ProspectOutput& a, const ProspectOutput& b) { return a.chance < b.chance; });
    ProspectRecipe gear; gear.types = {1, 2, 3}; gear.unique = true; gear.outputs = copper.outputs;
    std::vector<ProspectRecipe> recipes = {gear, copper};
    check(FindOreRecipe(27, recipes) == &recipes[1] && FindOreRecipe(28, recipes) == nullptr, "the ore recipe is the non-unique one taking that ore");

    int type = -1, id = -1; long long amount = 0;
    std::vector<double> rolls = {50, 50, 3, 99};   // misses 1% and 7%, hits 15% with 3
    size_t at = 0;
    check(ProspectYieldEach(copper, [&] { return rolls[at++]; }, [](int) { return 0; }, type, id, amount) && id == 2 && amount == 1 && at == 3,
          "every output rolls on its own and the first hit wins");
    at = 0; rolls = {99, 99, 99, 99};
    check(!ProspectYieldEach(copper, [&] { return rolls[at++]; }, [](int) { return 0; }, type, id, amount) && at == 4, "no hit gives nothing");

    // Every roll a fresh irandom(99): the share of units that give a material.
    long long got = 0, units = 0; unsigned state = 12345;
    auto irandom99 = [&] { state = state * 1103515245u + 12345u; return static_cast<double>((state >> 8) % 100); };
    std::map<int, long long> by;
    for (int u = 0; u < 200000; ++u, ++units)
        if (ProspectYieldEach(copper, irandom99, [](int) { return 0; }, type, id, amount)) { ++got; ++by[id]; }
    const double share = static_cast<double>(got) / units;
    check(share > 0.39 && share < 0.43, "about 41% of Copper Ore units give a jewel material");
    check(by[0] < by[1] && by[1] < by[2] && by[2] < by[3], "rarer materials come less often");

    check(SafeIdentifier("worker_ab12cd34_20260924_120000") && SafeIdentifier("a1b2c3d4", 8, 64), "delivery and request ids are file-name safe");
    check(!SafeIdentifier("..\\x") && !SafeIdentifier("a b") && !SafeIdentifier("short", 8, 64) && !SafeIdentifier(""), "path tricks and short ids are refused");
    check(ValidPayment(250000) && ValidPayment(1) && ValidPayment(500000000), "whole gold amounts within the cap pay");
    check(!ValidPayment(0) && !ValidPayment(-5) && !ValidPayment(1.5) && !ValidPayment(500000001) && !ValidPayment(std::numeric_limits<double>::infinity()),
          "zero, negative, fractional, over-cap and infinite prices are refused");
    check(StackSizes(2000, true) == std::vector<long long>({999, 999, 2}), "a haul leaves in native 999 stacks");

    // The Jeweler (0.8): the game's jewel recipes only, jewels and gems only.
    JewelRecipe exan; exan.index = 7; exan.resultType = 37; exan.output = {15, 82, 1};
    exan.inputs = {{14, 3, 3}, {14, 2, 2}};
    check(UsableJewelRecipe(exan), "a jewel recipe from jewel materials is usable");
    JewelRecipe gem = exan; gem.resultType = 41; gem.output = {15, 78, 1}; gem.inputs.push_back({14, 44, 1});
    check(UsableJewelRecipe(gem), "a gem recipe may take the Enchanted Sigil");
    JewelRecipe other = exan; other.resultType = 36;
    JewelRecipe armour = exan; armour.output = {6, 82, 1};
    JewelRecipe dice = exan; dice.inputs = {{14, 43, 1}};
    JewelRecipe broken = exan; broken.inputs = {{14, 3, 0}};
    JewelRecipe empty = exan; empty.inputs.clear();
    check(!UsableJewelRecipe(other) && !UsableJewelRecipe(armour) && !UsableJewelRecipe(dice) && !UsableJewelRecipe(broken) && !UsableJewelRecipe(empty),
          "other recipe types, gear outputs, dice inputs, zero amounts and recipes without inputs are refused");
    check(JewelOutput(15, 96) && !JewelOutput(15, 97) && !JewelOutput(15, 77) && JewelInput(14, 23) && !JewelInput(14, 24),
          "uncut jewels and other socketables are not recipe outputs; only jewel materials and the sigil are inputs");

    // The town (0.9): a town delivery may make exactly the goods the town trades
    // (TownGoods.hpp, generated from tools/goods.py); every other delivery keeps
    // the materials rule.
    const std::map<int, std::vector<std::pair<int, int>>> townList = {
        {12, {{0, 2}, {7, 19}, {21, 30}, {33, 33}}},
        {13, {{0, 1}, {18, 42}, {54, 55}}},
        {14, {{0, 23}, {27, 39}, {43, 44}, {49, 51}, {53, 58}, {60, 66}, {68, 70}}},
        {15, {{1, 69}, {78, 96}, {112, 135}}},
    };
    auto listed = [&](int t, int i) {
        const auto row = townList.find(t);
        if (row == townList.end()) return false;
        for (const auto& [first, last] : row->second) if (i >= first && i <= last) return true;
        return false;
    };
    int differ = 0, townGoods = 0, materialsOutside = 0, ruleMixed = 0;
    for (int t = -2; t <= 40; ++t)
        for (int i = -2; i <= 400; ++i) {
            if (TownGood(t, i) != listed(t, i)) ++differ;
            if (TownGood(t, i)) ++townGoods;
            if (WorkerMaterial(t, i) && !TownGood(t, i)) ++materialsOutside;
            if (DeliverableItem(false, t, i) != WorkerMaterial(t, i) || DeliverableItem(true, t, i) != TownGood(t, i)) ++ruleMixed;
        }
    check(differ == 0 && townGoods == 226 && townGoods == kTownGoodCount,
          "the town trades exactly 226 goods: 12:0-2,7-19,21-30,33; 13:0-1,18-42,54-55; 14:0-23,27-39,43-44,49-51,53-58,60-66,68-70; 15:1-69,78-96,112-135");
    check(materialsOutside == 0, "every material a worker delivers is also a town good");
    check(ruleMixed == 0, "a town delivery checks TownGood, every other delivery WorkerMaterial");
    check(TownGood(12, 0) && TownGood(12, 2) && !TownGood(12, 3) && !TownGood(12, 6) && TownGood(12, 7) && TownGood(12, 19) && !TownGood(12, 20)
          && TownGood(12, 21) && TownGood(12, 30) && !TownGood(12, 31) && !TownGood(12, 32) && TownGood(12, 33) && !TownGood(12, 34),
          "key edges: 2|3, 6|7, 19|20|21, 30|31, 32|33|34");
    check(TownGood(13, 0) && TownGood(13, 1) && !TownGood(13, 2) && !TownGood(13, 17) && TownGood(13, 18) && TownGood(13, 42) && !TownGood(13, 43)
          && !TownGood(13, 53) && TownGood(13, 54) && TownGood(13, 55) && !TownGood(13, 56) && !TownGood(13, 58) && !TownGood(13, 64),
          "fragment and card edges; the single items 13:58-64 are never town goods");
    check(TownGood(14, 0) && TownGood(14, 23) && !TownGood(14, 24) && !TownGood(14, 26) && TownGood(14, 27) && TownGood(14, 39) && !TownGood(14, 40)
          && !TownGood(14, 42) && TownGood(14, 43) && TownGood(14, 44) && !TownGood(14, 45) && !TownGood(14, 48) && TownGood(14, 49) && TownGood(14, 51)
          && !TownGood(14, 52) && TownGood(14, 53) && TownGood(14, 58) && !TownGood(14, 59) && TownGood(14, 60) && TownGood(14, 66) && !TownGood(14, 67)
          && TownGood(14, 68) && TownGood(14, 70) && !TownGood(14, 71), "material edges; the single items 14:59 and 14:67 are never town goods");
    check(!TownGood(15, 0) && TownGood(15, 1) && TownGood(15, 69) && !TownGood(15, 70) && !TownGood(15, 77) && TownGood(15, 78) && TownGood(15, 96)
          && !TownGood(15, 97) && !TownGood(15, 111) && TownGood(15, 112) && TownGood(15, 135) && !TownGood(15, 136), "socketable edges: 0|1, 69|70, 77|78, 96|97, 111|112, 135|136");
    check(!TownGood(11, 0) && !TownGood(16, 1) && !TownGood(6, 27) && !TownGood(0, 0) && !TownGood(-1, 0) && !TownGood(12, -1) && !TownGood(14, -1),
          "other item types and negative ids are never town goods");
    check(TownDelivery("worker_town_ab12cd34_20260925_120000") && TownDelivery("worker_town_") && !TownDelivery("worker_ab12cd34_20260924_120000")
          && !TownDelivery("worker_townhall_1") && !TownDelivery("Worker_town_1") && !TownDelivery("x_worker_town_1") && !TownDelivery(""),
          "only an id starting with worker_town_ is a town delivery");
    check(DeliverableItem(true, 12, 0) && DeliverableItem(true, 13, 19) && DeliverableItem(true, 15, 5) && DeliverableItem(true, 14, 43)
          && DeliverableItem(true, 14, 27) && DeliverableItem(false, 14, 27) && !DeliverableItem(false, 12, 0) && !DeliverableItem(false, 13, 19)
          && !DeliverableItem(false, 15, 5) && !DeliverableItem(false, 14, 43) && !DeliverableItem(true, 14, 67) && !DeliverableItem(true, 6, 27),
          "keys, cards, runes and dice only in a town delivery; ores in both; single items and gear in neither");

    // worker credit (0.9): whole gold within the cap, never past the cap, and
    // the gold must rise by exactly the amount.
    const double notNumber = std::numeric_limits<double>::quiet_NaN(), infinite = std::numeric_limits<double>::infinity();
    check(ValidCredit(1) && ValidCredit(250000) && ValidCredit(500000000), "a credit is whole gold from 1 to the cap");
    check(!ValidCredit(0) && !ValidCredit(-1) && !ValidCredit(2.5) && !ValidCredit(500000001) && !ValidCredit(notNumber) && !ValidCredit(infinite),
          "zero, negative, fractional, over-cap, NaN and infinite credits are refused");
    check(CreditWithinCap(0, 500000000) && CreditWithinCap(499999999, 1) && CreditWithinCap(250000000, 250000000) && CreditWithinCap(1234, 5678),
          "a credit may fill the hero's gold up to the cap exactly");
    check(!CreditWithinCap(499999999, 2) && !CreditWithinCap(500000000, 1) && !CreditWithinCap(700000000, 1) && !CreditWithinCap(1, 500000000),
          "a credit that would pass the cap is refused");
    check(!CreditWithinCap(-1, 5) && !CreditWithinCap(notNumber, 5) && !CreditWithinCap(infinite, 5) && !CreditWithinCap(100, 0) && !CreditWithinCap(100, 0.5),
          "an unreadable balance or an invalid amount is refused");
    check(GoldRoseBy(1000, 1250, 250) && GoldRoseBy(1000, 1250.5, 250) && GoldRoseBy(1000, 1249.5, 250) && GoldRoseBy(0, 500000000, 500000000),
          "the gold rose by exactly the amount, within half a unit");
    check(!GoldRoseBy(1000, 1000, 250) && !GoldRoseBy(1000, 1251, 250) && !GoldRoseBy(1000, 1248, 250) && !GoldRoseBy(1000, 1250.6, 250)
          && !GoldRoseBy(1000, 750, 250), "no rise, a different rise or a fall is not the credit");
    check(!GoldRoseBy(-1, 249, 250) && !GoldRoseBy(1000, -1, 250) && !GoldRoseBy(notNumber, 1250, 250) && !GoldRoseBy(1000, notNumber, 250)
          && !GoldRoseBy(1000, 1250, notNumber), "an unreadable amount before or after never confirms a credit");
    check(GoldRoseBy(1000, 1000, 0) && GoldRoseBy(1000, 1000.4, 0) && !GoldRoseBy(1000, 1250, 0) && !GoldRoseBy(1000, 999, 0),
          "a take-back is confirmed only when the gold is back where it was");

    std::printf("%d checks, %d failures\n", checks, failed);
    return failed ? 1 : 0;
}
