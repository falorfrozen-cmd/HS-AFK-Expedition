// Native rewards with an explicitly scoped policy. No item synthesis or
// permanent ForgePact/configuration changes. Included after StructGet.
namespace IndependentRewards {
using AfkExpedition::RewardPolicy;
static const RewardPolicy* active=nullptr;
static bool extraRelic=false;
static int goldDepth=0;
static PFUNC_YYGMLScript originalMf=nullptr, originalXp=nullptr, originalLoad=nullptr;
static PFUNC_YYGMLScript originalGold=nullptr,originalMonsterGold=nullptr,originalBossParts=nullptr,originalUberParts=nullptr;
static std::string mfKind,xpKind,loadKind,goldKind,monsterGoldKind,bossKind,uberKind;
static bool restorationFailed=false;
static uint64_t mfCalls=0;
static double mfNativeComponent=0,mfScaledComponent=0;

static bool Compatible(){
    const auto* s=HeroSiege::RewardScope::Get();
    return !restorationFailed && s && (!ForgePactLoaded() || s->forgePactReady==1);
}
static double Scalar(const RValue& value){
    RValue first=value;
    if(value.m_Kind==VALUE_ARRAY)first=g_Yytk->CallBuiltin("array_get",{value,RValue(0.0)});
    if(!IsNumberKind(first) || !std::isfinite(first.ToDouble()))throw std::runtime_error("native stat is not a finite number");
    return first.ToDouble();
}
static RValue ReplaceFirst(const RValue& value,double number){
    if(value.m_Kind!=VALUE_ARRAY)return RValue(number);
    int n=int(g_Yytk->CallBuiltin("array_length",{value}).ToDouble());
    if(n<1 || n>64)throw std::runtime_error("unexpected stat array");
    RValue copy=g_Yytk->CallBuiltin("array_create",{RValue(double(n))});
    for(int i=0;i<n;++i)g_Yytk->CallBuiltin("array_set",{copy,RValue(double(i)),i?g_Yytk->CallBuiltin("array_get",{value,RValue(double(i))}):RValue(number)});
    return copy;
}
static RValue& MagicFind(CInstance* s,CInstance* o,RValue& r,int argc,RValue** args){
    RValue& result=originalMf(s,o,r,argc,args);
    ++mfCalls;
    if(!HeroSiege::RewardScope::Active())return result;
    mfNativeComponent=Scalar(result);mfScaledComponent=mfNativeComponent;
    // Scale the native component once. ReturnSpecificStat still applies its
    // normal postprocessing; feeding its already-final total back here doubles it.
    if(active){mfScaledComponent=mfNativeComponent*active->get("magic_find");result=ReplaceFirst(result,mfScaledComponent);}
    return result;
}
static RValue& Experience(CInstance* s,CInstance* o,RValue& r,int argc,RValue** args){
    RValue& result=originalXp(s,o,r,argc,args);
    if(s && !HeroSiege::RewardScope::Active()){
        try {
            double native=-1;
            if(Compatible()){
                if(ForgePactLoaded()){
                    // This is the spawn-time XP calculation, not an XP grant.
                    // Query once with reward hooks neutralized so both direct
                    // XP and percentage XP-stat bonuses are removed exactly.
                    HeroSiege::RewardScope::Guard scope;RValue unscaled;
                    native=Scalar(originalXp(s,o,unscaled,argc,args));
                }else native=Scalar(result);
            }
            g_Yytk->CallBuiltin("variable_instance_set",{s->ToRValue(),RValue("__afk_native_xp_v1"),RValue(native)});
        }catch(...){} // Missing evidence invalidates calibration, never a real kill.
    }
    return result;
}
static double Uniform(){return g_Yytk->CallBuiltin("random",{RValue(1.0)}).ToDouble();}
static RValue& GoldCall(PFUNC_YYGMLScript fn,CInstance* s,CInstance* o,RValue& r,int argc,RValue** args){
    struct Depth{Depth(){++goldDepth;}~Depth(){--goldDepth;}} depth;
    const double mult=active && goldDepth==1?active->get("gold"):1;
    int calls=int(mult)+(mult>int(mult) && Uniform()<mult-int(mult)?1:0);
    for(int i=1;i<calls;++i){RValue discard;fn(s,o,discard,argc,args);}
    return fn(s,o,r,argc,args);
}
static RValue& Gold(CInstance* s,CInstance* o,RValue& r,int n,RValue** a){return GoldCall(originalGold,s,o,r,n,a);}
static RValue& MonsterGold(CInstance* s,CInstance* o,RValue& r,int n,RValue** a){return GoldCall(originalMonsterGold,s,o,r,n,a);}
static RValue& BossParts(CInstance* s,CInstance* o,RValue& r,int n,RValue** a){return extraRelic?r:originalBossParts(s,o,r,n,a);}
static RValue& UberParts(CInstance* s,CInstance* o,RValue& r,int n,RValue** a){return extraRelic?r:originalUberParts(s,o,r,n,a);}
static RValue& Load(CInstance* s,CInstance* o,RValue& r,int n,RValue** a){
    RValue& result=originalLoad(s,o,r,n,a);
    if(!active || n<9 || !a || !a[2] || !a[8] || a[2]->ToDouble()!=11)return result;
    RValue chances=*a[8];
    if(chances.m_Kind!=VALUE_ARRAY || g_Yytk->CallBuiltin("array_length",{chances}).ToDouble()<=41)throw std::runtime_error("reward gate array changed");
    const double normal=g_Yytk->CallBuiltin("array_get",{chances,RValue(11.0)}).ToDouble();
    for(const auto& [key,type]:std::vector<std::pair<const char*,int>>{{"dungeon",12},{"angelic",16},{"relic",41}}){
        double mult=active->get(key);
        if(mult<=1 || normal<=0)continue;
        RValue before=g_Yytk->CallBuiltin("array_get",{chances,RValue(double(type))});
        if(before.ToDouble()>0 || (type==41 && Uniform()>=AfkExpedition::RelicAttempt(mult)))continue;
        struct Restore {RValue array,value;int type;~Restore(){extraRelic=false;try{g_Yytk->CallBuiltin("array_set",{array,RValue(double(type)),value});}catch(...){restorationFailed=true;Out("ERROR: native reward-gate restoration failed; restart required");}}} restore{chances,before,type};
        g_Yytk->CallBuiltin("array_set",{chances,RValue(double(type)),RValue(normal*(type==41?mult:1))});
        std::vector<RValue*> args(a,a+n);RValue newType{double(type)};args[2]=&newType;
        extraRelic=type==41;RValue discard;originalLoad(s,o,discard,n,args.data());
    }
    return result;
}
static bool Install(){
    return InstallScriptDetour("StatMagicFind","afk_reward_mf",MagicFind,&originalMf,mfKind)
        && InstallScriptDetour("EnemyCalculateExperience","afk_reward_xp",Experience,&originalXp,xpKind)
        && InstallScriptDetour("LoadDrops","afk_reward_gates",Load,&originalLoad,loadKind)
        && InstallScriptDetour("DropGold","afk_reward_gold",Gold,&originalGold,goldKind)
        && InstallScriptDetour("DropMonsterGold","afk_reward_monstergold",MonsterGold,&originalMonsterGold,monsterGoldKind)
        && InstallScriptDetour("DropBossParts","afk_reward_bossparts",BossParts,&originalBossParts,bossKind)
        && InstallScriptDetour("DropUberParts","afk_reward_uberparts",UberParts,&originalUberParts,uberKind);
}
static double NativeMagicFind(){
    if(!Compatible())throw std::runtime_error("Update ForgePact's compatibility plugin or launch without ForgePact");
    // A Player instance exists before its protected stats and equipment finish
    // loading. Do not invoke a stat routine until the full context is ready.
    if(FarmContextJson()=="null")throw std::runtime_error("Character stats are still loading");
    RValue obj;if(!ObjectIndex("Player_obj",obj))throw std::runtime_error("player missing");
    auto* player=ResolveInstance(g_Yytk->CallBuiltin("instance_find",{obj,RValue(0.0)}));
    if(!player)throw std::runtime_error("player missing");
    HeroSiege::RewardScope::Guard guard;RValue value;const auto before=mfCalls;
    // ReturnSpecificStat prepares StatMagicFind's full native argument list.
    // Its query ID differs from the inventory item's Magic Find stat key.
    if(!AurieSuccess(g_Yytk->CallGameScriptEx(value,HeroSiege::Scripts::gml_Script_ReturnSpecificStat.data(),player,player,
            {RValue(1.0),RValue(double(HeroSiege::RewardStatQuery::MagicFind)),RValue(0.0),RValue(),RValue()})))
        throw std::runtime_error("native Magic Find unavailable");
    if(mfCalls==before)throw std::runtime_error("Magic Find query did not reach its native stat hook");
    const double mf=Scalar(value);if(mf<0)throw std::runtime_error("invalid native Magic Find");return mf;
}
static std::string Baseline(){
    try {if(!Install())throw std::runtime_error("independent reward hooks unavailable");
        return "{\"schema\":1,\"available\":true,\"magic_find\":"+std::to_string(NativeMagicFind())+"}";
    }catch(const std::exception& e){return "{\"schema\":1,\"available\":false,\"error\":\""+JsonEscape(e.what())+"\"}";}
    catch(...){return "{\"schema\":1,\"available\":false,\"error\":\"Native Magic Find could not be read\"}";}
}
static std::string NativeKillXp(const RValue& instance){
    try {
        RValue value=GetVar(instance,"__afk_native_xp_v1");
        if(!IsNumberKind(value))return "null";
        const double xp=value.ToDouble();
        return std::isfinite(xp) && xp>=0?std::to_string(xp):"null";
    }catch(...){return "null";}
}
static RewardPolicy Parse(const RValue& plan){
    RewardPolicy policy;RValue value=StructGet(plan,"reward_modifiers");
    if(value.m_Kind!=VALUE_OBJECT)return policy;
    if(Scalar(StructGet(value,"schema"))!=1)throw std::runtime_error("unsupported reward policy");
    for(size_t i=0;i<AfkExpedition::RewardKeys.size();++i){
        double number=Scalar(StructGet(value,std::string(AfkExpedition::RewardKeys[i]).c_str()));
        if(!RewardPolicy::Valid(number))throw std::runtime_error("invalid reward multiplier");
        policy.values[i]=number;
    }
    if(!Compatible() || !Install())throw std::runtime_error("Independent rewards require the current AFK plugin and compatible optional ForgePact plugin");
    auto baseline=StructGet(plan,"reward_baseline");
    if(!StructGet(baseline,"complete").ToBoolean())throw std::runtime_error("native reward baseline missing; recalibrate");
    policy.nativeMagicFind=Scalar(StructGet(baseline,"magic_find"));
    if(policy.nativeMagicFind<0)throw std::runtime_error("invalid Magic Find baseline");
    policy.enabled=true;return policy;
}
struct Rate {RValue definition;double native;std::string family;};
static std::vector<Rate> rates;
static void PrepareRates(){
    rates.clear();
    std::set<int> dungeon;
    RValue keys=g_Yytk->CallGameScript(HeroSiege::Scripts::gml_Script_GetDungeonKeys.data(),{});
    if(keys.m_Kind!=VALUE_ARRAY)throw std::runtime_error("dungeon-key pool unavailable");
    for(int i=0;i<g_Yytk->CallBuiltin("array_length",{keys}).ToDouble();++i)dungeon.insert(int(g_Yytk->CallBuiltin("array_get",{keys,RValue(double(i))}).ToDouble()));
    // Verified supported-build repository bounds; never probe past them.
    for(const auto& [category,count]:std::vector<std::pair<int,int>>{{12,44},{13,65},{15,200},{16,156}}){
        for(int i=0;i<count;++i){
            RValue item=g_Yytk->CallGameScript(HeroSiege::Scripts::gml_Script_GetNormalRepoStruct.data(),{RValue(double(category)),RValue(0.0),RValue(double(i))});
            if(item.m_Kind!=VALUE_OBJECT)throw std::runtime_error("reward repository changed");
            RValue definition=StructGet(item,"droprate");if(definition.m_Kind!=VALUE_OBJECT)continue;
            const double current=Scalar(StructGet(definition,"base"));if(current<=0)continue;
            const auto* shared=HeroSiege::RewardScope::Get();const double published=shared->nativeDropBase[category*256+i];
            double native=published>0?published:current;
            std::string name=StructGet(StructGet(item,"itemBaseInfoStruct"),"28").ToString(),family;
            if(category==16)family="relic";
            else if(category==12){
                if(dungeon.count(i))family="dungeon";
                if(name=="keys_angelic_key")family="angelic";
                else if(name=="keys_chaos_key" || name=="keys_crystal_key")family="chaos";
                else if(name=="keys_bifrost_key")family="bifrost";
                else if(name=="keys_ruby_key")family="ruby";
            }else{
                static const std::pair<const char*,const char*> patterns[]={
                    {"rune","_rune$"},{"orb","socketable_orb"},{"bossgem","socketable_gem"},
                    {"scrollofra","scroll_of_ra"},{"dimshard","dimensional_shard"},{"battlefrag","battle_fragment"},{"colosfrag","colosseum_fragment"},
                    {"stone","socketable_chipped_"},{"stone","socketable_flawed_"},{"stone","socketable_flawless_"},
                    {"stone","socketable_amethyst"},{"stone","socketable_diamond"},{"stone","socketable_emerald"},{"stone","socketable_ruby"},
                    {"stone","socketable_sapphire"},{"stone","socketable_skull"},{"stone","socketable_topaz"}};
                for(const auto& [key,part]:patterns)if(AfkExpedition::RewardNameMatches(name,part)){family=key;break;}
            }
            if(!family.empty() || published>0)rates.push_back({definition,native,family});
        }
    }
}
class Batch {
    std::unique_ptr<HeroSiege::RewardScope::Guard> guard;
    std::vector<std::pair<RValue,RValue>> changed;
    void Restore()noexcept{
        active=nullptr;extraRelic=false;
        for(auto it=changed.rbegin();it!=changed.rend();++it)try{g_Yytk->CallBuiltin("variable_struct_set",{it->first,RValue("base"),it->second});}catch(...){restorationFailed=true;Out("ERROR: native drop-rate restoration failed; restart required");}
        changed.clear();guard.reset();
    }
public:
    explicit Batch(const RewardPolicy& policy){
        if(!policy.enabled)return;
        if(!Compatible())throw std::runtime_error("ForgePact reward isolation unavailable");
        guard=std::make_unique<HeroSiege::RewardScope::Guard>();active=&policy;
        try{for(const auto& rate:rates){
            RValue before=StructGet(rate.definition,"base");
            changed.push_back({rate.definition,before});
            const double mult=rate.family.empty()?1:policy.get(rate.family);
            g_Yytk->CallBuiltin("variable_struct_set",{rate.definition,RValue("base"),RValue(AfkExpedition::DropBase(rate.native,mult))});
        }}catch(...){Restore();throw;}
    }
    ~Batch(){Restore();}
};
static std::string PaceContext(const RValue& context){
    RValue inputs=StructGet(context,"inputs");if(inputs.m_Kind!=VALUE_OBJECT)throw std::runtime_error("farm context inputs missing");
    // Clone before removing reward fields; never change the live/source stamp.
    inputs=g_Yytk->CallBuiltin("json_parse",{RValue(Canonical(inputs))});
    RValue fp=StructGet(inputs,"forgepact");if(fp.m_Kind!=VALUE_OBJECT)throw std::runtime_error("farm modifier context missing");
    for(const char* key:{"drops","keys","angelic_items","mod_filter_max_relics"})g_Yytk->CallBuiltin("variable_struct_remove",{fp,RValue(key)});
    RValue stats=StructGet(fp,"stats");
    if(stats.m_Kind==VALUE_OBJECT){
        for(const char* key:{"exp","magicfind","expgain","extragold"})g_Yytk->CallBuiltin("variable_struct_remove",{stats,RValue(key)});
        if(Canonical(stats)=="{}")g_Yytk->CallBuiltin("variable_struct_remove",{fp,RValue("stats")});
    }
    return Canonical(inputs);
}
static void Probe(){
    std::ostringstream output;
    try {
        if(!Install() || !Compatible())throw std::runtime_error("reward hooks or ForgePact compatibility unavailable");
        RewardPolicy policy;policy.enabled=true;policy.values.fill(2);policy.values[0]=40;policy.nativeMagicFind=NativeMagicFind();
        PrepareRates();std::vector<double> before;
        for(const auto& rate:rates)before.push_back(Scalar(StructGet(rate.definition,"base")));
        bool changedCorrectly=true,mfMatches=false;double effective=-1;
        {
            Batch batch(policy);
            effective=NativeMagicFind();
            mfMatches=std::abs(mfScaledComponent-mfNativeComponent*40)<.0001;
            for(const auto& rate:rates)changedCorrectly &= std::abs(Scalar(StructGet(rate.definition,"base"))-AfkExpedition::DropBase(rate.native,rate.family.empty()?1:2))<.00001;
        }
        bool restored=!HeroSiege::RewardScope::Active() && active==nullptr && !restorationFailed;
        for(size_t i=0;i<rates.size();++i)restored &= before[i]==Scalar(StructGet(rates[i].definition,"base"));
        restored &= std::abs(NativeMagicFind()-policy.nativeMagicFind)<.0001;
        std::map<std::string,int> families;for(const auto& rate:rates)if(!rate.family.empty())++families[rate.family];
        int xpSamples=0;double nativeXpSample=0;RValue enemies;
        if(ObjectIndex("Enemy_Parent_obj",enemies)){
            int count=int(g_Yytk->CallBuiltin("instance_number",{enemies}).ToDouble());
            for(int i=0;i<(std::min)(64,count);++i){
                auto value=NativeKillXp(g_Yytk->CallBuiltin("instance_find",{enemies,RValue(double(i))}));
                if(value!="null" && std::stod(value)>0){++xpSamples;nativeXpSample=std::stod(value);}
            }
        }
        output<<"{\"schema\":1,\"plugin\":\""<<AFK_EXPEDITION_VERSION<<"\",\"game_build\":\""<<GAME_BUILD_ID<<"\",\"native_magic_find\":"<<policy.nativeMagicFind
            <<",\"effective_magic_find\":"<<effective<<",\"magic_find_multiplier\":40,\"mf_component_matches\":"<<(mfMatches?"true":"false")<<",\"rates_checked\":"<<rates.size()
            <<",\"rates_match\":"<<(changedCorrectly?"true":"false")<<",\"restored\":"<<(restored?"true":"false")
            <<",\"xp_samples\":"<<xpSamples<<",\"native_xp_sample\":"<<nativeXpSample
            <<",\"forgepact_loaded\":"<<(ForgePactLoaded()?"true":"false")<<",\"families\":{";
        bool first=true;for(const auto& [key,count]:families){if(!first)output<<',';first=false;output<<'"'<<key<<"\":"<<count;}
        output<<"},\"passed\":"<<(changedCorrectly&&restored&&mfMatches&&families.size()==14?"true":"false")<<"}";
    }catch(const std::exception& e){output<<"{\"passed\":false,\"error\":\""<<JsonEscape(e.what())<<"\"}";}
    const auto folder=fs::path(DATA_ROOT)/"verification";fs::create_directories(folder);
    std::ofstream file(folder/"reward-probe.json");file<<output.str();file.close();Out("rewards probe: "+output.str());
}
}
static std::string RewardBaselineJson(){return IndependentRewards::Baseline();}
static std::string NativeKillXpJson(const RValue& instance){return IndependentRewards::NativeKillXp(instance);}
