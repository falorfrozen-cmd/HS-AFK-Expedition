#include <AfkExpedition/RuntimeState.hpp>
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
    std::printf("%d checks, %d failures\n", checks, failed);
    return failed ? 1 : 0;
}
