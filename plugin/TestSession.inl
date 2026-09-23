// Read-only JSON state for external test setup. All transitions still use the
// game's named menu callbacks; no input injection or save editing is involved.
namespace TestSession {
static RValue Object(HeroSiege::Objects::GameObject object) {
    RValue result;
    if (!ObjectIndex(std::string(HeroSiege::Objects::GetObjectName(object)), result))
        throw std::runtime_error("session object unavailable");
    return result;
}
static int Count(const RValue& object) {
    const double n = g_Yytk->CallBuiltin("instance_number", {object}).ToDouble();
    if (!std::isfinite(n) || n < 0 || n > 1024 || n != std::floor(n))
        throw std::runtime_error("invalid session instance count");
    return static_cast<int>(n);
}
static int Integer(const RValue& value) {
    if (value.m_Kind == VALUE_BOOL || !IsNumberKind(value)) return -1;
    const double n = value.ToDouble();
    return std::isfinite(n) && n >= 0 && n < 1024 && n == std::floor(n)
        ? static_cast<int>(n) : -1;
}
static std::string Identity(CInstance* context, int slot) {
    if (!context || slot < 0) return "null";
    const RValue map = SlotMap(context, slot);
    return "{\"slot\":" + std::to_string(slot) + ",\"name\":\""
        + JsonEscape(SlotMapString(map, "name")) + "\",\"class\":"
        + SlotMapNumber(map, "class") + "}";
}
static void State(const std::string& request,bool isolated=false) {
    if (request.empty() || request.size() > 64 || request.find_first_not_of(
            "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-") != std::string::npos) {
        Out("session state: supply an alphanumeric request id (max 64)"); return;
    }
    try {
        using HeroSiege::Objects::GameObject;
        const RValue menus = Object(GameObject::Menu_Controller_obj);
        const RValue players = Object(GameObject::Player_obj);
        const RValue choose = Object(GameObject::UI_Choose_Hero_obj);
        const RValue characterMenu = Object(GameObject::UI_Character_obj);
        const RValue buttons = Object(GameObject::Choose_Parent_obj);
        const int menuCount=Count(menus), playerCount=Count(players), buttonCount=Count(buttons);
        if (buttonCount > 128) throw std::runtime_error("too many character buttons");
        CInstance* context = nullptr;
        if (menuCount == 1) context=ResolveInstance(g_Yytk->CallBuiltin("instance_find", {menus,RValue(0.0)}));
        else if (playerCount == 1) context=ResolveInstance(g_Yytk->CallBuiltin("instance_find", {players,RValue(0.0)}));
        RValue online=g_Yytk->CallBuiltin("variable_global_get", {RValue("onl")});
        std::string onlineJson="null";
        if (IsNumberKind(online) && (online.ToDouble()==0 || online.ToDouble()==1))
            onlineJson=online.ToDouble()==0 ? "false" : "true";
        RValue slots=g_Yytk->CallBuiltin("variable_global_get", {RValue("slot")});
        int selected=-1;
        if (slots.m_Kind==VALUE_ARRAY && g_Yytk->CallBuiltin("array_length",{slots}).ToDouble()>1)
            selected=Integer(g_Yytk->CallBuiltin("array_get",{slots,RValue(1.0)}));
        std::ostringstream out;
        out << "{\"document_type\":\"afk.session-state\",\"schema\":1,\"request_id\":\"" << request
            << "\",\"pid\":" << GetCurrentProcessId() << ",\"captured_at\":\"" << NowIso()
            << "\",\"plugin\":\"" << AFK_EXPEDITION_VERSION << "\",\"game_build\":\"" << GAME_BUILD_ID
            << "\",\"room\":\"" << JsonEscape(CurrentRoomName()) << "\",\"online\":" << onlineJson
            << ",\"replay_running\":" << (g_Exp.running ? "true" : "false")
            << ",\"player_count\":" << playerCount << ",\"menu_count\":" << menuCount
            << ",\"choose_count\":" << Count(choose) << ",\"character_menu_count\":" << Count(characterMenu)
            << ",\"selected_slot\":" << (selected<0 ? "null" : std::to_string(selected))
            << ",\"selected_character\":" << Identity(context,selected)
            << ",\"character\":" << CharacterStampJson() << ",\"choices\":[";
        for (int i=0;i<buttonCount;++i) {
            const RValue button=g_Yytk->CallBuiltin("instance_find",{buttons,RValue(static_cast<double>(i))});
            const int uiSlot=Integer(GetVar(button,"slot"));
            // Measured UI slot is one-based; persisted/global selected slot is zero-based.
            const int slot=uiSlot>0 ? uiSlot-1 : -1;
            const RValue map=slot>=0 && context ? SlotMap(context,slot) : RValue();
            const bool enabled=GetVar(button,"enabled").ToBoolean()
                && !GetVar(button,"hidden").ToBoolean() && !GetVar(button,"loadInProgress").ToBoolean();
            if(i)out << ',';
            out << "{\"ordinal\":" << i << ",\"slot\":" << slot << ",\"name\":\""
                << JsonEscape(SlotMapString(map,"name")) << "\",\"class\":" << SlotMapNumber(map,"class")
                << ",\"enabled\":" << (enabled ? "true" : "false") << '}';
        }
        out << "],\"world\":null,\"capture_on\":" << (g_CaptureOn ? "true" : "false")
            << ",\"capture_file\":\"" << JsonEscape(g_SessionFile) << "\",\"forgepact\":" << ForgePactSettingsJson()
            << ",\"farm_context\":" << FarmContextJson()
            << ",\"reward_baseline\":" << RewardBaselineJson()
            << ",\"farm_context_error\":\"" << JsonEscape(g_FarmContextError) << "\""
            << ",\"hook_native\":" << (g_DropItemHookKind.rfind("native",0)==0 ? "true" : "false") << '}';
        const fs::path directory=fs::path(DATA_ROOT)/"models";
        fs::create_directories(directory);
        const fs::path path=directory/(isolated?"session-state-"+request+".json":"session-state.json");
        const fs::path temporary=path.wstring()+L".tmp";
        { std::ofstream file(temporary,std::ios::binary|std::ios::trunc);
          file << out.str();file.flush();if(!file)throw std::runtime_error("session state write failed"); }
        if (!MoveFileExW(temporary.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))
            throw std::runtime_error("session state replace failed");
        Out("session state: saved " + path.string());
    } catch(const std::exception& error) { Out(std::string("session state: ")+error.what()); }
      catch(...) { Out("session state: capture failed"); }
}
}
