#include <AfkExpedition/RuntimeState.hpp>
#include <AfkExpedition/Conversion.hpp>
#include <cstdio>
#include <limits>
#include <map>

int main() {
    int failed = 0, checks = 0;
    auto check = [&](bool ok, const char* label) { ++checks; if (!ok) ++failed; std::printf("%s: %s\n", ok ? "ok" : "FAIL", label); };
    using namespace AfkExpedition;
    ResearchArgument argument;
    check(ParseResearchArgument("0",argument) && argument.kind==ResearchArgumentKind::Number && argument.number==0,
          "explicit zero remains a valid research argument");
    check(ParseResearchArgument("-1.25e2",argument) && argument.number==-125,
          "signed and exponent research numbers remain supported");
    check(ParseResearchArgument("me",argument) && argument.kind==ResearchArgumentKind::Player &&
          ParseResearchArgument("self",argument) && argument.kind==ResearchArgumentKind::Self &&
          ParseResearchArgument("undef",argument) && argument.kind==ResearchArgumentKind::Undefined &&
          ParseResearchArgument("'x",argument) && argument.kind==ResearchArgumentKind::Text && argument.text=="x",
          "named handles, undefined and strings have explicit argument kinds");
    bool refused=true;
    for (const char* token:{"", "player", "1oops", "nan", "inf", "1e999", "1e-999", " 1", "1 ", "'"})
        refused &= !ParseResearchArgument(token,argument);
    check(refused,"malformed research arguments cannot silently become zero");
    int ordinal=-1;
    check(ParseResearchIndex("0",ordinal) && ordinal==0 && ParseResearchIndex("260757",ordinal) && ordinal==260757,
          "instance selectors preserve explicit zero and live ids");
    check(!ParseResearchIndex("me",ordinal) && !ParseResearchIndex("nope",ordinal) &&
          !ParseResearchIndex("1.5",ordinal) && !ParseResearchIndex("-1",ordinal) &&
          !ParseResearchIndex("2147483648",ordinal),"invalid selectors cannot target the first instance");
    check(ResearchRoomTravelAllowed("Town_03_rm") && ResearchRoomTravelAllowed("Act_03_03"), "research travel still accepts gameplay rooms");
    check(!ResearchRoomTravelAllowed("Main_Menu_rm") && !ResearchRoomTravelAllowed("Init_rm") && !ResearchRoomTravelAllowed("CHARACTER_MENU_rm"), "research travel refuses menu lifecycle bypass");
    check(LocalSaveSlot({0, 1, 0}) == 1, "duplicate names cannot change selected slot 1");
    check(LocalSaveSlot({9, 0, 9}) == 0, "slot zero is valid");
    check(LocalSaveSlot({}) == -1 && LocalSaveSlot({0}) == -1, "missing slot array rejected");
    check(LocalSaveSlot({0, -1, 0}) == -1 && LocalSaveSlot({0, 2147483648.0, 0}) == -1, "invalid slot bounds rejected");
    check(LocalSaveSlot({0, 20, 0}) == 20, "unlocked slots beyond the old 16-slot search are supported");
    check(LocalSaveSlot({0, 1.5, 0}) == -1 && LocalSaveSlot({0, std::numeric_limits<double>::quiet_NaN()}) == -1, "noninteger slot rejected");
    RewardTotals old; old.items = 900; old.filtered = 261; old.unplaced = 12; old.groundRemoved = 700;
    old.gold = 123.5; old.goldPiles = 21; old.exp = 12345.75; old.expCalls = 70;
    old.expUpdateCalls = 71; old.expUpdateSum = 12345.75; old.goldLogCalls = 21; old.goldLogSum = 123.5;
    std::map<std::string, double> disk;
    old.Fields([&](const char* k, auto& v) { disk[k] = static_cast<double>(v); });
    RewardTotals resumed;
    auto read = [&](const char* k) { return disk.contains(k) ? disk[k] : std::numeric_limits<double>::quiet_NaN(); };
    check(resumed.Load(read), "complete checkpoint loads");
    resumed.filtered += 785; resumed.items += 1316;
    check(resumed.filtered == 1046 && resumed.items == 2216, "abort/resume preserves filter and item totals");
    check(resumed.unplaced == 12 && resumed.groundRemoved == 700 && resumed.goldPiles == 21, "placement and coin counters restored");
    check(resumed.exp == old.exp && resumed.gold == old.gold && resumed.expUpdateCalls == 71 && resumed.goldLogCalls == 21, "reward counters preserve fractional amounts");
    disk.erase("items_filtered");
    check(!resumed.Load(read), "incomplete legacy checkpoint refused");
    check(!CanResume("running", true, true) && !CanResume("paused", true, false) && !CanResume("error", true, true), "unclean interruption needs reconciliation");
    check(CanResume("aborted", true, true) && !CanResume("aborted", false, true) && !CanResume("aborted", true, false), "resume requires same plan and saved rewards");
    check(CanResume("paused", true, true) && !CanResume("paused", false, true), "a saved pause outside the region resumes");
    check(CanResume("running", true, false, true) && !CanResume("running", false, false, true) && !CanResume("error", true, true, true)
          && !CanResume("done", true, true, true), "a running checkpoint continues only after the player accepted its recorded position");
    check(CheckFarmSample(false,true,true,1)==FarmSample::Count,"ordinary farm time is counted");
    check(CheckFarmSample(true,false,true,1)==FarmSample::Pause,"transient room loading pauses the same recording");
    check(CheckFarmSample(false,true,false,40)==FarmSample::Resume,"loading interval is excluded on return");
    check(CheckFarmSample(false,false,false,1)==FarmSample::Invalid,"changed loadout after loading still refuses");
    check(CheckFarmSample(false,true,true,8)==FarmSample::Invalid,"unexplained clock gaps still invalidate");

    // Filtered items: sell below Satanic, Prospector break-down from Satanic up.
    // The recipe table mirrors the shape of the game's unique-equipment recipes.
    auto facts=[](int type,int rarity,int tier,double value,double stack=1,bool corrupted=false){
        ItemFacts f; f.valid=true; f.type=type; f.rarity=rarity; f.tier=tier; f.value=value; f.stack=stack; f.corrupted=corrupted; f.baseId=7; return f; };
    std::vector<ProspectRecipe> recipes;
    const std::vector<int> gear{0,1,2,3,4,5,6,7,8,10,18};
    const long long scf[4]{6,13,20,25};
    for(int tier=0;tier<4;++tier){ ProspectRecipe r; r.types=gear; r.unique=true; r.tier=tier; r.outputs.push_back({14,{60},scf[tier],100}); recipes.push_back(r); }
    { ProspectRecipe r; r.types=gear; r.unique=true; r.tier=4; r.outputs.push_back({14,{72,73},1,100}); recipes.push_back(r); }
    { ProspectRecipe r; r.types=gear; r.unique=true; r.tier=5; r.outputs.push_back({14,{71,72,73},4,100}); recipes.push_back(r); }
    { ProspectRecipe r; r.types=gear; r.rarity=5; r.tier=kAnyTier; r.outputs.push_back({14,{66},1,100}); recipes.push_back(r); }
    check(SellGold(facts(5,2,2,3))==3 && SellGold(facts(12,1,0,2.5,4))==10 && SellGold(facts(3,1,0,7,0))==7 && SellGold(ItemFacts{})==0,
          "a sale pays ceil(value x stack), a missing or zero stack counts as one");
    check(DecideConversion(facts(5,2,2,3),recipes,true,true)==Conversion::Sell && DecideConversion(facts(3,5,1,30),recipes,true,true)==Conversion::Sell,
          "every rarity below Satanic is sold, even one a recipe would take");
    check(DecideConversion(facts(3,6,1,50),recipes,true,true)==Conversion::Prospect && DecideConversion(facts(18,9,3,225),recipes,true,true)==Conversion::Prospect,
          "Satanic and above equipment is broken down");
    check(DecideConversion(facts(15,6,1,50),recipes,true,true)==Conversion::Keep && DecideConversion(facts(3,6,1,50,1,true),recipes,true,true)==Conversion::Keep
          && DecideConversion(facts(3,6,6,50),recipes,true,true)==Conversion::Keep,
          "Satanic and above without a recipe (type, corruption, tier) stays in the records and is never sold");
    check(DecideConversion(facts(5,2,2,3),recipes,false,true)==Conversion::Keep && DecideConversion(facts(3,6,1,50),recipes,true,false)==Conversion::Keep
          && DecideConversion(facts(5,2,2,0),recipes,true,true)==Conversion::Keep && DecideConversion(ItemFacts{},recipes,true,true)==Conversion::Keep,
          "a disabled path, a zero value or unread facts keep the item");
    int type=-1,id=-1; long long amount=0;
    check(ProspectYield(*FindProspectRecipe(facts(3,6,1,50),recipes),50,[](int){return 0;},type,id,amount) && type==14 && id==60 && amount==13,
          "a C tier Satanic item yields 13 Satanic Crystal Fragments");
    check(ProspectYield(*FindProspectRecipe(facts(3,7,5,450),recipes),50,[](int n){return n-1;},type,id,amount) && id==73 && amount==1
          && ProspectYield(*FindProspectRecipe(facts(3,7,5,450),recipes),50,[](int){return 9;},type,id,amount) && id==73,
          "a random pick yields one of the listed fragments, its index clamped, never the table amount");
    { ProspectRecipe r; r.types={3}; r.unique=true; r.outputs.push_back({14,{60},5,25}); std::vector<ProspectRecipe> one{r};
      check(!ProspectYield(one[0],80,[](int){return 0;},type,id,amount) && ProspectYield(one[0],10,[](int){return 0;},type,id,amount) && amount==5, "a chance output hits only below its chance"); }
    check(StackSizes(38154,false)==std::vector<long long>(38,999) && StackSizes(38154,true).back()==192 && StackSizes(998,false).empty()
          && StackSizes(0,true).empty(), "fragments leave as full 999 stacks, the rest at the end");
    check(OutputKey(14,60)=="14:60" && ParseOutputKey("14:60",type,id) && type==14 && id==60 && !ParseOutputKey("14",type,id)
          && !ParseOutputKey("a:1",type,id) && !ParseOutputKey("14:6x",type,id), "pending output keys round-trip and refuse junk");
    std::printf("%d checks, %d failures\n", checks, failed);
    return failed ? 1 : 0;
}
