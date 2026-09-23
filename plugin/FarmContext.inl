// Stable inputs for measured farm profiles. This does not calculate damage.
static std::string Canonical(const RValue& value, unsigned depth=0) {
    if (depth>20) throw std::runtime_error("context nesting exceeds limit");
    if(value.m_Kind==VALUE_STRING || value.m_Kind==VALUE_BOOL || value.m_Kind==VALUE_UNDEFINED || IsNumberKind(value))return Stringify(value);
    if (value.m_Kind==VALUE_ARRAY) {
        const auto n=g_Yytk->CallBuiltin("array_length",{value}).ToDouble();
        if(n<0 || n>4096) throw std::runtime_error("context array exceeds limit");
        std::string out="[";
        for(int i=0;i<n;++i) { if(i)out+=',';out+=Canonical(g_Yytk->CallBuiltin("array_get",{value,RValue(double(i))}),depth+1); }
        return out+']';
    }
    if(value.m_Kind==VALUE_OBJECT || IsDsRef(value,1)) {
        std::map<std::string,std::string> entries;
        const bool map=IsDsRef(value,1);
        if(map) {
            const double n=g_Yytk->CallBuiltin("ds_map_size",{value}).ToDouble();
            if(n<0 || n>4096)throw std::runtime_error("context map exceeds limit");
            RValue key=g_Yytk->CallBuiltin("ds_map_find_first",{value});
            for(int i=0;i<n;++i) {
                if(key.m_Kind!=VALUE_STRING)throw std::runtime_error("context map key is not text");
                entries[key.ToString()]=Canonical(g_Yytk->CallBuiltin("ds_map_find_value",{value,key}),depth+1);
                key=g_Yytk->CallBuiltin("ds_map_find_next",{value,key});
            }
        } else {
            RValue keys=g_Yytk->CallBuiltin("variable_struct_get_names",{value});
            const auto n=g_Yytk->CallBuiltin("array_length",{keys}).ToDouble();
            if(n<0 || n>4096)throw std::runtime_error("context struct exceeds limit");
            for(int i=0;i<n;++i) {
                RValue key=g_Yytk->CallBuiltin("array_get",{keys,RValue(double(i))});
                entries[key.ToString()]=Canonical(g_Yytk->CallBuiltin("variable_struct_get",{value,key}),depth+1);
            }
        }
        std::string out="{";bool first=true;
        for(const auto& [key,entry]:entries) {if(!first)out+=',';first=false;out+='"'+JsonEscape(key)+"\":"+entry;}
        return out+'}';
    }
    if(value.m_Kind==VALUE_STRING || value.m_Kind==VALUE_BOOL || value.m_Kind==VALUE_UNDEFINED || IsNumberKind(value))return Stringify(value);
    throw std::runtime_error("unresolved value in farm context");
}
static RValue FarmAt(const RValue& value,int index) {
    if(value.m_Kind!=VALUE_ARRAY || index<0 || g_Yytk->CallBuiltin("array_length",{value}).ToDouble()<=index)
        throw std::runtime_error("farm context array missing");
    return g_Yytk->CallBuiltin("array_get",{value,RValue(double(index))});
}
static std::string g_FarmContextError;
static bool g_FarmContextLoading = false;
static std::string FarmContextJson() {
    g_FarmContextLoading=false;
    g_FarmContextError="primary player context unavailable";
    try {
        if(GAME_BUILD_ID!="exe-281751552-pe6aaa6779-111b8000")return "null";
        RValue online=g_Yytk->CallBuiltin("variable_global_get",{RValue("onl")});
        if(!IsNumberKind(online) || online.ToDouble()!=0)return "null";
        RValue loader;
        if(ObjectIndex("Load_Specific_Stats_obj",loader) && g_Yytk->CallBuiltin("instance_number",{loader}).ToDouble()!=0){g_FarmContextLoading=true;return "null";}
        RValue object; if(!ObjectIndex("Player_obj",object))return "null";
        const double players=g_Yytk->CallBuiltin("instance_number",{object}).ToDouble();
        if(players!=1){g_FarmContextLoading=players==0;return "null";}
        const RValue player=g_Yytk->CallBuiltin("instance_find",{object,RValue(0.0)});
        CInstance* self=ResolveInstance(player);if(!self){g_FarmContextLoading=true;return "null";}
        RValue number,local;
        if(!AurieSuccess(g_Yytk->CallGameScriptEx(number,HeroSiege::Scripts::gml_Script_GetPlayerNumber.data(),self,self,{})) || number.ToDouble()!=1){g_FarmContextLoading=true;return "null";}
        if(!AurieSuccess(g_Yytk->CallGameScriptEx(local,HeroSiege::Scripts::gml_Script_GetLocalPlayerObj.data(),self,self,{number})) || ResolveInstance(local)!=self){g_FarmContextLoading=true;return "null";}
        int slot=-1;const RValue map=SelectedSlotMap(&slot);if(slot<0){g_FarmContextLoading=true;return "null";}
        auto field=[&](const char* name){
            if(!g_Yytk->CallBuiltin("ds_map_exists",{map,RValue(name)}).ToBoolean())throw std::runtime_error("missing farm field");
            return g_Yytk->CallBuiltin("ds_map_find_value",{map,RValue(name)});
        };
        const std::string loadout=SlotMapNumber(map,"loadout"),difficulty=SlotMapNumber(map,"difficulty");
        if(loadout=="null" || difficulty=="null")return "null";
        RValue equipped=g_Yytk->CallBuiltin("variable_global_get",{RValue("equippedItems")});
        equipped=FarmAt(equipped,1);
        g_FarmContextError="equipped item sets unavailable";
        if(equipped.m_Kind!=VALUE_ARRAY)return "null";
        const auto sets=g_Yytk->CallBuiltin("array_length",{equipped}).ToDouble();
        if(sets<1 || sets>8)return "null";
        // Saved loadout numbers are one-based. Inactive sets can refer to items
        // absent from this inventory; only the active set determines farm speed.
        const double active=std::stod(loadout);
        if(active<1 || active>sets || active!=double(int(active)))return "null";
        equipped=FarmAt(equipped,int(active)-1);
        if(equipped.m_Kind!=VALUE_ARRAY || g_Yytk->CallBuiltin("array_length",{equipped}).ToDouble()!=18)return "null";
        std::set<std::string> wanted;
        for(int i=0;i<18;++i){RValue id=FarmAt(equipped,i);if(id.m_Kind!=VALUE_STRING)return "null";if(!id.ToString().empty())wanted.insert(id.ToString());}
        g_FarmContextError="equipped item definitions incomplete";
        RValue inventory=field("inventory");if(inventory.m_Kind!=VALUE_ARRAY){g_FarmContextError="inventory kind="+std::to_string(int(inventory.m_Kind));return "null";}
        const double total=g_Yytk->CallBuiltin("array_length",{inventory}).ToDouble();if(total>4096)return "null";
        std::map<std::string,std::string> items;
        for(int i=0;i<total;++i){
            RValue item=FarmAt(inventory,i);if(item.m_Kind!=VALUE_OBJECT)continue;
            auto part=[&](const char* k){return g_Yytk->CallBuiltin("variable_struct_get",{item,RValue(k)});};
            RValue type=part("itemType");if(!IsNumberKind(type))continue;
            std::string id=part("itemAccount").ToString()+"-"+part("itemRegion").ToString()+"-"+part("itemTimeStamp").ToString()+"-"+std::to_string(int(type.ToDouble()));
            if(!wanted.count(id))continue;
            RValue definition=part("itemDefinitionStruct");if(definition.m_Kind!=VALUE_OBJECT){g_FarmContextError="definition missing for "+id;return "null";}
            items[id]="{\"definition\":"+Canonical(definition)+",\"forgepact_mechanic\":"+Canonical(part("fp_mechanic"))+"}";
        }
        if(items.size()!=wanted.size()){
            g_FarmContextError="matched "+std::to_string(items.size())+" of "+std::to_string(wanted.size())+" equipped definitions; inventory "+std::to_string(int(total))+"; missing:";
            for(const auto& id:wanted)if(!items.count(id))g_FarmContextError+=" "+id;
            return "null";
        }
        g_FarmContextError="talent context unavailable";
        RValue talentLoadout=field("talent_loadout");if(!IsNumberKind(talentLoadout))return "null";
        std::string payload="{\"schema\":1,\"build\":\""+GAME_BUILD_ID+"\",\"character\":"+CharacterStampJson()+",\"difficulty\":"+difficulty
            +",\"hero_level\":"+SlotMapNumber(map,"herolevel")+",\"loadout\":"+loadout+",\"equipped\":"+Canonical(equipped)+",\"items\":{";
        bool first=true;for(const auto& [id,item]:items){if(!first)payload+=',';first=false;payload+='"'+JsonEscape(id)+"\":"+item;}
        payload+="},\"talents\":"+Canonical(FarmAt(field("talentMap"),int(talentLoadout.ToDouble())))
            +",\"subtalents\":"+Canonical(FarmAt(field("subTalentMap"),int(talentLoadout.ToDouble())));
        for(const char* name:{"attribute_points","bind_skill","bind_skill2","merc_talents","incarnation_loadout","ether_loadout"})
            payload+=",\""+std::string(name)+"\":"+Canonical(field(name));
        const auto settings=ForgePactSettingsJson();if(settings=="null")return "null";
        payload+=",\"forgepact\":"+Canonical(g_Yytk->CallBuiltin("json_parse",{RValue(settings)}))+"}";
        g_FarmContextError.clear();
        return "{\"schema\":1,\"hash\":\""+AfkExpedition::Sha256::Of(payload)+"\",\"inputs\":"+payload+"}";
    } catch(const std::exception& e) {g_FarmContextError=e.what();return "null";}
      catch(...) {g_FarmContextError="farm context capture failed";return "null";}
}
