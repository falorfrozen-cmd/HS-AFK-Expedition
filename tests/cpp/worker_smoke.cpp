// Worker delivery and payment rules (0.7.0), without the game.
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

    std::printf("%d checks, %d failures\n", checks, failed);
    return failed ? 1 : 0;
}
