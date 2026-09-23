#pragma once
#include <cmath>
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <string>
#include <type_traits>
#include <vector>
#include <cstdlib>
#include <cerrno>

namespace AfkExpedition {
enum class FarmSample { Pause, Resume, Count, Invalid };
inline FarmSample CheckFarmSample(bool loading, bool contextMatches, bool previousReady, double elapsed) {
    if (loading) return FarmSample::Pause;
    if (!contextMatches) return FarmSample::Invalid;
    if (!previousReady) return FarmSample::Resume;
    if (!std::isfinite(elapsed) || elapsed <= 0 || elapsed > 5) return FarmSample::Invalid;
    return FarmSample::Count;
}
enum class ResearchArgumentKind { Number, Undefined, Text, Self, Player };
struct ResearchArgument {
    ResearchArgumentKind kind=ResearchArgumentKind::Undefined;
    double number=0;
    std::string text;
};
inline bool ParseResearchArgument(const std::string& token, ResearchArgument& output) {
    ResearchArgument next;
    if (token=="undef") next.kind=ResearchArgumentKind::Undefined;
    else if (token=="self") next.kind=ResearchArgumentKind::Self;
    else if (token=="me") next.kind=ResearchArgumentKind::Player;
    else if (token.size()>1 && token[0]=='\'') {
        next.kind=ResearchArgumentKind::Text; next.text=token.substr(1);
    } else {
        if (token.empty() || std::any_of(token.begin(),token.end(),[](unsigned char c){return std::isspace(c);})) return false;
        char* end=nullptr; errno=0;
        const double value=std::strtod(token.c_str(),&end);
        if (end==token.c_str() || !end || *end || errno==ERANGE || !std::isfinite(value)) return false;
        next.kind=ResearchArgumentKind::Number; next.number=value;
    }
    output=std::move(next); return true;
}
inline bool ParseResearchIndex(const std::string& token, int& output) {
    ResearchArgument value;
    if (!ParseResearchArgument(token,value) || value.kind!=ResearchArgumentKind::Number ||
        value.number<0 || value.number>2147483647.0 || std::floor(value.number)!=value.number) return false;
    output=static_cast<int>(value.number); return true;
}
// RoomGoto is a zone-travel route, not the game's quit/menu lifecycle.
inline bool ResearchRoomTravelAllowed(std::string room) {
    std::transform(room.begin(), room.end(), room.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return !room.empty() && room.find("menu") == std::string::npos
        && room != "init_rm" && room.find("loading") == std::string::npos;
}
// Local play uses global.slot[1], measured by UI selection (2026-09-18).
inline int LocalSaveSlot(const std::vector<double>& slots) {
    if (slots.size() <= 1 || !std::isfinite(slots[1]) || slots[1] < 0 || slots[1] >= 2147483648.0 || std::floor(slots[1]) != slots[1]) return -1;
    return static_cast<int>(slots[1]);
}

struct RewardTotals {
    uint64_t items = 0, filtered = 0, unplaced = 0, groundRemoved = 0, goldPiles = 0, expCalls = 0;
    uint64_t expUpdateCalls = 0, goldLogCalls = 0;
    double gold = 0, exp = 0, expUpdateSum = 0, goldLogSum = 0;
    template<class F> void Fields(F fn) {
        fn("items", items); fn("items_filtered", filtered); fn("items_unplaced", unplaced);
        fn("ground_removed", groundRemoved); fn("gold_piles", goldPiles); fn("exp_calls", expCalls);
        fn("gold", gold); fn("exp", exp); fn("exp_update_calls", expUpdateCalls);
        fn("exp_update_sum", expUpdateSum); fn("gold_log_calls", goldLogCalls); fn("gold_log_sum", goldLogSum);
    }
    template<class Read> bool Load(Read read) {
        RewardTotals next; bool valid = true;
        next.Fields([&](const char* key, auto& value) {
            const double v = read(key);
            if (!std::isfinite(v) || v < 0 || v > 9007199254740991.0) { valid = false; return; }
            using T = std::remove_reference_t<decltype(value)>;
            if constexpr (std::is_integral_v<T>) { if (std::floor(v) != v) { valid = false; return; } }
            value = static_cast<T>(v);
        });
        if (valid) *this = next;
        return valid;
    }
};

// "aborted": a Pause/abort or a closed game window saved and stopped delivery.
// "paused" counts only when it carries a save: the hero left the region and
// the plugin saved before waiting. A running or unsaved checkpoint may hide
// rewards the save does not contain, so it still needs reconciliation.
inline bool CanResume(const std::string& state, bool samePlan, bool saved) {
    return (state == "aborted" || state == "paused") && samePlan && saved;
}
}
