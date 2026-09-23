#include <AfkExpedition/RewardPolicy.hpp>
#include <hs_game_sdk/reward_scope.hpp>
#include <cassert>
#include <iostream>
#include <thread>
int main(){
    using namespace HeroSiege::RewardScope;
    using namespace AfkExpedition;
    RewardPolicy p;assert(p.get("experience")==1);assert(p.get("magic_find")==1);
    assert(!RewardPolicy::Valid(0));assert(!RewardPolicy::Valid(NAN));assert(!RewardPolicy::Valid(INFINITY));
    assert(DropBase(100,1)==100);assert(DropBase(100,2)==50);assert(DropBase(2,100)==1);
    assert(RelicAttempt(1)==0);assert(RelicAttempt(2)==.001);assert(RelicAttempt(100)==1);
    assert(RewardNameMatches("socketable_fire_rune","_rune$"));
    assert(!RewardNameMatches("socketable_orb_of_runeforge","_rune$"));
    assert(!Active());RegisterForgePact();SetForgePactXp(4);
    try {
        Guard outer;assert(Active());
        {Guard inner;assert(Active());}
        assert(Active());bool other=true;
        std::thread t([&]{other=Active();});t.join();assert(!other);
        throw std::runtime_error("test unwind");
    }catch(const std::runtime_error&){}
    assert(!Active());assert(Get()->forgePactXp==4);
    PublishBase(16,155,100);assert(Get()->nativeDropBase[16*256+155]==100);
    PublishBase(20,0,999);assert(Get()->forgePactXp==4);
    std::cout<<"reward defaults, bounds, family matching, nested scope, thread isolation and exception restoration passed\n";
}
