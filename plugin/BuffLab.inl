// Research R3 (Stronghold design, 2026-09-26): the buff lab.
//
// Questions it answers in a running offline game:
// - How does the game add, time, show and apply a buff on the hero? (the
//   game's buff routines are logged with their arguments and results)
// - Which buff ids does the game's stat code ask about? (GetBuff and
//   GetBuffStack are counted per id)
// - What does a buff change? (ReturnSpecificStat, the game's own stat query,
//   is read for a range of stat ids before and after)
//
// Commands (`afk buff ...`), research build only:
//   buff hook                        log the buff routines (installed once)
//   buff log [n] | clear | quiet | loud
//   buff stats                       call counts; the ids GetBuff / GetBuffStack were asked about
//   buff snap [from] [to]            ReturnSpecificStat(1, id, 0) for ids from..to (default 0..160)
//   buff diff                        the same ids again: which changed since the snapshot
//   buff table                       global playerBuff: its sizes and the entries in use
//   buff list                        the live buff instances (Draw_Player_Buff_obj)
//   buff statmap [from] [to]         names the ReturnSpecificStat ids: detours on every Stat*
//                                    routine, then which one each id reaches first
//   buff scan <from> <to> [value] [frames]   add each buff type natively, record the stats it
//                                    changes, remove it (types past the table size are refused)
// Buffs themselves are added with the plugin's `call <Script> [args]`
// (self = other = the hero).
namespace BuffLab {

struct Hooked {
    const char* name;
    bool log = true;        // one log line per call
    bool perId = false;     // count calls per first argument
    PFUNC_YYGMLScript orig = nullptr;
    uint64_t calls = 0;
    std::string kind;
    std::map<std::string, uint64_t> ids;
};

static constexpr int N = 12;
static Hooked g_H[N] = {
    { .name = "BuffAdd" }, { .name = "BuffRemove" }, { .name = "BuffSetStack" }, { .name = "CA_playerBuffAdd" },
    { .name = "CreateShrineEffect" }, { .name = "CodexBuff" }, { .name = "SetupBuffs" }, { .name = "GetCodexBuffs" },
    { .name = "GetBuff", .log = false, .perId = true }, { .name = "GetBuffStack", .log = false, .perId = true },
    { .name = "DrawHudBuffs", .log = false }, { .name = "DrawBuffTooltip", .log = false, .perId = true },
};
static std::string g_HookIds[N];
static std::deque<std::string> g_Log;
static bool g_Loud = true, g_Installed = false;
static uint64_t g_Frame = 0;

static std::string Short(const RValue& v)
{
    std::string s;
    try { s = Stringify(v); } catch (...) { s = "?"; }
    return s.size() > 90 ? s.substr(0, 90) + "..." : s;
}

static std::string Who(CInstance* inst)
{
    if (!inst) return "null";
    try { const std::string n = ObjectNameOf(inst->ToRValue()); return n.empty() ? std::string("?") : n; } catch (...) { return "?"; }
}

template <int I> static RValue& Hook(CInstance* self, CInstance* other, RValue& result, int argc, RValue** args)
{
    Hooked& h = g_H[I];
    ++h.calls;
    if (h.perId && argc > 0 && args && args[0]) ++h.ids[Short(*args[0])];
    std::string line;
    if (g_Loud && h.log) {
        line = "f" + std::to_string(g_Frame) + " " + h.name + "(";
        for (int i = 0; i < argc && i < 10; ++i) line += (i ? ", " : "") + (args && args[i] ? Short(*args[i]) : std::string("?"));
        line += ") self " + Who(self) + (other != self ? ", other " + Who(other) : "");
    }
    RValue& r = h.orig(self, other, result, argc, args);
    if (!line.empty()) {
        g_Log.push_back(line + " -> " + Short(r));
        while (g_Log.size() > 400) g_Log.pop_front();
    }
    return r;
}

static const PFUNC_YYGMLScript g_Hooks[N] = { Hook<0>, Hook<1>, Hook<2>, Hook<3>, Hook<4>, Hook<5>, Hook<6>, Hook<7>, Hook<8>, Hook<9>, Hook<10>, Hook<11> };

static void Install()
{
    if (g_Installed) { Out("buff hook: already installed"); return; }
    g_Installed = true;
    for (int i = 0; i < N; ++i) {
        g_HookIds[i] = "AfkBuffLab" + std::to_string(i);
        const bool ok = InstallScriptDetour(g_H[i].name, g_HookIds[i].c_str(), g_Hooks[i], &g_H[i].orig, g_H[i].kind);
        if (!ok && !g_H[i].orig) Out(std::string("buff hook ") + g_H[i].name + ": not installed (" + g_H[i].kind + ")");
    }
}

static void Stats()
{
    Out("buff stats (frame " + std::to_string(g_Frame) + "):");
    for (const Hooked& h : g_H) {
        std::string line = std::string("  ") + h.name + ": " + std::to_string(h.calls) + " calls" + (h.orig ? "" : " (not hooked)");
        if (h.perId && !h.ids.empty()) {
            std::vector<std::pair<uint64_t, std::string>> rows;
            for (const auto& [id, n] : h.ids) rows.push_back({ n, id });
            std::sort(rows.rbegin(), rows.rend());
            line += "; by first argument:";
            int shown = 0;
            for (const auto& [n, id] : rows) { if (++shown > 40) { line += " ..."; break; } line += " " + id + " x" + std::to_string(n); }
        }
        Out(line);
    }
}

// ReturnSpecificStat(1, id, 0, undefined, undefined) is the game's own stat
// query (id 34 = magic find, MEASURED 2026-09-19 in the SDK).
static std::map<int, double> g_Snap;
static int g_From = 0, g_To = 160;

static bool StatAt(CInstance* hero, int id, double& out)
{
    RValue res;
    std::vector<RValue> args = { RValue(1.0), RValue((double)id), RValue(0.0), RValue(), RValue() };
    if (!AurieSuccess(g_Yytk->CallGameScriptEx(res, "gml_Script_ReturnSpecificStat", hero, hero, args))) return false;
    if (!IsNumberKind(res)) return false;
    out = res.ToDouble();
    return std::isfinite(out);
}

static CInstance* Hero()
{
    RValue pid; double px = 0, py = 0;
    if (!DefenseLab::PlayerAt(pid, px, py)) return nullptr;
    return ResolveInstance(pid);
}

static void Snap(int from, int to)
{
    CInstance* hero = Hero();
    if (!hero) { Out("buff snap: no hero"); return; }
    g_From = from; g_To = to; g_Snap.clear();
    std::string line;
    for (int id = from; id <= to; ++id) {
        double v = 0;
        if (!StatAt(hero, id, v)) continue;
        g_Snap[id] = v;
        if (v != 0) { char b[48]; std::snprintf(b, sizeof b, " %d=%g", id, v); line += b; }
    }
    Out("buff snap " + std::to_string(from) + ".." + std::to_string(to) + ": " + std::to_string(g_Snap.size()) + " stats read; nonzero:" + line);
}

static void Diff()
{
    CInstance* hero = Hero();
    if (!hero) { Out("buff diff: no hero"); return; }
    int changed = 0;
    for (const auto& [id, before] : g_Snap) {
        double now = 0;
        if (!StatAt(hero, id, now) || now == before) continue;
        ++changed;
        char b[96]; std::snprintf(b, sizeof b, "  stat %d: %g -> %g", id, before, now);
        Out(b);
    }
    Out("buff diff: " + std::to_string(changed) + " of " + std::to_string(g_Snap.size()) + " stats changed since the snapshot");
}

// global.playerBuff[player][field][type] holds a buff instance or noone (-4);
// its innermost size bounds the buff types the game can index.
static int g_TypeCount = -1;

static void Table()
{
    const RValue pb = InGameUI::Get("variable_global_get", { RValue(std::string("playerBuff")) });
    if (pb.m_Kind != VALUE_ARRAY) { Out("buff table: global playerBuff is not an array (" + KindName(pb) + ")"); return; }
    const int players = (int)InGameUI::Num("array_length", { pb });
    int smallest = -1;
    for (int pl = 0; pl < players; ++pl) {
        const RValue fields = InGameUI::Get("array_get", { pb, RValue((double)pl) });
        if (fields.m_Kind != VALUE_ARRAY) { Out("  playerBuff[" + std::to_string(pl) + "]: " + KindName(fields)); continue; }
        const int nf = (int)InGameUI::Num("array_length", { fields });
        for (int f = 0; f < nf; ++f) {
            const RValue row = InGameUI::Get("array_get", { fields, RValue((double)f) });
            if (row.m_Kind != VALUE_ARRAY) { Out("  playerBuff[" + std::to_string(pl) + "][" + std::to_string(f) + "]: " + KindName(row)); continue; }
            const int n = (int)InGameUI::Num("array_length", { row });
            if (smallest < 0 || n < smallest) smallest = n;
            std::string set;
            for (int i = 0; i < n; ++i) {
                const RValue e = InGameUI::Get("array_get", { row, RValue((double)i) });
                if (IsNumberKind(e) && e.ToDouble() == -4) continue;
                set += " " + std::to_string(i) + "=" + Short(e);
            }
            Out("  playerBuff[" + std::to_string(pl) + "][" + std::to_string(f) + "]: " + std::to_string(n) + " entries; in use:" + (set.empty() ? " none" : set));
        }
    }
    g_TypeCount = smallest;
    Out("buff table: " + std::to_string(players) + " players; the smallest type row has " + std::to_string(smallest) + " entries");
}

static int BuffInstances()
{
    RValue obj;
    if (!ObjectIndex("Draw_Player_Buff_obj", obj)) return -1;
    return (int)InGameUI::Num("instance_number", { obj });
}

static std::set<int> ActiveTypes(bool print)
{
    std::set<int> types;
    RValue obj;
    if (!ObjectIndex("Draw_Player_Buff_obj", obj)) return types;
    const int n = (int)InGameUI::Num("instance_number", { obj });
    for (int i = 0; i < n; ++i) {
        const RValue id = InGameUI::Get("instance_find", { obj, RValue((double)i) });
        const double type = GetVarNumber(id, "buffType", NAN);
        if (std::isfinite(type)) types.insert((int)type);
        if (print) Out("  " + std::to_string(i) + ": type " + Short(GetVar(id, "buffType")) + ", value " + Short(GetVar(id, "buffValue")) + ", stack "
            + Short(GetVar(id, "buffStack")) + ", destroyTimer " + Short(GetVar(id, "destroyTimer")) + ", mercenary " + Short(GetVar(id, "mercenary"))
            + ", debuff " + Short(GetVar(id, "isDebuff")) + ", player " + Short(GetVar(id, "playerNumber")));
    }
    if (print) Out("buff list: " + std::to_string(n) + " live buff instances");
    return types;
}

static bool Script(const char* name, CInstance* hero, std::vector<RValue> args)
{
    RValue res;
    return AurieSuccess(g_Yytk->CallGameScriptEx(res, name, hero, hero, args));
}

static void Scan(int from, int to, double value, double frames)
{
    if (g_TypeCount <= 0) Table();
    if (g_TypeCount <= 0) { Out("buff scan: the buff table size is unknown; refused"); return; }
    if (to >= g_TypeCount) to = g_TypeCount - 1;
    CInstance* hero = Hero();
    if (!hero) { Out("buff scan: no hero"); return; }
    std::map<int, double> base;
    for (int id = 0; id <= 160; ++id) { double v = 0; if (StatAt(hero, id, v)) base[id] = v; }
    const std::set<int> active = ActiveTypes(false);
    const bool wasLoud = g_Loud; g_Loud = false;               // the scan's own BuffAdd calls stay out of the log
    int valid = 0, changed = 0, leftovers = 0;
    std::string silent, noBuff;
    for (int t = from; t <= to; ++t) {
        if (active.count(t)) { Out("  type " + std::to_string(t) + ": skipped (active)"); continue; }
        const int before = BuffInstances();
        Script("gml_Script_BuffAdd", hero, { RValue(1.0), RValue((double)t), RValue(value), RValue(frames), RValue(false), RValue(false), RValue(1.0), RValue(false), RValue(false), RValue(true) });
        const int after = BuffInstances();
        std::string diff;
        for (const auto& [id, v] : base) {
            double now = 0;
            if (!StatAt(hero, id, now) || now == v) continue;
            char b[64]; std::snprintf(b, sizeof b, " s%d %g->%g", id, v, now); diff += b;
        }
        Script("gml_Script_BuffRemove", hero, { RValue(1.0), RValue((double)t) });
        const int end = BuffInstances();
        if (after > before) ++valid; else noBuff += " " + std::to_string(t);
        if (end > before) ++leftovers;
        if (!diff.empty()) { ++changed; Out("  type " + std::to_string(t) + ":" + diff + (after > before ? "" : " (no instance)") + (end > before ? " NOT REMOVED" : "")); }
        else if (after > before) silent += " " + std::to_string(t) + (end > before ? "!" : "");
    }
    g_Loud = wasLoud;
    Out("  buffs with no stat change:" + (silent.empty() ? std::string(" none") : silent));
    Out("  no instance created:" + (noBuff.empty() ? std::string(" none") : noBuff));
    Out("buff scan " + std::to_string(from) + ".." + std::to_string(to) + " (value " + Short(RValue(value)) + ", " + Short(RValue(frames)) + " frames): "
        + std::to_string(valid) + " created a buff, " + std::to_string(changed) + " changed a stat, " + std::to_string(leftovers) + " not removed");
}

static constexpr int SN = 128;
static std::string g_StatNames[SN];
static std::string g_StatHookIds[SN];
static PFUNC_YYGMLScript g_StatOrig[SN] = {};
static int g_StatCount = 0, g_StatFirst = -1;
static bool g_StatProbe = false, g_StatInstalled = false;
static std::string g_StatSeen;

template <int I> static RValue& StatHook(CInstance* self, CInstance* other, RValue& result, int argc, RValue** args)
{
    if (g_StatProbe) {
        if (g_StatFirst < 0) g_StatFirst = I;
        else if (g_StatSeen.size() < 160) g_StatSeen += " " + g_StatNames[I];
    }
    return g_StatOrig[I](self, other, result, argc, args);
}
template <int... Is> static void FillStatHooks(std::integer_sequence<int, Is...>, PFUNC_YYGMLScript* out) { ((out[Is] = &StatHook<Is>), ...); }

// Every compiled script's name sits in the image's read-only data.
static std::vector<std::string> ScriptNames(const std::string& prefix)
{
    std::set<std::string> names;
    const std::string full = "gml_Script_" + prefix;
    for (const auto& sec : ModuleSections(GetModuleHandleA(nullptr))) {
        if (!sec.read || sec.exec) continue;
        const uint8_t* at = sec.begin;
        while (at < sec.end) {
            const uint8_t* hit = static_cast<const uint8_t*>(std::memchr(at, 'g', (size_t)(sec.end - at)));
            if (!hit) break;
            at = hit + 1;
            if ((size_t)(sec.end - hit) < full.size() + 2 || std::memcmp(hit, full.data(), full.size()) != 0 || (hit > sec.begin && hit[-1] != 0)) continue;
            const size_t room = (size_t)(sec.end - hit) < 120 ? (size_t)(sec.end - hit) : 120;
            const uint8_t* z = static_cast<const uint8_t*>(std::memchr(hit, 0, room));
            if (!z) continue;
            const std::string name(reinterpret_cast<const char*>(hit) + 11, (size_t)(z - hit) - 11);
            if (name.size() > prefix.size() && std::isupper((unsigned char)name[prefix.size()])) names.insert(name);
            at = z;
        }
    }
    return std::vector<std::string>(names.begin(), names.end());
}

static void StatMap(int from, int to)
{
    if (!g_StatInstalled) {
        g_StatInstalled = true;
        PFUNC_YYGMLScript hooks[SN];
        FillStatHooks(std::make_integer_sequence<int, SN>{}, hooks);
        const std::vector<std::string> names = ScriptNames("Stat");
        std::string failed;
        for (const std::string& name : names) {
            if (g_StatCount >= SN) break;
            const int i = g_StatCount;
            g_StatNames[i] = name; g_StatHookIds[i] = "AfkStatMap" + std::to_string(i);
            std::string kind;
            if (InstallScriptDetour(name.c_str(), g_StatHookIds[i].c_str(), hooks[i], &g_StatOrig[i], kind) || g_StatOrig[i]) ++g_StatCount;
            else failed += " " + name;
        }
        Out("buff statmap: " + std::to_string(names.size()) + " Stat* routines, " + std::to_string(g_StatCount) + " hooked" + (failed.empty() ? "" : "; not hooked:" + failed));
    }
    CInstance* hero = Hero();
    if (!hero) { Out("buff statmap: no hero"); return; }
    for (int id = from; id <= to; ++id) {
        g_StatFirst = -1; g_StatSeen.clear(); g_StatProbe = true;
        double v = 0;
        const bool ok = StatAt(hero, id, v);
        g_StatProbe = false;
        char b[64]; std::snprintf(b, sizeof b, "  %d = %g: ", id, v);
        Out(std::string(b) + (!ok ? "(no number)" : g_StatFirst < 0 ? "(no Stat* routine)" : g_StatNames[g_StatFirst]) + (g_StatSeen.empty() ? "" : "; then" + g_StatSeen));
    }
}

static void Command(const std::string& verb, const std::string& first, std::istream& rest)
{
    std::string second; rest >> second;
    try {
        if (verb == "hook") { Install(); return; }
        if (verb == "log") {
            const size_t n = first.empty() ? 40 : (size_t)std::strtoul(first.c_str(), nullptr, 10);
            Out("buff log (last " + std::to_string(n) + " of " + std::to_string(g_Log.size()) + "):");
            for (size_t i = g_Log.size() > n ? g_Log.size() - n : 0; i < g_Log.size(); ++i) Out("  " + g_Log[i]);
            return;
        }
        if (verb == "clear") { g_Log.clear(); for (Hooked& h : g_H) { h.calls = 0; h.ids.clear(); } Out("buff: log and counts cleared"); return; }
        if (verb == "quiet") { g_Loud = false; Out("buff: logging off (counts continue)"); return; }
        if (verb == "loud") { g_Loud = true; Out("buff: logging on"); return; }
        if (verb == "stats") { Stats(); return; }
        if (verb == "snap") {
            const int from = first.empty() ? 0 : std::atoi(first.c_str()), to = second.empty() ? 160 : std::atoi(second.c_str());
            Snap(from, to); return;
        }
        if (verb == "diff") { Diff(); return; }
        if (verb == "table") { Table(); return; }
        if (verb == "statmap") { StatMap(first.empty() ? 0 : std::atoi(first.c_str()), second.empty() ? 160 : std::atoi(second.c_str())); return; }
        if (verb == "list") { ActiveTypes(true); return; }
        if (verb == "scan") {
            std::string sv, sf; rest >> sv >> sf;
            const int from = first.empty() ? 0 : std::atoi(first.c_str()), to = second.empty() ? from : std::atoi(second.c_str());
            Scan(from, to, sv.empty() ? 50.0 : std::atof(sv.c_str()), sf.empty() ? 60.0 : std::atof(sf.c_str()));
            return;
        }
    } catch (...) { Out("buff " + verb + ": EXCEPTION"); return; }
    Out("buff: usage -> buff hook | log [n] | clear | quiet | loud | stats | snap [from] [to] | diff | table | list | scan <from> <to> [value] [frames]");
}

static void Frame() { ++g_Frame; }

} // namespace BuffLab
