#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <string_view>
namespace AfkExpedition {
inline constexpr std::array<std::string_view,17> RewardKeys={"magic_find","experience","gold","dungeon","angelic","chaos","bifrost","relic","rune","stone","bossgem","orb","scrollofra","dimshard","battlefrag","colosfrag","ruby"};
struct RewardPolicy {
    bool enabled=false;
    std::array<double,17> values{};
    double nativeMagicFind=0;
    RewardPolicy(){values.fill(1);}
    double get(std::string_view key)const{for(size_t i=0;i<RewardKeys.size();++i)if(RewardKeys[i]==key)return values[i];throw std::runtime_error("unknown reward key");}
    static bool Valid(double value){return std::isfinite(value) && value>=1 && value<=100;}
};
inline double RelicAttempt(double multiplier){return multiplier<=1 ? 0 : (std::min)(1.0, .00025*multiplier*multiplier);}
inline double DropBase(double base,double multiplier){return multiplier<=1?base:(std::max)(1.0,base/multiplier);}
inline bool RewardNameMatches(std::string_view name,std::string_view part){
    if(part.ends_with('$'))return name.ends_with(part.substr(0,part.size()-1));
    return name.find(part)!=std::string_view::npos;
}
}
