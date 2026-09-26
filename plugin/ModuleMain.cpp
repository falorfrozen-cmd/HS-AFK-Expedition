// HS AFK Expedition - Aurie/YYToolkit plugin
//
// Phase 0 scope (see docs/DESIGN.md §11):
//   P0-0  load next to ForgePact (BloodPactPlugin) and the Tracker producer,
//         report what else is loaded and whether our hook got native
//         interception;
//   P0-1  hook gml_Script_DropItem and, while capture is armed, record one
//         packet per kill: the call's arguments, a snapshot of the dying
//         monster's instance variables, the room, and the kill's experience.
//
// Everything here is our own code reacting to measured runtime behaviour:
// names of scripts and variables, and the shapes of the values they carry.
// No game script text is reproduced (hub AGENTS.md, "Legal").
//
// IPC: <game bin>\afk_ipc\cmd.txt is read every few frames, one command per
// line, and deleted; replies are appended to <game bin>\afk_ipc\out.txt.
// Data: %LOCALAPPDATA%\Hero_Siege\afk\{packets,sessions}.

#include <YYToolkit/YYTK_Shared.hpp>
#include <hs_game_sdk/hs_game_sdk.hpp>
#include <hs_game_sdk/native_names.hpp>
#include <hs_game_sdk/reward_scope.hpp>
#include <hs_game_sdk/reward_stats.hpp>
#include <AfkExpedition/RewardPolicy.hpp>
#include <AfkExpedition/Version.hpp>
#include <AfkExpedition/Packet.hpp>
#include <AfkExpedition/RuntimeState.hpp>
#include <AfkExpedition/Conversion.hpp>
#include <AfkExpedition/Worker.hpp>
#include <iomanip>
#include <limits>
#include <stdexcept>

#include <windows.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cctype>
#include <cmath>
#include <ctime>
#include <deque>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace Aurie;
using namespace YYTK;
namespace fs = std::filesystem;
using AfkExpedition::JsonEscape;

// ---------------------------------------------------------------- globals
static YYTKInterface* g_Yytk = nullptr;
static AurieModule*   g_Module = nullptr;
static std::mutex     g_OutMutex;

static std::string GameBinDir()
{
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(nullptr, buf, MAX_PATH);
    return fs::path(buf).parent_path().string();
}
static const std::string IPC_DIR = GameBinDir() + "\\afk_ipc";
static std::string CmdPath() { return IPC_DIR + "\\cmd.txt"; }
static std::string OutPath() { return IPC_DIR + "\\out.txt"; }

static std::string DataRoot()
{
    char* v = nullptr; size_t n = 0;
    std::string base;
    if (_dupenv_s(&v, &n, "LOCALAPPDATA") == 0 && v) { base = v; free(v); }
    if (base.empty()) base = GameBinDir();
    return base + "\\Hero_Siege\\afk";
}
static const std::string DATA_ROOT = DataRoot();
static std::string PacketsDir()  { return DATA_ROOT + "\\packets"; }
static std::string SessionsDir() { return DATA_ROOT + "\\sessions"; }

static void EnsureDirs()
{
    std::error_code ec;
    fs::create_directories(IPC_DIR, ec);
    fs::create_directories(PacketsDir(), ec);
    fs::create_directories(SessionsDir(), ec);
}

static void Out(const std::string& s)
{
    std::lock_guard<std::mutex> lk(g_OutMutex);
    std::ofstream f(OutPath(), std::ios::app);
    f << s << "\n";
    if (g_Yytk) g_Yytk->PrintInfo("[AFK] %s", s.c_str());
}

static std::string NowIso()
{
    using namespace std::chrono;
    auto t = system_clock::to_time_t(system_clock::now());
    std::tm tm{}; gmtime_s(&tm, &t);
    char buf[32]; std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}
static std::string NowStamp()
{
    using namespace std::chrono;
    auto t = system_clock::to_time_t(system_clock::now());
    std::tm tm{}; localtime_s(&tm, &t);
    char buf[32]; std::strftime(buf, sizeof buf, "%Y%m%d_%H%M%S", &tm);
    return buf;
}

// Build identity: size + last-write time of the game executable. Enough to
// refuse replaying a packet captured on a different build (DESIGN §15).
static std::string ComputeGameBuildId()
{
    // Content identity, not file identity: the linker's time stamp and image
    // size from the PE header survive a re-copy of the executable (MEASURED
    // 2026-09-18: the exe was re-created with the same bytes and a new file
    // time, which made every packet "another build" under the old scheme).
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(nullptr, buf, MAX_PATH);
    std::error_code ec;
    auto size = fs::file_size(buf, ec);
    unsigned long stamp = 0, image = 0;
    try {
        const BYTE* base = (const BYTE*)GetModuleHandleA(nullptr);
        const IMAGE_DOS_HEADER* dos = (const IMAGE_DOS_HEADER*)base;
        if (dos && dos->e_magic == IMAGE_DOS_SIGNATURE) {
            const IMAGE_NT_HEADERS* nt = (const IMAGE_NT_HEADERS*)(base + dos->e_lfanew);
            if (nt->Signature == IMAGE_NT_SIGNATURE) { stamp = nt->FileHeader.TimeDateStamp; image = nt->OptionalHeader.SizeOfImage; }
        }
    } catch (...) {}
    char out[96];
    std::snprintf(out, sizeof out, "exe-%llu-pe%lx-%lx", (unsigned long long)size, stamp, image);
    return out;
}
static const std::string GAME_BUILD_ID = ComputeGameBuildId();

// ----------------------------------------------------------- RValue helpers
static std::string KindName(const RValue& v)
{
    try { return v.GetKindName(); } catch (...) { return "?"; }
}

static bool IsNumberKind(const RValue& v) { return v.m_Kind == VALUE_REAL || v.m_Kind == VALUE_INT32 || v.m_Kind == VALUE_INT64 || v.m_Kind == VALUE_BOOL; }

// Asset indices never change while the game runs, so each name is looked up
// once per session. A replayed kill resolves about fifty sprite/sound names
// and several object names; asking the runner every time was a large share of
// its cost. Game thread only.
static std::unordered_map<std::string, RValue> g_AssetIndexCache;
static RValue AssetIndexCached(const std::string& name)
{
    auto it = g_AssetIndexCache.find(name);
    if (it != g_AssetIndexCache.end()) return it->second;
    RValue idx;
    try { idx = g_Yytk->CallBuiltin("asset_get_index", { RValue(name) }); } catch (...) { return RValue(); }
    g_AssetIndexCache.emplace(name, idx);
    return idx;
}

// asset_get_index returns a typed asset reference on this runner (VALUE_REF),
// not a plain number (MEASURED 2026-09-17: a number check rejected every
// object). Validate through object_exists instead of looking at the kind.
static bool ObjectIndex(const std::string& name, RValue& out)
{
    static std::unordered_map<std::string, std::pair<bool, RValue>> known;
    auto it = known.find(name);
    if (it != known.end()) { out = it->second.second; return it->second.first; }
    try {
        out = AssetIndexCached(name);
        bool exists = out.m_Kind != VALUE_UNDEFINED && !(IsNumberKind(out) && out.ToDouble() < 0)
            && g_Yytk->CallBuiltin("object_exists", { out }).ToBoolean();
        known.emplace(name, std::make_pair(exists, out));
        return exists;
    } catch (...) { return false; }
}

// JSON text for any value: the game's own json_stringify for everything it
// can serialise (reals, strings, arrays, structs), a tagged fallback for the
// rest (instance refs, methods, pointers) so the packet stays valid JSON.
//
// Data-structure references are expanded in place: json_stringify renders a
// ds_list as the text "@ref ds_list(882)", which is a runtime handle that
// means nothing after the kill (MEASURED 2026-09-17: DropItem's arguments 6
// and 7 and the monster's dropTable / exclusiveDrops are such lists). The
// packet must carry the contents, so a list becomes
// {"_ds":"list","items":[...]} and a map {"_ds":"map","entries":[[k,v],...]}.
static std::string Stringify(const RValue& v, int depth = 0);

static bool IsDsRef(const RValue& v, int dsType)
{
    if (v.m_Kind != VALUE_REF && v.m_Kind != VALUE_REAL && v.m_Kind != VALUE_INT32 && v.m_Kind != VALUE_INT64) return false;
    try { return g_Yytk->CallBuiltin("ds_exists", { v, RValue((double)dsType) }).ToBoolean(); } catch (...) { return false; }
}

static std::string StringifyDsList(const RValue& v, int depth)
{
    std::string s = "{\"_ds\": \"list\", \"items\": [";
    int n = (int)g_Yytk->CallBuiltin("ds_list_size", { v }).ToDouble();
    for (int i = 0; i < n && i < 4096; ++i) {
        if (i) s += ", ";
        RValue e = g_Yytk->CallBuiltin("ds_list_find_value", { v, RValue((double)i) });
        s += Stringify(e, depth + 1);
    }
    return s + "]}";
}

static std::string StringifyDsMap(const RValue& v, int depth)
{
    std::string s = "{\"_ds\": \"map\", \"entries\": [";
    RValue keys = g_Yytk->CallBuiltin("ds_map_keys_to_array", { v });
    int n = keys.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { keys }).ToDouble() : 0;
    for (int i = 0; i < n && i < 4096; ++i) {
        if (i) s += ", ";
        RValue k = g_Yytk->CallBuiltin("array_get", { keys, RValue((double)i) });
        RValue e = g_Yytk->CallBuiltin("ds_map_find_value", { v, k });
        s += "[" + Stringify(k, depth + 1) + ", " + Stringify(e, depth + 1) + "]";
    }
    return s + "]}";
}

static std::string Stringify(const RValue& v, int depth)
{
    switch (v.m_Kind) {
    case VALUE_UNDEFINED: return "null";
    case VALUE_BOOL:      return v.ToBoolean() ? "true" : "false";
    case VALUE_REAL: case VALUE_INT32: case VALUE_INT64: {
        char buf[64]; std::snprintf(buf, sizeof buf, "%.17g", v.ToDouble());
        std::string s = buf;
        if (s == "nan" || s == "-nan" || s == "inf" || s == "-inf") return "null";
        return s;
    }
    case VALUE_STRING:    return "\"" + JsonEscape(v.ToString()) + "\"";
    default: break;
    }
    // GML ds type constants: ds_type_map = 1, ds_type_list = 2.
    if (depth < 4) {
        try {
            if (IsDsRef(v, 2)) return StringifyDsList(v, depth);
            if (IsDsRef(v, 1)) return StringifyDsMap(v, depth);
        } catch (...) {}
    }
    try {
        RValue s = g_Yytk->CallBuiltin("json_stringify", { v });
        if (s.m_Kind == VALUE_STRING) {
            std::string t = s.ToString();
            if (!t.empty()) {
                // Belt and braces: if the runner still rendered a data-structure
                // handle, expand it from the text form.
                if (depth < 4 && t.rfind("\"@ref ds_list(", 0) == 0) { try { return StringifyDsList(v, depth); } catch (...) {} }
                if (depth < 4 && t.rfind("\"@ref ds_map(", 0) == 0)  { try { return StringifyDsMap(v, depth); } catch (...) {} }
                return t;
            }
        }
    } catch (...) {}
    return "{\"_kind\": \"" + JsonEscape(KindName(v)) + "\"}";
}

static RValue GetVar(const RValue& inst, const char* name)
{
    try {
        RValue ex = g_Yytk->CallBuiltin("variable_instance_exists", { inst, RValue(name) });
        if (!ex.ToBoolean()) return RValue();
        return g_Yytk->CallBuiltin("variable_instance_get", { inst, RValue(name) });
    } catch (...) { return RValue(); }
}
static std::string GetVarString(const RValue& inst, const char* name)
{
    RValue v = GetVar(inst, name);
    return v.m_Kind == VALUE_STRING ? v.ToString() : std::string();
}
static double GetVarNumber(const RValue& inst, const char* name, double dflt = -1.0)
{
    RValue v = GetVar(inst, name);
    if (v.m_Kind == VALUE_REAL || v.m_Kind == VALUE_INT32 || v.m_Kind == VALUE_INT64 || v.m_Kind == VALUE_BOOL) return v.ToDouble();
    return dflt;
}

static std::string CurrentRoomName()
{
    try {
        RValue room;
        if (AurieSuccess(g_Yytk->GetBuiltin("room", nullptr, NULL_INDEX, room))) {
            RValue n = g_Yytk->CallBuiltin("room_get_name", { room });
            if (n.m_Kind == VALUE_STRING) return n.ToString();
        }
    } catch (...) {}
    return "";
}

static std::string ObjectNameOf(const RValue& inst)
{
    try {
        RValue oi = GetVar(inst, "object_index");
        if (oi.m_Kind == VALUE_UNDEFINED) return "";
        RValue n = g_Yytk->CallBuiltin("object_get_name", { oi });
        if (n.m_Kind == VALUE_STRING) return n.ToString();
    } catch (...) {}
    return "";
}

// The anti-cheat store keeps combat numbers behind a per-instance handle; the
// game's own wrapper turns the handle back into the value (MEASURED 2026-09-17,
// HeroSiege_Wiki_Data/07_oyun_ici/ANTICHEAT_KAPISI.md).
static std::string ResolveProtected(const RValue& handle)
{
    if (!(handle.m_Kind == VALUE_REAL || handle.m_Kind == VALUE_INT32 || handle.m_Kind == VALUE_INT64)) return "";
    try {
        RValue r = g_Yytk->CallGameScript("gml_Script_PC_GetVariableGMLWrapper", { handle });
        if (r.m_Kind == VALUE_REAL || r.m_Kind == VALUE_INT32 || r.m_Kind == VALUE_INT64) return Stringify(r);
    } catch (...) {}
    return "";
}

// -------------------------------------------------------------- capture state
static PFUNC_YYGMLScript g_OrigDropItem = nullptr;
static std::string       g_DropItemHookKind = "not installed";
static PFUNC_YYGMLScript g_OrigCreateItemNew = nullptr;
static std::string       g_CreateItemHookKind = "not installed";
static PFUNC_YYGMLScript g_OrigLootGroundCreate = nullptr;
static std::string       g_LootGroundHookKind = "not installed";
static PFUNC_YYGMLScript g_OrigExperienceUpdate = nullptr;
static std::string       g_ExpUpdateHookKind = "not installed";
static PFUNC_YYGMLScript g_OrigGoldLogAdd = nullptr;
static std::string       g_GoldLogHookKind = "not installed";
// Anti-cheat reports the game raised (ReportClient). Filtered-item sales check
// the count around every gold credit and stop selling at the first report.
static PFUNC_YYGMLScript g_OrigReportClient = nullptr;
static std::string       g_ReportClientHookKind = "not installed";
static std::atomic<uint64_t> g_ReportClientCalls{ 0 };
// Observed through the game's own routines during a replay window: how much
// experience the game credited (ExperienceUpdate arg0) and gold it logged
// (GoldLogAdd arg1).
static uint64_t          g_ExpUpdateCalls = 0;
static double            g_ExpUpdateSum = 0;
static uint64_t          g_GoldLogCalls = 0;
static double            g_GoldLogSum = 0;
static std::string       g_ExpArgsNote, g_GoldArgsNote;
// Spool mode: during a replay, items are written to the spool instead of the
// floor (LootGroundCreate is skipped) and coins are harvested after each call.
static bool              g_SpoolActive = false;
static std::string       g_SpoolPath;
static std::string       g_SpoolId;
static uint64_t          g_SpoolSeq = 0;
static uint64_t          g_SpoolItems = 0;
static double            g_SpoolGold = 0;
static uint64_t          g_SpoolGoldPiles = 0;
static uint64_t          g_LootGroundSkipped = 0;
static std::string       g_LootGroundArgsNote;
static bool              g_GoldPickup = true;     // spool mode: coins are given to the player instead of destroyed
static bool              g_GiveExp = true;        // spool mode: the ghost hands out its experience after the drop
static double            g_PlayerX = 0, g_PlayerY = 0;
static uint64_t          g_GiveExpCalls = 0;
static std::string       g_GiveExpNote;
static double            g_GiveExpSum = 0;   // experience handed to EnemyGiveExperience in the current replay
// Context for item attribution: which drop call (and which packet) an item
// created by the game belongs to. Set around every DropItem invocation we
// see or make; read by the CreateItemNew hook.
static std::string       g_CtxKind = "none";      // none | live | replay | replaylive
static std::string       g_CtxPacket;
static std::atomic<uint64_t> g_ItemsLogged{ 0 };
static std::atomic<bool> g_CaptureOn{ false };
static std::atomic<bool> g_ReplayActive{ false };     // reserved for Phase 1
static bool g_ExpBusy = false;            // an expedition is replaying across frames
static std::atomic<uint64_t> g_DropItemCalls{ 0 };
static std::atomic<uint64_t> g_KillsCaptured{ 0 };
static std::atomic<uint64_t> g_PacketsWritten{ 0 };
static std::atomic<uint64_t> g_CaptureErrors{ 0 };
static std::atomic<uint64_t> g_SnapshotsTaken{ 0 };
static std::mutex g_CaptureMutex;
static std::unordered_map<std::string, std::string> g_IdentityToHash;   // cheap identity -> packet hash
static std::map<std::string, uint64_t> g_KillsByMonster;
static std::string g_SessionFile;
static std::string g_SessionCharacter, g_SessionForgePact;
static bool g_CaptureAll = false;      // "capture on full": snapshot every kill, never dedupe by identity

// Who is playing: the selected save slot's map (GetSelectedSlot, MEASURED
// 2026-09-17: a ds_map with name / class / level among its keys). Used as the
// validity stamp of a calibration and checked again before an expedition.
static CInstance* ResolveInstance(const RValue& ref);
static RValue SlotMap(CInstance* pi, int slot)
{
    // GetSlot(i) / GetSelectedSlot(i) answer save slot i as a ds_map handle (a
    // typed reference on this runner). The argument is the SLOT INDEX, not the
    // local player (MEASURED 2026-09-18: with Sgham in play, slot 0 still
    // answered the slot-0 character).
    RValue m;
    try {
        RValue r;
        if (!AurieSuccess(g_Yytk->CallGameScriptEx(r, "gml_Script_GetSlot", pi, pi, { RValue((double)slot) }))) return m;
        const bool plausible = r.m_Kind == VALUE_REF || (r.m_Kind != VALUE_BOOL && IsNumberKind(r) && r.ToDouble() >= 0);
        if (!plausible) return m;
        bool exists = false;
        try { exists = g_Yytk->CallBuiltin("ds_exists", { r, RValue(1.0) }).ToBoolean(); } catch (...) {}
        if (exists) m = r;
    } catch (...) { m = RValue(); }
    return m;
}
static std::string SlotMapString(const RValue& m, const char* key)
{
    try {
        if (!g_Yytk->CallBuiltin("ds_map_exists", { m, RValue(key) }).ToBoolean()) return "";
        RValue v = g_Yytk->CallBuiltin("ds_map_find_value", { m, RValue(key) });
        return v.m_Kind == VALUE_STRING ? v.ToString() : "";
    } catch (...) { return ""; }
}
static std::string SlotMapNumber(const RValue& m, const char* key)
{
    // numbers in the slot map are anti-cheat handles, read through the wrapper
    try {
        if (!g_Yytk->CallBuiltin("ds_map_exists", { m, RValue(key) }).ToBoolean()) return "null";
        RValue v = g_Yytk->CallBuiltin("ds_map_find_value", { m, RValue(key) });
        if (v.m_Kind == VALUE_STRING) { const std::string t = v.ToString(); if (t.empty()) return "null"; return std::to_string((long long)std::strtod(t.c_str(), nullptr)); }
        if (!IsNumberKind(v)) return "null";
        if (v.ToDouble() > 100000) { std::string r = ResolveProtected(v); return r.empty() ? "null" : r; }
        return std::to_string((long long)v.ToDouble());
    } catch (...) { return "null"; }
}
// Local selection is global.slot[1]. Name search is ambiguous when saves
// share a name; use the name only as a consistency check on that one slot.
static RValue SelectedSlotMap(int* slotOut = nullptr, std::string* nameOut = nullptr)
{
    RValue none;
    if (slotOut) *slotOut = -1;
    try {
        RValue pobj; if (!ObjectIndex("Player_obj", pobj)) return none;
        RValue pid = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
        if (!g_Yytk->CallBuiltin("instance_exists", { pid }).ToBoolean()) return none;
        CInstance* pi = ResolveInstance(pid);
        if (!pi) return none;
        RValue nm = GetVar(pid, "name");
        const std::string name = nm.m_Kind == VALUE_STRING ? nm.ToString() : "";
        if (nameOut) *nameOut = name;
        RValue slots = g_Yytk->CallBuiltin("variable_global_get", { RValue("slot") });
        if (slots.m_Kind != VALUE_ARRAY || g_Yytk->CallBuiltin("array_length", { slots }).ToDouble() <= 1) return none;
        RValue selected = g_Yytk->CallBuiltin("array_get", { slots, RValue(1.0) });
        if (!IsNumberKind(selected) || selected.m_Kind == VALUE_BOOL) return none;
        const int slot = AfkExpedition::LocalSaveSlot({ 0, selected.ToDouble() });
        if (slot < 0 || name.empty()) return none;
        RValue m = SlotMap(pi, slot);
        if (!(IsNumberKind(m) || m.m_Kind == VALUE_REF) || SlotMapString(m, "name") != name) return none;
        if (slotOut) *slotOut = slot;
        return m;
    } catch (...) {}
    return none;
}
static std::string CharacterStampJson()
{
    int slot = -1; std::string name;
    RValue m = SelectedSlotMap(&slot, &name);
    std::string cls = "null", level = "null";
    if (IsNumberKind(m) || m.m_Kind == VALUE_REF) { cls = SlotMapNumber(m, "class"); level = SlotMapNumber(m, "level"); }
    return "{\"identity_version\":2,\"name\":\"" + JsonEscape(name) + "\",\"class\":" + cls + ",\"level\":" + level + ",\"slot\":" + (slot >= 0 ? std::to_string(slot) : std::string("null")) + "}";
}
// The ForgePact panel keeps its settings in %LOCALAPPDATA%\Hero_Siege\forgepact.json
// (density, drop multipliers, stat multipliers ...). Its drop and stat hooks sit
// in the replay path too (MEASURED 2026-09-18: `dropmult item 5` turned 50 items
// per 300 replays into 273), so the settings in force at capture and at claim
// both matter; both moments record them.
static std::string ReadFileText(const std::string& path);
static bool ForgePactLoaded()
{
    // Aurie manually maps plugins; GetModuleHandle alone cannot find them.
    static const bool loaded=[](){
        if(GetModuleHandleW(L"BloodPactPlugin.dll"))return true;
        wchar_t executable[32768]{};GetModuleFileNameW(nullptr,executable,32768);
        const auto path=fs::path(executable).parent_path()/L"mods"/L"aurie"/L"BloodPactPlugin.dll";
        std::error_code error;if(!fs::exists(path,error))return error ? true : false;
        AurieModule* module=nullptr;
        try{return AurieSuccess(Internal::MdpLookupModuleByPath(path,module));}
        catch(...){return true;} // Unknown presence cannot authorize unisolated rewards.
    }();
    return loaded;
}
static std::string ForgePactSettingsJson()
{
    if(!ForgePactLoaded())return "{}";
    try {
        const std::string path = fs::path(DATA_ROOT).parent_path().string() + "\\forgepact.json";
        if (!fs::exists(path)) return "{}"; // absent optional integration = vanilla
        const std::string text = ReadFileText(path);
        if (text.empty()) return "null";
        RValue parsed = g_Yytk->CallBuiltin("json_parse", { RValue(text) });
        if (parsed.m_Kind != VALUE_OBJECT) return "null";
        RValue back = g_Yytk->CallBuiltin("json_stringify", { parsed });
        return back.m_Kind == VALUE_STRING ? back.ToString() : "null";
    } catch (...) { return "null"; }
}
static std::string g_SessionFarmContext;
static std::string RewardBaselineJson();
static std::string NativeKillXpJson(const RValue& instance);
static bool InstallIndependentRewardHooks();
static bool g_FarmClockReady=false;
static std::string CurrentIdentityKey();
#include "FarmContext.inl"

static void SessionOpen()
{
    g_FarmClockReady=false;
    static unsigned serial = 0;
    g_SessionCharacter = CharacterStampJson(); g_SessionForgePact = ForgePactSettingsJson();
    g_SessionFarmContext = FarmContextJson();
    g_SessionFile = SessionsDir() + "\\capture_" + NowStamp() + "_" + std::to_string(GetCurrentProcessId()) + "_" + std::to_string(++serial) + ".ndjson";
    std::ofstream f(g_SessionFile, std::ios::app);
    f << "{\"kind\":\"session_start\",\"t\":\"" << NowIso() << "\",\"build\":\"" << JsonEscape(GAME_BUILD_ID)
      << "\",\"room\":\"" << JsonEscape(CurrentRoomName()) << "\",\"plugin\":\"" AFK_EXPEDITION_VERSION "\",\"character\":" << g_SessionCharacter
      << ",\"forgepact\":" << g_SessionForgePact << ",\"farm_context\":" << g_SessionFarmContext
      << ",\"reward_baseline\":" << RewardBaselineJson() << "}\n";
}

static void SessionAppend(const std::string& line)
{
    if (g_SessionFile.empty()) return;
    std::ofstream f(g_SessionFile, std::ios::app);
    f << line << "\n";
}

// Snapshot every instance variable of the monster as name -> JSON text.
static std::vector<std::pair<std::string, std::string>> SnapshotSelf(const RValue& inst)
{
    std::vector<std::pair<std::string, std::string>> out;
    RValue names = g_Yytk->CallBuiltin("variable_instance_get_names", { inst });
    if (names.m_Kind != VALUE_ARRAY) return out;
    int n = (int)g_Yytk->CallBuiltin("array_length", { names }).ToDouble();
    out.reserve(n);
    for (int i = 0; i < n; ++i) {
        RValue nm = g_Yytk->CallBuiltin("array_get", { names, RValue((double)i) });
        if (nm.m_Kind != VALUE_STRING) continue;
        std::string name = nm.ToString();
        std::string js;
        try {
            RValue v = g_Yytk->CallBuiltin("variable_instance_get", { inst, nm });
            js = Stringify(v);
        } catch (...) { js = "{\"_kind\": \"error\"}"; }
        out.emplace_back(std::move(name), std::move(js));
    }
    return out;
}

static RValue StructGet(const RValue& st,const char* name);

static std::string CaptureKill(const RValue& inst, const std::vector<RValue>& argv, bool synthetic);

static std::string CaptureKill(CInstance* S, int argc, RValue** A)
{
    RValue inst = S->ToRValue();
    std::vector<RValue> argv; argv.reserve(argc);
    for (int i = 0; i < argc; ++i) argv.push_back(A && A[i] ? *A[i] : RValue());
    return CaptureKill(inst, argv, false);
}

// Build the drop-call arguments a live monster would receive, from its own
// variables. MEASURED 2026-09-17 on 35 real packets: arg0 = rank, arg1 = 0,
// arg2/3 = x/y, arg4 = 1, arg5 = 0 (occasionally 500/3500: a per-kill bonus
// not derivable here), arg6 = dropTable, arg7 = exclusiveDrops, arg8..11 = 0.
static std::vector<RValue> SyntheticDropArgs(const RValue& inst)
{
    std::vector<RValue> a(12);
    a[0] = RValue(GetVarNumber(inst, "enemyRarity", 1.0));
    a[1] = RValue(0.0);
    a[2] = RValue(GetVarNumber(inst, "x", 0.0));
    a[3] = RValue(GetVarNumber(inst, "y", 0.0));
    a[4] = RValue(1.0);
    // arg5 is the player's magic find at the kill (325 on a real packet next
    // to extraMagicFind = 325). The monster carries it as a protected value.
    double mf = 0;
    { std::string r = ResolveProtected(GetVar(inst, "extraMagicFind")); if (!r.empty()) mf = std::strtod(r.c_str(), nullptr); }
    a[5] = RValue(mf);
    a[6] = GetVar(inst, "dropTable");
    a[7] = GetVar(inst, "exclusiveDrops");
    for (int i = 8; i < 12; ++i) a[i] = RValue(0.0);
    return a;
}

static std::string CaptureKill(const RValue& inst, const std::vector<RValue>& argv, bool synthetic)
{
    // Check before attributing a kill, including the first kill after loading
    // a different character in the same process.
    if (g_SessionFile.empty()) SessionOpen();
    if (CharacterStampJson() != g_SessionCharacter) {
        // Transient no-player states must not split a user-owned recording.
        if(CurrentIdentityKey().empty()) return "";
        SessionAppend("{\"kind\":\"context_invalid\",\"reason\":\"character_changed\",\"t\":\""+NowIso()+"\"}");
        g_CaptureOn=false;return "";
    }
    const int argc = (int)argv.size();
    const std::string nameKey = GetVarString(inst, "nameKey");
    const int rank = (int)GetVarNumber(inst, "enemyRarity", 0.0);
    const std::string room = CurrentRoomName();

    // Cheap identity: what makes two kills "the same drop situation" without a
    // full snapshot. The full packet hash is authoritative; this only decides
    // whether the (expensive) snapshot is worth taking again.
    std::string identity = nameKey + "|" + std::to_string(rank) + "|" + room + "|";
    try { identity += Stringify(GetVar(inst, "dList")) + "|" + Stringify(GetVar(inst, "dropTable")) + "|" + Stringify(GetVar(inst, "enemyType")); } catch (...) {}
    for (const char* nm : { "dSlots", "dCommonChance", "dCommonDropMult", "dSatanicDropMult" }) {
        try { RValue h = GetVar(inst, nm); identity += std::string("|") + nm + "=" + (IsNumberKind(h) ? ResolveProtected(h) : std::string("?")); } catch (...) {}
    }
    // The drop call's 6th argument is the monster's own extraMagicFind (207 of
    // 215 packets, MEASURED 2026-09-18) and champions carry 175-3575 there;
    // the kill's experience differs the same way. Both belong to the identity,
    // otherwise a bonus kill is filed under the first plain kill of its kind
    // and the profile under-counts the kills that drop best.
    try {
        RValue mf = GetVar(inst, "extraMagicFind"); std::string mfs = IsNumberKind(mf) ? ResolveProtected(mf) : "";
        identity += "|mf" + mfs;
    } catch (...) {}

    std::string hash;
    {
        std::lock_guard<std::mutex> lk(g_CaptureMutex);
        auto it = g_IdentityToHash.find(identity);
        if (it != g_IdentityToHash.end() && !g_CaptureAll) hash = it->second;
    }

    RValue expRaw = GetVar(inst, "experience");
    std::string expRawJs = Stringify(expRaw);
    std::string expResolved = ResolveProtected(expRaw);

    if (hash.empty()) {
        AfkExpedition::PacketInput in;
        in.script = synthetic ? "gml_Script_DropItem#synthetic-args" : "gml_Script_DropItem";
        in.gameBuildId = GAME_BUILD_ID;
        in.room = room;
        in.selfObject = ObjectNameOf(inst);
        in.monsterKey = nameKey;
        in.rank = rank;
        in.capturedAt = NowIso();
        in.expRaw = expRawJs;
        in.expResolved = expResolved;
        for (int i = 0; i < argc; ++i) in.args.push_back(Stringify(argv[i]));
        in.selfVars = SnapshotSelf(inst);
        ++g_SnapshotsTaken;

        // Protected variables. The anti-cheat store hands each instance a block
        // of consecutive handles (MEASURED 2026-09-17: 171065-171069 and
        // 171294-171320 on one monster). Only numbers near the handles we know
        // to be handles are resolved: the wrapper returns 0 for a freed
        // neighbour but crashes the game on a far-away nonsense id.
        {
            double lo = 0, hi = 0; bool have = false;
            for (const char* anchor : { "experience", "max_hp", "damage", "enemy_hp" }) {
                RValue h = GetVar(inst, anchor);
                if (h.m_Kind == VALUE_REAL || h.m_Kind == VALUE_INT32 || h.m_Kind == VALUE_INT64) {
                    double d = h.ToDouble();
                    if (d < 1000) continue;
                    if (!have) { lo = hi = d; have = true; } else { lo = d < lo ? d : lo; hi = d > hi ? d : hi; }
                }
            }
            // The names below are protected on every enemy (union over 317
            // packets, MEASURED 2026-09-18); their handles can sit 75 000
            // below or 600 000 above the anchors, so a window alone missed the
            // drop-critical ones on 6 % of the packets. A live instance's own
            // handle is always valid, so these are resolved by name; the window
            // still catches names not listed here.
            static const char* const kProtectedNames[] = {
                "enemy_hp", "damage", "antiSocialMf", "experience", "max_hp", "damageTaken", "damageTakenTimer",
                "etherPeChaosKey", "satanicZoneRelic", "satanicZoneRelicFeast", "maxHpUnscaled", "currentHpPercentage",
                "damageDealer", "etherEbKeyChance", "etherSrKeyChance", "etherUrAncientOreGoblin", "xPos", "yPos",
                "etherOwAncientChest", "etherOwGoblins", "isGoblin", "etherSrShadowGoblins", "extraGold", "extraMagicFind",
                "lootAmount", "killExperience", "killSpawnGrave", "killStatistic", "dSatanicDropMult", "dCommonChance",
                "dCommonDropMult", "dSlots", "goblinGOLD", "goblinMF" };
            std::set<std::string> done;
            for (const char* nm : kProtectedNames) {
                RValue h = GetVar(inst, nm);
                if (!(h.m_Kind == VALUE_REAL || h.m_Kind == VALUE_INT32 || h.m_Kind == VALUE_INT64)) continue;
                const double d = h.ToDouble();
                if (d < 1000 || d > 50000000 || d != std::floor(d)) continue;
                std::string r = ResolveProtected(h);
                if (!r.empty()) { in.protectedVars.emplace_back(nm, r); done.insert(nm); }
            }
            if (have) {
                lo -= 2048; hi += 2048;
                for (const auto& kv : in.selfVars) {
                    if (done.count(kv.first)) continue;
                    if (kv.second.empty() || !(std::isdigit((unsigned char)kv.second[0]))) continue;
                    if (kv.second.find_first_of(".eE") != std::string::npos) continue;   // handles are whole numbers
                    double d = std::strtod(kv.second.c_str(), nullptr);
                    if (d < lo || d > hi) continue;
                    RValue h = GetVar(inst, kv.first.c_str());
                    std::string r = ResolveProtected(h);
                    if (!r.empty()) in.protectedVars.emplace_back(kv.first, r);
                }
            }
        }
        hash = AfkExpedition::HashOf(in);
        const std::string path = PacketsDir() + "\\" + hash + ".json";
        std::error_code ec;
        if (!fs::exists(path, ec)) {
            std::ofstream f(path, std::ios::binary);
            f << AfkExpedition::ToJson(in, hash);
            ++g_PacketsWritten;
        }
        std::lock_guard<std::mutex> lk(g_CaptureMutex);
        g_IdentityToHash[identity] = hash;
    }

    // A destructible (hay bale, crate) runs the same drop routine but is not a
    // kill: no name key, no rarity, no experience (MEASURED 2026-09-17).
    const bool isEnemy = !nameKey.empty() || GetVar(inst, "enemyRarity").m_Kind != VALUE_UNDEFINED;
    const std::string label = isEnemy ? (nameKey.empty() ? std::string("?") : nameKey) : ("break:" + ObjectNameOf(inst));
    {
        std::lock_guard<std::mutex> lk(g_CaptureMutex);
        ++g_KillsByMonster[label];
    }
    ++g_KillsCaptured;
    if (synthetic) { Out("snapshot: " + label + " -> packet " + hash.substr(0, 12) + " (synthetic args)"); return hash; }
    SessionAppend(std::string("{\"kind\":\"") + (isEnemy ? "kill" : "break") + "\",\"t\":\"" + NowIso() + "\",\"monster_key\":\"" + JsonEscape(nameKey)
        + "\",\"object\":\"" + JsonEscape(ObjectNameOf(inst)) + "\",\"rank\":" + std::to_string(rank) + ",\"room\":\"" + JsonEscape(room)
        + "\",\"packet\":\"" + hash + "\",\"exp\":" + (expResolved.empty() ? std::string("null") : expResolved)
        + ",\"native_kill_exp\":" + NativeKillXpJson(inst)
        + ",\"kill_exp\":" + [&]{ try { RValue k = GetVar(inst, "killExperience"); std::string r = IsNumberKind(k) ? ResolveProtected(k) : ""; return r.empty() ? std::string("null") : r; } catch (...) { return std::string("null"); } }() + "}");
    return hash;
}

namespace DefenseLab {   // research R2 (DefenseLab.inl)
static bool OnDropItem(CInstance* self, int argc, RValue** args);
static void EndDrop();
static void OnCreateItem();
}
static RValue& Hook_DropItem(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    ++g_DropItemCalls;
    if (DefenseLab::OnDropItem(S, argc, A)) return R;   // the lab already dropped for this tower kill
    struct LabDropScope { ~LabDropScope() { DefenseLab::EndDrop(); } } labDropScope;
    if (g_ReplayActive.load()) {
        // our own replay call: context was set by the caller
        if (g_OrigDropItem) return g_OrigDropItem(S, O, R, argc, A);
        return R;
    }
    std::string hash;
    if (g_CaptureOn.load() && S) {
        try { hash = CaptureKill(S, argc, A); }
        catch (...) { ++g_CaptureErrors; }
    }
    g_CtxKind = g_CaptureOn.load() ? "live" : "none"; g_CtxPacket = hash;
    RValue* r = &R;
    if (g_OrigDropItem) r = &g_OrigDropItem(S, O, R, argc, A);
    g_CtxKind = "none"; g_CtxPacket.clear();
    return *r;
}

// Every item the game builds passes through CreateItemNew; the finished item
// struct is the return value (it carries itemDefinitionStruct / itemInfoStruct /
// itemStatStruct, the same shape ForgePact logs). Attribute it to the drop call
// in progress so live kills and replays can be compared item by item.
static std::string g_PendingSpoolItem;      // spool record of the item just built, waiting for its floor object
static uint64_t    g_SpoolFiltered = 0;     // items the player's loot filter hides
static uint64_t    g_SpoolUnplaced = 0;     // items whose floor object never came back
// One open, buffered stream per spool instead of opening and closing the file
// for every item. SpoolFlush runs after each replay frame and before every
// checkpoint, so a checkpoint never counts items that are not on disk.
// Filtered items during delivery (AfkExpedition/Conversion.hpp): what the plan
// chose, which paths passed their start checks, and what was sold or broken down.
struct ConversionState {
    bool enabled = false, sell = false, prospect = false;
    std::string note;
    std::vector<AfkExpedition::ProspectRecipe> recipes;
    uint64_t soldItems = 0, prospectedItems = 0, keptItems = 0, outputStacks = 0, creditFailures = 0;
    double sellGold = 0, sellPending = 0;
    std::vector<std::string> soldLines;          // this frame's sales, written back if the credit fails
    std::map<std::string, long long> pending;    // fragments gathered but not created yet, by "type:id"
    std::map<std::string, long long> created;    // fragments delivered as stacks, by "type:id"
};
static ConversionState g_Conv;
static AfkExpedition::ItemFacts g_PendingFacts;   // facts of the item in g_PendingSpoolItem
static bool g_CreatingOutput = false;             // one of our fragment stacks is being built
static std::string g_OutputSource = "prospect";  // what the stacks being built are: prospect | worker
static std::string ConversionJson();
static std::ofstream g_SpoolStream;
static void SpoolWriteLine(const std::string& line)
{
    if (!g_SpoolStream.is_open()) g_SpoolStream.open(g_SpoolPath, std::ios::app);
    g_SpoolStream << line << "\n";
}
static void SpoolFlush() { if (g_SpoolStream.is_open()) g_SpoolStream.flush(); }
static void SpoolClose() { if (g_SpoolStream.is_open()) { g_SpoolStream.flush(); g_SpoolStream.close(); } g_SpoolStream.clear(); }
// A record gets its sequence number when it is written, so an item that a sale
// or a break-down replaces never leaves a gap in the spool.
static std::string SpoolRecord(const std::string& body)
{
    return "{\"expedition_id\":\"" + JsonEscape(g_SpoolId) + "\",\"seq\":" + std::to_string(++g_SpoolSeq) + "," + body + "}";
}
static void FlushPendingSpoolItem(const std::string& extraFields)
{
    if (g_PendingSpoolItem.empty()) return;
    SpoolWriteLine(SpoolRecord(g_PendingSpoolItem + extraFields));
    g_PendingSpoolItem.clear();
    g_PendingFacts = AfkExpedition::ItemFacts{};
    ++g_SpoolItems;
}
// The fields a sale or a break-down needs, read from the finished item struct.
static AfkExpedition::ItemFacts ReadItemFacts(const RValue& item, const RValue& type, const RValue& info)
{
    AfkExpedition::ItemFacts f;
    try {
        if (!IsNumberKind(type) || info.m_Kind != VALUE_OBJECT) return f;
        RValue def = g_Yytk->CallBuiltin("variable_struct_get", { item, RValue("itemDefinitionStruct") });
        if (def.m_Kind != VALUE_OBJECT) return f;
        auto number = [](const RValue& st, const char* key, double fallback) {
            RValue v = g_Yytk->CallBuiltin("variable_struct_get", { st, RValue(key) });
            return IsNumberKind(v) && v.m_Kind != VALUE_BOOL ? v.ToDouble() : fallback;
        };
        const double rarity = number(info, "27", -1), tier = number(info, "32", -1);
        if (rarity < 0 || tier < 0) return f;
        f.type = static_cast<int>(type.ToDouble()); f.rarity = static_cast<int>(rarity); f.tier = static_cast<int>(tier);
        f.value = number(info, "9", 0); f.stack = number(def, "o", 1); f.baseId = static_cast<int>(number(def, "b", -1));
        RValue corrupted = g_Yytk->CallBuiltin("variable_struct_get", { def, RValue("r") });
        f.corrupted = IsNumberKind(corrupted) && corrupted.ToBoolean();
        f.valid = true;
    } catch (...) { f = AfkExpedition::ItemFacts{}; }
    return f;
}
static RValue& Hook_CreateItemNew(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    RValue* r = &R;
    if (g_OrigCreateItemNew) r = &g_OrigCreateItemNew(S, O, R, argc, A);
    DefenseLab::OnCreateItem();
    if (g_CtxKind != "none") {
        try {
            const RValue& it = *r;
            if (it.m_Kind == VALUE_OBJECT && g_Yytk->CallBuiltin("variable_struct_exists", { it, RValue("itemDefinitionStruct") }).ToBoolean()) {
                RValue type = g_Yytk->CallBuiltin("variable_struct_get", { it, RValue("itemType") });
                RValue info = g_Yytk->CallBuiltin("variable_struct_get", { it, RValue("itemInfoStruct") });
                std::string name, tname, rarity = "null";
                if (info.m_Kind == VALUE_OBJECT) {
                    RValue n = g_Yytk->CallBuiltin("variable_struct_get", { info, RValue("28") });
                    RValue t = g_Yytk->CallBuiltin("variable_struct_get", { info, RValue("14") });
                    RValue q = g_Yytk->CallBuiltin("variable_struct_get", { info, RValue("27") });
                    if (n.m_Kind == VALUE_STRING) name = n.ToString();
                    if (t.m_Kind == VALUE_STRING) tname = t.ToString();
                    if (IsNumberKind(q)) rarity = Stringify(q);
                }
                ++g_ItemsLogged;
                // A claim's items already go to its spool in full. Logging each
                // one again into the last calibration recording doubled the
                // per-item work and grew that file by tens of MB per claim.
                if (!g_ExpBusy) {
                    RValue def = g_Yytk->CallBuiltin("variable_struct_get", { it, RValue("itemDefinitionStruct") });
                    std::string defjs = def.m_Kind == VALUE_OBJECT ? Stringify(def) : "null";
                    SessionAppend("{\"kind\":\"item\",\"t\":\"" + NowIso() + "\",\"ctx\":\"" + g_CtxKind + "\",\"packet\":\"" + g_CtxPacket
                        + "\",\"type\":" + (IsNumberKind(type) ? Stringify(type) : std::string("null")) + ",\"tname\":\"" + JsonEscape(tname)
                        + "\",\"name\":\"" + JsonEscape(name) + "\",\"rarity\":" + rarity + ",\"def\":" + defjs + "}");
                }
                // Spool: the complete native item struct, exactly as the game
                // built it (the shape a stash entry stores), one record per item.
                if (g_SpoolActive && g_CtxKind == "replay") {
                    // Written by the LootGroundCreate hook once the floor object
                    // exists: its Create evaluates the player's own loot filter
                    // (lootFilterVisible / lootFilterHighlight, MEASURED
                    // 2026-09-18 on a live floor item), and that verdict goes
                    // into the record so the Vault ingest can drop the junk.
                    FlushPendingSpoolItem("");           // an earlier item still pending: no floor object came back for it
                    std::ostringstream o;
                    o << "\"kind\":\"item\",\"t\":\"" << NowIso() << "\",\"packet\":\"" << g_CtxPacket
                      << "\",\"type\":" << (IsNumberKind(type) ? Stringify(type) : std::string("null"))
                      << ",\"name\":\"" << JsonEscape(name) << "\",\"item\":" << Stringify(it);
                    g_PendingSpoolItem = o.str();
                    if (g_Conv.enabled && !g_CreatingOutput) g_PendingFacts = ReadItemFacts(it, type, info);
                }
            }
        } catch (...) {}
    }
    return *r;
}

// Ground placement of a dropped item. MEASURED 2026-09-17: the item itself is
// built INSIDE this call (LootGroundCreate(x, y, z, definition-struct, bool,
// undefined) creates the floor object whose Create runs CreateItemNew), so it
// must run. In spool mode the item goes to the spool from the CreateItemNew
// hook during the call, and the floor object the call returns is removed
// right away. Outside replays the call is untouched.
static int g_EventDepth = 0;   // >0 while a reward routine (LootExplosion, WormholeGiveReward ...) is running
// The game's own irandom(n): 0..n inclusive.
static int GameRandom(int n)
{
    if (n <= 0) return 0;
    try { return static_cast<int>(g_Yytk->CallBuiltin("irandom", { RValue(static_cast<double>(n)) }).ToDouble()); }
    catch (...) { return 0; }
}
// A hidden item the plan converts. Below Satanic: its sale joins this frame's
// gold credit and its record waits until the credit succeeds. Satanic and
// above: broken down unit by unit with the game's recipe and random numbers,
// the fragments gathered for full stacks. Returns false to keep the record.
static bool ConvertPendingItem(const std::string& extra)
{
    if (!g_Conv.enabled || g_PendingSpoolItem.empty() || !g_PendingFacts.valid) return false;
    using AfkExpedition::Conversion;
    const Conversion c = AfkExpedition::DecideConversion(g_PendingFacts, g_Conv.recipes, g_Conv.sell, g_Conv.prospect);
    if (c == Conversion::Sell) {
        g_Conv.sellPending += AfkExpedition::SellGold(g_PendingFacts);
        g_Conv.soldLines.push_back(g_PendingSpoolItem + extra);
    } else if (c == Conversion::Prospect) {
        const AfkExpedition::ProspectRecipe* recipe = AfkExpedition::FindProspectRecipe(g_PendingFacts, g_Conv.recipes);
        const long long units = std::max<long long>(1, static_cast<long long>(g_PendingFacts.stack));
        for (long long u = 0; u < units; ++u) {
            int type = -1, id = -1; long long amount = 0;
            if (AfkExpedition::ProspectYield(*recipe, GameRandom(99), [](int n) { return GameRandom(n - 1); }, type, id, amount))
                g_Conv.pending[AfkExpedition::OutputKey(type, id)] += amount;
        }
        ++g_Conv.prospectedItems;
    } else {
        if (g_PendingFacts.rarity >= AfkExpedition::kSatanicRarity) ++g_Conv.keptItems;
        return false;
    }
    g_PendingSpoolItem.clear();
    g_PendingFacts = AfkExpedition::ItemFacts{};
    return true;
}
static RValue& Hook_LootGroundCreate(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    if (g_ReplayActive.load() && g_SpoolActive && g_LootGroundArgsNote.empty()) {
        std::string n = "argc=" + std::to_string(argc);
        for (int i = 0; i < argc && i < 8; ++i) n += " a" + std::to_string(i) + ":" + (A && A[i] ? KindName(*A[i]) : std::string("?"));
        g_LootGroundArgsNote = n;
    }
    // A ground item that no kill produced (Chaos Tower reward item, battle
    // fragments, quest hand-outs): listed as an event with its definition
    // struct so it can be reproduced with the very same call.
    if (g_CaptureOn.load() && !g_ReplayActive.load() && g_CtxKind != "live" && g_CtxKind != "replay" && g_EventDepth == 0) {
        try {
            std::string line = std::string("{\"kind\":\"event\",\"t\":\"") + NowIso() + "\",\"script\":\"LootGroundCreate\",\"self\":\""
                + JsonEscape(S ? ObjectNameOf(S->ToRValue()) : std::string("null")) + "\",\"room\":\"" + JsonEscape(CurrentRoomName())
                + "\",\"inside\":\"" + JsonEscape(g_CtxKind == "live_event" ? g_CtxPacket : std::string("")) + "\",\"argc\":" + std::to_string(argc) + ",\"args\":[";
            for (int i = 0; i < argc && i < 8; ++i) {
                if (i) line += ",";
                line += "{\"kind\":\"" + (A && A[i] ? KindName(*A[i]) : std::string("?")) + "\",\"value\":\"" + JsonEscape(A && A[i] ? Stringify(*A[i]).substr(0, 1200) : std::string("?")) + "\"}";
            }
            line += "]}";
            SessionAppend(line);
        } catch (...) {}
    }
    RValue* r = &R;
    const bool stray = g_CaptureOn.load() && !g_ReplayActive.load() && g_CtxKind == "none";
    std::string prevKindLG, prevPacketLG;
    if (stray) { prevKindLG = g_CtxKind; prevPacketLG = g_CtxPacket; g_CtxKind = "live_event"; g_CtxPacket = "LootGroundCreate"; }
    if (g_OrigLootGroundCreate) r = &g_OrigLootGroundCreate(S, O, R, argc, A);
    if (stray) { g_CtxKind = prevKindLG; g_CtxPacket = prevPacketLG; }
    if (g_ReplayActive.load() && g_SpoolActive) {
        try {
            if ((r->m_Kind == VALUE_REF || IsNumberKind(*r)) && g_Yytk->CallBuiltin("instance_exists", { *r }).ToBoolean()) {
                std::string extra;
                bool hidden = false;
                try {
                    RValue vis = GetVar(*r, "lootFilterVisible"), hi = GetVar(*r, "lootFilterHighlight"), skip = GetVar(*r, "skipLootFilter");
                    const bool visible = (vis.m_Kind == VALUE_BOOL || IsNumberKind(vis)) ? vis.ToBoolean() : true;
                    const bool skipped = (skip.m_Kind == VALUE_BOOL || IsNumberKind(skip)) && skip.ToBoolean();
                    hidden = !(visible || skipped) && !g_CreatingOutput;
                    extra = std::string(",\"placed\":true,\"filter_visible\":") + (hidden ? "false" : "true")
                          + ",\"filter_highlight\":" + (((hi.m_Kind == VALUE_BOOL || IsNumberKind(hi)) && hi.ToBoolean()) ? "true" : "false");
                } catch (...) { extra = ",\"placed\":true"; }
                // Fragments the player chose are never filtered away again.
                if (g_CreatingOutput) extra += ",\"source\":\"" + g_OutputSource + "\"";
                if (!(hidden && ConvertPendingItem(extra))) {
                    if (hidden) ++g_SpoolFiltered;
                    FlushPendingSpoolItem(extra);
                }
                g_Yytk->CallBuiltin("instance_destroy", { *r });
                ++g_LootGroundSkipped;
            } else if (!g_PendingSpoolItem.empty()) {
                // the game found no free spot: the item exists but never reached the floor
                ++g_SpoolUnplaced;
                FlushPendingSpoolItem(",\"placed\":false");
            }
        } catch (...) {}
    }
    return *r;
}

// Observers on the experience / gold routines (chained detours; pass-through).
// Every call is remembered in a small ring (who was self, which arguments) so
// the real calling convention can be read off live play with `afk lastcalls`.
static std::mutex g_CallLogMutex;
static std::deque<std::string> g_CallLog;
static void RememberCall(const char* what, CInstance* S, int argc, RValue** A, const RValue* result)
{
    try {
        std::string s = std::string(what) + " self=";
        try { RValue inst = S ? S->ToRValue() : RValue(); s += S ? ObjectNameOf(inst) : std::string("null"); } catch (...) { s += "?"; }
        s += " argc=" + std::to_string(argc);
        for (int i = 0; i < argc && i < 6; ++i) s += " a" + std::to_string(i) + "=" + (A && A[i] ? Stringify(*A[i]).substr(0, 60) : std::string("?"));
        if (result) s += " -> " + Stringify(*result).substr(0, 40);
        s += " ctx=" + g_CtxKind;
        std::lock_guard<std::mutex> lk(g_CallLogMutex);
        g_CallLog.push_back(s); if (g_CallLog.size() > 24) g_CallLog.pop_front();
    } catch (...) {}
}
static RValue& Hook_ExperienceUpdate(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    if (g_ReplayActive.load() || g_SpoolActive) {
        ++g_ExpUpdateCalls;
        if (argc > 0 && A && A[0] && IsNumberKind(*A[0])) g_ExpUpdateSum += A[0]->ToDouble();
        if (g_ExpArgsNote.empty()) { g_ExpArgsNote = "argc=" + std::to_string(argc); for (int i = 0; i < argc && i < 6; ++i) g_ExpArgsNote += " a" + std::to_string(i) + "=" + (A && A[i] ? Stringify(*A[i]).substr(0, 40) : std::string("?")); }
    }
    RValue* r = &R;
    if (g_OrigExperienceUpdate) r = &g_OrigExperienceUpdate(S, O, R, argc, A);
    RememberCall("ExperienceUpdate", S, argc, A, r);
    return *r;
}
static RValue& Hook_ReportClient(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    ++g_ReportClientCalls;
    RValue* r = &R;
    if (g_OrigReportClient) r = &g_OrigReportClient(S, O, R, argc, A);
    return *r;
}
static RValue& Hook_GoldLogAdd(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    if (g_ReplayActive.load() || g_SpoolActive) {
        ++g_GoldLogCalls;
        if (argc > 1 && A && A[1] && IsNumberKind(*A[1])) g_GoldLogSum += A[1]->ToDouble();
        if (g_GoldArgsNote.empty()) { g_GoldArgsNote = "argc=" + std::to_string(argc); for (int i = 0; i < argc && i < 6; ++i) g_GoldArgsNote += " a" + std::to_string(i) + "=" + (A && A[i] ? Stringify(*A[i]).substr(0, 40) : std::string("?")); }
    }
    RValue* r = &R;
    if (g_OrigGoldLogAdd) r = &g_OrigGoldLogAdd(S, O, R, argc, A);
    RememberCall("GoldLogAdd", S, argc, A, r);
    return *r;
}

// Coins are Coin_obj instances made by the gold drop path; their amount is
// the anti-cheat protected `goldValue` (MEASURED 2026-09-17 with `afk vars
// Coin_obj`). Resolve it while the coin is alive, then remove the coin.
static std::unordered_set<double> g_HarvestedCoins;   // instance ids already counted (they stay alive when handed to the player)
static void HarvestCoins(int coinBefore)
{
    try {
        RValue oi;
        if (!ObjectIndex("Coin_obj", oi)) return;
        int n = (int)g_Yytk->CallBuiltin("instance_number", { oi }).ToDouble();
        for (int i = n - 1; i >= coinBefore && i >= 0; --i) {
            RValue id = g_Yytk->CallBuiltin("instance_find", { oi, RValue((double)i) });
            const double idnum = g_Yytk->CallBuiltin("variable_instance_get", { id, RValue("id") }).ToDouble();
            if (g_HarvestedCoins.count(idnum)) continue;
            g_HarvestedCoins.insert(idnum);
            double amt = 0;
            std::string r = ResolveProtected(GetVar(id, "goldValue"));
            if (!r.empty()) amt = std::strtod(r.c_str(), nullptr);
            if (amt <= 0) {
                // fallback: the draw text "N Gold"
                RValue dd = GetVar(id, "lootDrawData");
                if (dd.m_Kind == VALUE_OBJECT) {
                    RValue t = g_Yytk->CallBuiltin("variable_struct_get", { dd, RValue("txtName") });
                    if (t.m_Kind == VALUE_STRING) amt = std::strtod(t.ToString().c_str(), nullptr);
                }
            }
            if (amt < 0) amt = 0;
            g_SpoolGold += amt; ++g_SpoolGoldPiles;
            if (g_GoldPickup) {
                // Hand the coin to the player: put it on top of them and let the
                // game's own pickup collect it (the same path as a live kill).
                g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("x"), RValue(g_PlayerX) });
                g_Yytk->CallBuiltin("variable_instance_set", { id, RValue("y"), RValue(g_PlayerY) });
            } else {
                g_Yytk->CallBuiltin("instance_destroy", { id });
            }
        }
    } catch (...) {}
}

// ------------------------------------------ resolve a YYC function by name
// The script table is a shared, mutable resource: another plugin may already
// have swapped an entry (MEASURED 2026-09-17: ForgePact's research build holds
// DropItem's), and then the entry no longer says where the game's function is.
// Compiled GML also calls scripts directly, so a table swap would not see the
// monster death path anyway. The function's own entry region loads its
// "gml_Script_<name>" string with a
// rip-relative lea. Find the string, find that lea in executable code, ask the
// unwinder (.pdata) which function contains it. Resolved by name at runtime on
// the running build; no address is stored anywhere (hub AGENTS.md, "Never Call
// an Address You Resolved by Hand").
struct SectionSpan { const uint8_t* begin; const uint8_t* end; bool exec; bool read; };

static std::vector<SectionSpan> ModuleSections(HMODULE mod)
{
    std::vector<SectionSpan> out;
    auto* base = reinterpret_cast<const uint8_t*>(mod);
    auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return out;
    auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return out;
    const size_t imageSize = nt->OptionalHeader.SizeOfImage;
    const IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++sec) {
        if (sec->Misc.VirtualSize == 0 || sec->VirtualAddress > imageSize ||
            sec->Misc.VirtualSize > imageSize-sec->VirtualAddress) continue;
        SectionSpan s;
        s.begin = base + sec->VirtualAddress;
        s.end = s.begin + sec->Misc.VirtualSize;
        s.exec = (sec->Characteristics & IMAGE_SCN_MEM_EXECUTE) != 0;
        s.read = (sec->Characteristics & IMAGE_SCN_MEM_READ) != 0;
        // Section flags do not guarantee that every mapped page is readable.
        for (auto at=s.begin;s.read && at<s.end;) {
            MEMORY_BASIC_INFORMATION memory{};
            const DWORD readable=PAGE_READONLY|PAGE_READWRITE|PAGE_WRITECOPY|
                PAGE_EXECUTE_READ|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY;
            if (VirtualQuery(at,&memory,sizeof(memory))!=sizeof(memory) ||
                memory.State!=MEM_COMMIT || memory.AllocationBase!=mod ||
                (memory.Protect&(PAGE_GUARD|PAGE_NOACCESS)) || !(memory.Protect&readable)) {
                s.read=false; break;
            }
            const auto end=static_cast<const uint8_t*>(memory.BaseAddress)+memory.RegionSize;
            if (end<=at) { s.read=false; break; }
            at=(std::min)(end,s.end);
        }
        out.push_back(s);
    }
    return out;
}

static uintptr_t ResolveYycFunctionByName(const char* fullName, std::string& note)
{
    HMODULE module = GetModuleHandleA(nullptr);
    std::vector<HeroSiege::Hooks::NativeNameSection> sections;
    for (const auto& section : ModuleSections(module)) {
        sections.push_back({section.begin, static_cast<size_t>(section.end-section.begin), section.exec, section.read});
    }
    return HeroSiege::Hooks::ResolveNativeName(sections, fullName,
        [module](uintptr_t reference) -> HeroSiege::Hooks::NativeFunctionRange {
            DWORD64 imageBase = 0;
            const auto function = RtlLookupFunctionEntry(reference, &imageBase, nullptr);
            if (!function || imageBase != reinterpret_cast<uintptr_t>(module)) return {};
            return {imageBase+function->BeginAddress, imageBase+function->EndAddress};
        }, note);
}

// Install an inline detour at the function the game actually runs (resolved by
// name, §"resolve a YYC function by name"). Covers direct compiled-GML calls.
// A foreign table owner may forward through its own trampoline and bypass this
// entry, so leaving the table untouched does not certify table-call coverage.
// Falls back to the SDK's table+detour installer.
static bool InstallScriptDetour(const char* shortName, const char* hookId, PFUNC_YYGMLScript hook, PFUNC_YYGMLScript* orig, std::string& kindOut)
{
    if (*orig) { return kindOut.rfind("native", 0) == 0; }
    const std::string full = std::string("gml_Script_") + shortName;
    std::string note;
    const uintptr_t fn = ResolveYycFunctionByName(full.c_str(), note);
    if (fn) {
        const uint8_t first = *reinterpret_cast<const uint8_t*>(fn);
        const bool alreadyDetoured = (first == 0xE9 || first == 0xEB || (first == 0xFF && *reinterpret_cast<const uint8_t*>(fn + 1) == 0x25));
        PVOID tramp = nullptr;
        AurieStatus st = MmCreateHook(g_Module, hookId, reinterpret_cast<PVOID>(fn), reinterpret_cast<PVOID>(hook), &tramp);
        if (AurieSuccess(st) && tramp) {
            *orig = reinterpret_cast<PFUNC_YYGMLScript>(tramp);
            kindOut = std::string("native (inline detour at resolved function") + (alreadyDetoured ? ", chained behind an existing detour" : "") + ")";
            Out(std::string("hook ") + shortName + ": " + kindOut + " [" + note + "]");
            return true;
        }
        Out(std::string("hook ") + shortName + ": inline detour failed st=" + std::to_string((int)st) + " [" + note + "], falling back to the script table");
    } else {
        Out(std::string("hook ") + shortName + ": could not resolve function by name (" + note + "), falling back to the script table");
    }

    HeroSiege::Hooks::ScriptHookOptions opt;
    opt.selfModule = g_Module;
    opt.hookId = hookId;
    auto res = HeroSiege::Hooks::InstallScriptHook(g_Yytk, full, hook, orig, opt);
    using HeroSiege::Hooks::ScriptHookKind;
    switch (res.kind) {
    case ScriptHookKind::Native:    kindOut = "native (table + inline detour)"; break;
    case ScriptHookKind::NativeOnly: kindOut = "native (direct entry; foreign table owner)"; break;
    case ScriptHookKind::TableOnly: kindOut = std::string("TABLE-ONLY: ") + (res.note ? res.note : ""); break;
    case ScriptHookKind::AlreadyInstalled: kindOut = "already installed"; break;
    default: kindOut = std::string("FAILED: ") + (res.note ? res.note : ""); break;
    }
    const bool ok = res.InterceptsDirectCalls();
    Out(std::string("hook ") + shortName + ": " + kindOut);
    if (res.kind == ScriptHookKind::TableOnly)
        Out("  WARNING: direct compiled-GML calls bypass a table-only hook");
    return ok;
}

// Reward routines that are not monster kills (STATIC 2026-09-18): a rift
// completion is WormholeGiveReward -> LootExplosion (+ hashed experience), a
// Chaos Tower completion is a Spawn_Next_obj method -> LootExplosion +
// LootGroundCreate, battle fragments come from DropBattleFragments ->
// LootGroundCreate, and LootExplosion / CreateItemDrop / CreateItemDropInstance
// place items through LootGroundDrop rather than LootGroundCreate. These
// observers record every such call with its arguments as an "event" line in
// the capture session and label the items built inside it, so the shape of
// each reward can be read off ordinary play before it is replayed.
struct EventObserver { const char* name; PFUNC_YYGMLScript orig; std::string kind; };
static EventObserver g_EventObs[] = {
    { "LootExplosion", nullptr, "" }, { "WormholeGiveReward", nullptr, "" }, { "DropBattleFragments", nullptr, "" },
    { "CreateItemDrop", nullptr, "" }, { "CreateItemDropInstance", nullptr, "" }, { "LootGroundDrop", nullptr, "" },
    { "DropRiftItems", nullptr, "" },
};
// Protected values of an arbitrary instance (reward objects are not enemies,
// so their handle names are unknown): every whole number in the handle range
// is resolved, but only kept when it sits in a cluster of at least three such
// values within 4096 of each other - a live instance's handles form a block
// (MEASURED 2026-09-17), a lone gold amount or counter does not.
static std::vector<std::pair<std::string, std::string>> ResolveProtectedCluster(const RValue& inst, const std::vector<std::pair<std::string, std::string>>& vars)
{
    std::vector<std::pair<std::string, double>> cands;
    for (const auto& kv : vars) {
        if (kv.second.empty() || !std::isdigit((unsigned char)kv.second[0])) continue;
        if (kv.second.find_first_of(".eE") != std::string::npos) continue;
        const double d = std::strtod(kv.second.c_str(), nullptr);
        if (d < 100000 || d > 50000000) continue;
        cands.emplace_back(kv.first, d);
    }
    std::vector<std::pair<std::string, std::string>> out;
    for (const auto& c : cands) {
        int closeBy = 0;   // `near` is a windows.h macro
        for (const auto& o : cands) if (std::fabs(o.second - c.second) <= 4096) ++closeBy;
        if (closeBy < 3) continue;
        try { std::string r = ResolveProtected(GetVar(inst, c.first.c_str())); if (!r.empty()) out.emplace_back(c.first, r); } catch (...) {}
    }
    return out;
}
static std::string CaptureEventPacket(const char* script, CInstance* S, int argc, RValue** A)
{
    if (!S) return "";
    try {
        RValue inst = S->ToRValue();
        AfkExpedition::PacketInput in;
        in.script = std::string("gml_Script_") + script;
        for (int i = 0; i < argc; ++i) in.args.push_back(A && A[i] ? Stringify(*A[i]) : std::string("null"));
        in.selfVars = SnapshotSelf(inst);
        in.room = CurrentRoomName();
        in.gameBuildId = GAME_BUILD_ID;
        in.selfObject = ObjectNameOf(inst);
        in.monsterKey = "";
        in.rank = 0;
        in.capturedAt = NowIso();
        in.protectedVars = ResolveProtectedCluster(inst, in.selfVars);
        const std::string hash = AfkExpedition::HashOf(in);
        const std::string path = PacketsDir() + "\\ev_" + hash + ".json";
        std::error_code ec;
        if (!fs::exists(path, ec)) { std::ofstream f(path, std::ios::binary); f << AfkExpedition::ToJson(in, hash); }
        return hash;
    } catch (...) { return ""; }
}
static RValue& EventObserved(int which, CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    EventObserver& ob = g_EventObs[which];
    // Only top-level reward routines are events. DropRiftItems runs INSIDE a
    // kill's DropItem (DropItem -> LoadDrops -> DropRiftItems, MEASURED
    // 2026-09-18: 2.9 calls per rift kill, 2 030 needless packets in one
    // evening), so anything reached while a live kill is being captured is
    // part of that kill and replays with it.
    const bool observe = g_CaptureOn.load() && !g_ReplayActive.load() && g_EventDepth == 0 && g_CtxKind != "live";
    std::string prevKind, prevPacket;
    if (observe) {
        try {
            // the reward routines proper get a packet (self snapshot + args); the
            // ground-drop helpers are only listed
            const bool wantPacket = which <= 2 || which == 6;
            const std::string pk = wantPacket ? CaptureEventPacket(ob.name, S, argc, A) : std::string();
            std::string line = std::string("{\"kind\":\"event\",\"t\":\"") + NowIso() + "\",\"script\":\"" + ob.name + "\",\"self\":\""
                + JsonEscape(S ? ObjectNameOf(S->ToRValue()) : std::string("null")) + "\",\"room\":\"" + JsonEscape(CurrentRoomName())
                + "\",\"packet\":\"" + pk + "\",\"argc\":" + std::to_string(argc) + ",\"args\":[";
            for (int i = 0; i < argc && i < 8; ++i) {
                if (i) line += ",";
                line += "{\"kind\":\"" + (A && A[i] ? KindName(*A[i]) : std::string("?")) + "\",\"value\":\"" + JsonEscape(A && A[i] ? Stringify(*A[i]).substr(0, 600) : std::string("?")) + "\"}";
            }
            line += "]}";
            SessionAppend(line);
        } catch (...) {}
        prevKind = g_CtxKind; prevPacket = g_CtxPacket;
        g_CtxKind = "live_event"; g_CtxPacket = ob.name;
    }
    ++g_EventDepth;
    RValue* r = &R;
    if (ob.orig) r = &ob.orig(S, O, R, argc, A);
    --g_EventDepth;
    if (observe) { g_CtxKind = prevKind; g_CtxPacket = prevPacket; }
    RememberCall(ob.name, S, argc, A, r);
    return *r;
}
static RValue& Hook_Ev0(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A) { return EventObserved(0, S, O, R, argc, A); }
static RValue& Hook_Ev1(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A) { return EventObserved(1, S, O, R, argc, A); }
static RValue& Hook_Ev2(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A) { return EventObserved(2, S, O, R, argc, A); }
static RValue& Hook_Ev3(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A) { return EventObserved(3, S, O, R, argc, A); }
static RValue& Hook_Ev4(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A) { return EventObserved(4, S, O, R, argc, A); }
static RValue& Hook_Ev5(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A) { return EventObserved(5, S, O, R, argc, A); }
static RValue& Hook_Ev6(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A) { return EventObserved(6, S, O, R, argc, A); }
static void InstallEventObservers()
{
    static bool done = false;
    if (done) return;
    done = true;
    PFUNC_YYGMLScript hooks[] = { &Hook_Ev0, &Hook_Ev1, &Hook_Ev2, &Hook_Ev3, &Hook_Ev4, &Hook_Ev5, &Hook_Ev6 };
    for (int i = 0; i < 7; ++i) {
        const std::string id = std::string("afk_ev_") + g_EventObs[i].name;
        InstallScriptDetour(g_EventObs[i].name, id.c_str(), hooks[i], &g_EventObs[i].orig, g_EventObs[i].kind);
    }
}

static bool InstallDropItemHook()
{
    const bool a = InstallScriptDetour("DropItem", "afk_dropitem", &Hook_DropItem, &g_OrigDropItem, g_DropItemHookKind);
    const bool b = InstallScriptDetour("CreateItemNew", "afk_createitemnew", &Hook_CreateItemNew, &g_OrigCreateItemNew, g_CreateItemHookKind);
    const bool c = InstallScriptDetour("LootGroundCreate", "afk_lootgroundcreate", &Hook_LootGroundCreate, &g_OrigLootGroundCreate, g_LootGroundHookKind);
    const bool d = InstallScriptDetour("ExperienceUpdate", "afk_experienceupdate", &Hook_ExperienceUpdate, &g_OrigExperienceUpdate, g_ExpUpdateHookKind);
    const bool e = InstallScriptDetour("GoldLogAdd", "afk_goldlogadd", &Hook_GoldLogAdd, &g_OrigGoldLogAdd, g_GoldLogHookKind);
    if (a && b && c) InstallEventObservers();
    return a && b && c && d && e && InstallIndependentRewardHooks();
}

// ------------------------------------------------------------- replay (P0-2)
// Ghost replay: rebuild the dying monster as an inert instance carrying the
// packet's variables, then run the game's own drop routine on it with the
// packet's arguments. Nothing is synthesised; the dice are the game's.
static std::vector<RValue> g_ReplayDs;          // ds structures created for the current replay call
static std::string g_ReplayNote;

static std::string ReadFileText(const std::string& path)
{
    std::ifstream f(path, std::ios::binary);
    if (!f.good()) return "";
    std::stringstream ss; ss << f.rdbuf();
    return ss.str();
}

static RValue StructGet(const RValue& st, const char* name)
{
    try {
        if (st.m_Kind != VALUE_OBJECT) return RValue();
        if (!g_Yytk->CallBuiltin("variable_struct_exists", { st, RValue(name) }).ToBoolean()) return RValue();
        return g_Yytk->CallBuiltin("variable_struct_get", { st, RValue(name) });
    } catch (...) { return RValue(); }
}

#include "IndependentRewards.inl"
static bool InstallIndependentRewardHooks(){return IndependentRewards::Install();}

// Turn a parsed packet value back into what the game expects on the ghost.
static RValue FromJsonValue(const RValue& v, int depth = 0)
{
    if (depth > 6) return v;
    if (v.m_Kind == VALUE_STRING) {
        const std::string s = v.ToString();
        if (s.rfind("@ref ", 0) == 0) {
            auto lp = s.find('('), rp = s.rfind(')');
            const std::string kind = s.substr(5, lp == std::string::npos ? 0 : lp - 5);
            const std::string name = (lp != std::string::npos && rp != std::string::npos && rp > lp) ? s.substr(lp + 1, rp - lp - 1) : "";
            if (kind == "sprite" || kind == "sound" || kind == "object" || kind == "room" || kind == "font") {
                RValue idx = AssetIndexCached(name);
                if (IsNumberKind(idx)) return idx;
                return RValue(-1.0);
            }
            if (kind == "instance") return RValue(-4.0);   // noone: the killer, targets... are long gone
            if (kind == "path") return RValue(-1.0);
            return RValue();                                 // stale ds handle from an old packet
        }
        return v;
    }
    if (v.m_Kind == VALUE_ARRAY) {
        int n = (int)g_Yytk->CallBuiltin("array_length", { v }).ToDouble();
        RValue arr = g_Yytk->CallBuiltin("array_create", { RValue((double)n) });
        for (int i = 0; i < n; ++i) {
            RValue e = g_Yytk->CallBuiltin("array_get", { v, RValue((double)i) });
            g_Yytk->CallBuiltin("array_set", { arr, RValue((double)i), FromJsonValue(e, depth + 1) });
        }
        return arr;
    }
    if (v.m_Kind == VALUE_OBJECT) {
        RValue ds = StructGet(v, "_ds");
        if (ds.m_Kind == VALUE_STRING) {
            const std::string kind = ds.ToString();
            if (kind == "list") {
                RValue id = g_Yytk->CallBuiltin("ds_list_create", {});
                g_ReplayDs.push_back(id);
                RValue items = StructGet(v, "items");
                int n = items.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { items }).ToDouble() : 0;
                for (int i = 0; i < n; ++i) {
                    RValue e = g_Yytk->CallBuiltin("array_get", { items, RValue((double)i) });
                    g_Yytk->CallBuiltin("ds_list_add", { id, FromJsonValue(e, depth + 1) });
                }
                return id;
            }
            if (kind == "map") {
                RValue id = g_Yytk->CallBuiltin("ds_map_create", {});
                g_ReplayDs.push_back(id);
                RValue entries = StructGet(v, "entries");
                int n = entries.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { entries }).ToDouble() : 0;
                for (int i = 0; i < n; ++i) {
                    RValue pair = g_Yytk->CallBuiltin("array_get", { entries, RValue((double)i) });
                    if (pair.m_Kind != VALUE_ARRAY) continue;
                    RValue k = g_Yytk->CallBuiltin("array_get", { pair, RValue(0.0) });
                    RValue e = g_Yytk->CallBuiltin("array_get", { pair, RValue(1.0) });
                    g_Yytk->CallBuiltin("ds_map_add", { id, FromJsonValue(k, depth + 1), FromJsonValue(e, depth + 1) });
                }
                return id;
            }
        }
        return v;   // ordinary struct: keep as is
    }
    return v;
}

static void FreeReplayDs()
{
    for (auto& id : g_ReplayDs) {
        try {
            if (g_Yytk->CallBuiltin("ds_exists", { id, RValue(2.0) }).ToBoolean()) g_Yytk->CallBuiltin("ds_list_destroy", { id });
            else if (g_Yytk->CallBuiltin("ds_exists", { id, RValue(1.0) }).ToBoolean()) g_Yytk->CallBuiltin("ds_map_destroy", { id });
        } catch (...) {}
    }
    g_ReplayDs.clear();
}

static CInstance* ResolveInstance(const RValue& ref)
{
    try {
        if (!g_Yytk->CallBuiltin("instance_exists", { ref }).ToBoolean()) return nullptr;
        RValue id = g_Yytk->CallBuiltin("variable_instance_get", { ref, RValue("id") });
        RValue resolved = g_Yytk->CallBuiltin("@@GetInstance@@", { id });
        if (resolved.m_Kind != VALUE_OBJECT) return nullptr;
        return resolved.ToInstance();
    } catch (...) { return nullptr; }
}

static int CountInstances(const char* objName)
{
    try {
        RValue oi;
        if (!ObjectIndex(objName, oi)) return -1;
        return (int)g_Yytk->CallBuiltin("instance_number", { oi }).ToDouble();
    } catch (...) { return -1; }
}

// instance_count is a built-in *variable*, not a function.
static int TotalInstances()
{
    try {
        RValue v;
        if (AurieSuccess(g_Yytk->GetBuiltin("instance_count", nullptr, NULL_INDEX, v)) && IsNumberKind(v)) return (int)v.ToDouble();
    } catch (...) {}
    return -1;
}

// Instance census: object index -> live instance count, for every object that
// has at least one instance. Cheap enough for research (one builtin call per
// object) and the only honest way to see what a replay created.
static std::map<int, int> Census()
{
    std::map<int, int> out;
    try {
        for (int i = 0; i < 8192; ++i) {
            RValue idx((double)i);
            if (!g_Yytk->CallBuiltin("object_exists", { idx }).ToBoolean()) continue;
            int n = (int)g_Yytk->CallBuiltin("instance_number", { idx }).ToDouble();
            if (n > 0) out[i] = n;
        }
    } catch (...) {}
    return out;
}

static std::string ObjectNameByIndex(int i)
{
    try { RValue n = g_Yytk->CallBuiltin("object_get_name", { RValue((double)i) }); if (n.m_Kind == VALUE_STRING) return n.ToString(); } catch (...) {}
    return "#" + std::to_string(i);
}

static void ReportCensusDiff(const std::map<int, int>& before, const std::map<int, int>& after)
{
    int lines = 0;
    for (const auto& kv : after) {
        auto it = before.find(kv.first);
        int b = it == before.end() ? 0 : it->second;
        if (kv.second != b) { Out("  " + ObjectNameByIndex(kv.first) + ": " + std::to_string(b) + " -> " + std::to_string(kv.second)); ++lines; }
    }
    for (const auto& kv : before) if (!after.count(kv.first)) { Out("  " + ObjectNameByIndex(kv.first) + ": " + std::to_string(kv.second) + " -> 0"); ++lines; }
    if (!lines) Out("  (no object count changed)");
}

// Remove the newest pickups beyond a baseline count (items and coins a research
// replay put on the floor). Instances are returned by instance_find in creation
// order, so the last ones are ours.
static void DestroyNewPickups(int lootBefore, int coinBefore)
{
    int removed = 0;
    for (const char* obj : { "Loot_Ground_obj", "Coin_obj" }) {
        const int before = (obj[0] == 'L') ? lootBefore : coinBefore;
        try {
            RValue oi;
            if (!ObjectIndex(obj, oi)) continue;
            int n = (int)g_Yytk->CallBuiltin("instance_number", { oi }).ToDouble();
            for (int i = n - 1; i >= before && i >= 0; --i) {
                RValue id = g_Yytk->CallBuiltin("instance_find", { oi, RValue((double)i) });
                g_Yytk->CallBuiltin("instance_destroy", { id }); ++removed;
            }
        } catch (...) {}
    }
    if (removed) Out("cleanup: removed " + std::to_string(removed) + " pickups created by the replay");
}

static std::string FindPacketPath(const std::string& prefix)
{
    std::error_code ec;
    std::string best; fs::file_time_type bestTime{};
    for (auto& e : fs::directory_iterator(PacketsDir(), ec)) {
        if (!e.is_regular_file()) continue;
        const std::string name = e.path().filename().string();
        if (name.size() < 5 || name.substr(name.size() - 5) != ".json") continue;
        if (prefix == "latest") {
            auto t = e.last_write_time(ec);
            if (best.empty() || t > bestTime) { best = e.path().string(); bestTime = t; }
        } else if (name.rfind(prefix, 0) == 0) {
            return e.path().string();
        }
    }
    return best;
}

// Variables the ghost must keep as its own.
static bool IsGhostOwnVar(const std::string& n)
{
    static const char* own[] = { "id", "object_index", "x", "y", "xstart", "ystart", "xprevious", "yprevious",
                                 "depth", "layer", "persistent", "solid", "visible", "phy_active" };
    for (const char* o : own) if (n == o) return true;
    return false;
}

static const char* g_ReplayStage = "-";
static void CmdReplayImpl(const std::string& prefix, int times, bool clean, const std::string& spoolId);
static void CmdReplay(const std::string& prefix, int times, bool clean, const std::string& spoolId)
{
    if (g_ExpBusy) { Out("replay: an expedition is running; abort it first"); return; }
    g_ReplayStage = "start";
    try { CmdReplayImpl(prefix, times, clean, spoolId); }
    catch (...) { Out(std::string("replay: EXCEPTION at stage '") + g_ReplayStage + "'"); g_ReplayActive = false; g_SpoolActive = false; FreeReplayDs(); }
}

// A packet loaded once (parsed JSON kept as game structs) and replayed many times.
// The first replay also prepares what every replay of it needs: each restored
// variable's name and converted value, the protected values and the call
// arguments, so a kill no longer walks the parsed JSON, re-parses "@ref ..."
// strings and looks up asset names. Arrays and ds markers stay per-call values
// (each ghost gets fresh ones, exactly as before); everything else is shared
// the way plain structs already were.
struct PreparedValue { RValue name; RValue value; bool perCall = false; };
struct LoadedPacket {
    std::string path, hashPrefix, monsterKey, selfObject;
    RValue pk, args, snap, prot, monsterObj;
    int argc = 0; double packetExp = -1.0; bool haveMonsterObj = false;
    double overrideExp = -1.0;     // from the plan: mean kill experience of the kills behind this packet
    bool prepared = false;
    std::vector<PreparedValue> vars, prots, argv;
};
// anchor: a global variable name that keeps the parsed packet alive between
// frames. The runner's garbage collector only sees GML references; a struct
// held solely by C++ RValues is freed at the next collection (MEASURED
// 2026-09-17: a multi-frame expedition crashed after ~2 frames without this).
static bool LoadPacket(const std::string& prefix, LoadedPacket& lp, std::string& err, const char* anchor = "__afk_keep_replay")
{
    lp.path = FindPacketPath(prefix);
    if (lp.path.empty()) { err = "packet not found for '" + prefix + "'"; return false; }
    const std::string text = ReadFileText(lp.path);
    if (text.empty()) { err = "cannot read " + lp.path; return false; }
    try { lp.pk = g_Yytk->CallBuiltin("json_parse", { RValue(text) }); } catch (...) { err = "json_parse threw"; return false; }
    if (lp.pk.m_Kind != VALUE_OBJECT) { err = "packet did not parse to a struct"; return false; }
    try { g_Yytk->CallBuiltin("variable_global_set", { RValue(anchor), lp.pk }); } catch (...) { err = "cannot anchor the packet in a global"; return false; }
    RValue build = StructGet(lp.pk, "game_build_id");
    if (build.m_Kind == VALUE_STRING && build.ToString() != GAME_BUILD_ID) { err = "refused, packet is from another game build (" + build.ToString() + ")"; return false; }
    lp.args = StructGet(lp.pk, "args");
    lp.snap = StructGet(lp.pk, "self_snapshot");
    lp.prot = StructGet(lp.pk, "protected");
    if (lp.args.m_Kind != VALUE_ARRAY || lp.snap.m_Kind != VALUE_OBJECT) { err = "packet lacks args/self_snapshot"; return false; }
    if (lp.prot.m_Kind != VALUE_OBJECT) { err = "old packet format without protected values (the drop routine rejects it); capture again"; return false; }
    {
        // a monster packet must carry what the drop routine reads through the
        // anti-cheat wrapper; without these the ghost would get raw handle ids
        // that read as some other value or throw (MEASURED 2026-09-18)
        // Breakables too: a chest's snapshot carried `dSlots` as a raw handle
        // id and one replay of it produced 4, then 0, then 6 650 items as the
        // id drifted onto other counters (MEASURED 2026-09-18). Any drop
        // variable that looks like a handle in the snapshot must be resolved.
        RValue mk0 = StructGet(lp.pk, "monster_key");
        const bool monster = mk0.m_Kind == VALUE_STRING && !mk0.ToString().empty();
        for (const char* need : { "dSlots", "dCommonChance", "dCommonDropMult", "dSatanicDropMult", "killExperience", "extraMagicFind", "lootAmount" }) {
            const bool have = g_Yytk->CallBuiltin("variable_struct_exists", { lp.prot, RValue(need) }).ToBoolean();
            if (have) continue;
            bool handleLike = false;
            if (g_Yytk->CallBuiltin("variable_struct_exists", { lp.snap, RValue(need) }).ToBoolean()) {
                RValue v = StructGet(lp.snap, need);
                handleLike = IsNumberKind(v) && v.m_Kind != VALUE_BOOL && v.ToDouble() >= 1000 && v.ToDouble() == std::floor(v.ToDouble());
            }
            if (handleLike || (monster && std::string(need) != "lootAmount")) {
                err = std::string("incomplete packet: protected value '") + need + "' was not captured (an older capture); the profile skips it";
                return false;
            }
        }
    }
    lp.argc = (int)g_Yytk->CallBuiltin("array_length", { lp.args }).ToDouble();
    RValue mk = StructGet(lp.pk, "monster_key"); lp.monsterKey = mk.m_Kind == VALUE_STRING ? mk.ToString() : "?";
    RValue ph = StructGet(lp.pk, "packet_hash"); lp.hashPrefix = ph.m_Kind == VALUE_STRING ? ph.ToString() : prefix;
    RValue so = StructGet(lp.pk, "self_object"); lp.selfObject = so.m_Kind == VALUE_STRING ? so.ToString() : "";
    lp.haveMonsterObj = !lp.selfObject.empty() && ObjectIndex(lp.selfObject, lp.monsterObj);
    // the kill's experience as captured (packet "exp_reward": {"raw", "resolved"})
    try { RValue ex = StructGet(lp.pk, "exp_reward"); if (ex.m_Kind == VALUE_OBJECT) { RValue r = StructGet(ex, "resolved"); if (IsNumberKind(r)) lp.packetExp = r.ToDouble(); } } catch (...) {}
    return true;
}

// Where a replay happens: next to the player, with the player as killer.
struct ReplayEnv { RValue pid; CInstance* pi = nullptr; double px = 0, py = 0; RValue ghostObj; };
static bool PrepareReplayEnv(ReplayEnv& env, std::string& err)
{
    try {
        RValue pobj;
        if (!ObjectIndex("Player_obj", pobj)) { err = "Player_obj not found"; return false; }
        env.pid = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
        if (!g_Yytk->CallBuiltin("instance_exists", { env.pid }).ToBoolean()) { err = "no player instance in this room"; return false; }
        env.pi = ResolveInstance(env.pid);
        if (!env.pi) { err = "cannot resolve the player instance"; return false; }
        env.px = g_Yytk->CallBuiltin("variable_instance_get", { env.pid, RValue("x") }).ToDouble();
        env.py = g_Yytk->CallBuiltin("variable_instance_get", { env.pid, RValue("y") }).ToDouble();
    } catch (...) { err = "cannot locate the player"; return false; }
    if (!ObjectIndex("Blood_Maiden_Spawnpoint_obj", env.ghostObj)) { err = "ghost object not found"; return false; }
    return true;
}

struct CallOutcome { bool ok = false; int restored = 0; int handles = 0; bool changed = false; std::string note; };

// Where replay time goes, per stage, for the progress file (`perf`). Game
// thread only; reset when an expedition starts.
struct ReplayPerf {
    uint64_t calls = 0, frames = 0;
    double create = 0, restore = 0, prot = 0, drop = 0, exp = 0, coins = 0, popups = 0, cleanup = 0, frameTotal = 0, checkpoint = 0;
};
static ReplayPerf g_Perf;
static double PerfMs(std::chrono::steady_clock::time_point& t)
{
    const auto now = std::chrono::steady_clock::now();
    const double ms = std::chrono::duration<double, std::milli>(now - t).count();
    t = now;
    return ms;
}

// variable_instance_set is called several hundred times per replayed kill;
// calling the runner's routine directly skips YYToolkit's by-name lookup and
// argument vector for each of them. Falls back to CallBuiltin if unresolved.
static TRoutine g_VariableInstanceSet = nullptr;
static bool g_VariableInstanceSetTried = false;
static void SetInstanceVar(const RValue& inst, const RValue& name, const RValue& value)
{
    if (!g_VariableInstanceSetTried) {
        g_VariableInstanceSetTried = true;
        PVOID fn = nullptr;
        if (AurieSuccess(g_Yytk->GetNamedRoutinePointer("variable_instance_set", &fn)) && fn) g_VariableInstanceSet = reinterpret_cast<TRoutine>(fn);
    }
    if (g_VariableInstanceSet) {
        RValue args[3] = { inst, name, value };
        RValue result;
        g_VariableInstanceSet(result, nullptr, nullptr, 3, args);
        return;
    }
    g_Yytk->CallBuiltin("variable_instance_set", { inst, name, value });
}

static bool NeedsFreshValue(const RValue& v)
{
    if (v.m_Kind == VALUE_ARRAY) return true;
    if (v.m_Kind != VALUE_OBJECT) return false;
    return StructGet(v, "_ds").m_Kind == VALUE_STRING;
}

static void PreparePacket(LoadedPacket& lp)
{
    if (lp.prepared) return;
    lp.vars.clear(); lp.prots.clear(); lp.argv.clear();
    RValue names = g_Yytk->CallBuiltin("variable_struct_get_names", { lp.snap });
    const int n = names.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { names }).ToDouble() : 0;
    for (int i = 0; i < n; ++i) {
        RValue nm = g_Yytk->CallBuiltin("array_get", { names, RValue((double)i) });
        if (nm.m_Kind != VALUE_STRING || IsGhostOwnVar(nm.ToString())) continue;
        RValue raw = g_Yytk->CallBuiltin("variable_struct_get", { lp.snap, nm });
        PreparedValue pv; pv.name = nm; pv.perCall = NeedsFreshValue(raw);
        pv.value = pv.perCall ? raw : FromJsonValue(raw);
        lp.vars.push_back(pv);
    }
    if (lp.prot.m_Kind == VALUE_OBJECT) {
        RValue pn = g_Yytk->CallBuiltin("variable_struct_get_names", { lp.prot });
        const int m = pn.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { pn }).ToDouble() : 0;
        for (int i = 0; i < m; ++i) {
            RValue nm = g_Yytk->CallBuiltin("array_get", { pn, RValue((double)i) });
            RValue val = g_Yytk->CallBuiltin("variable_struct_get", { lp.prot, nm });
            if (!IsNumberKind(val)) continue;
            PreparedValue pv; pv.name = nm; pv.value = val;
            lp.prots.push_back(pv);
        }
    }
    for (int i = 0; i < lp.argc; ++i) {
        RValue raw = g_Yytk->CallBuiltin("array_get", { lp.args, RValue((double)i) });
        PreparedValue pv; pv.perCall = NeedsFreshValue(raw);
        pv.value = pv.perCall ? raw : FromJsonValue(raw);
        lp.argv.push_back(pv);
    }
    lp.prepared = true;
}

// One replayed kill: ghost up, the game's drop routine, experience, coins, ghost down.
static CallOutcome ReplayOneCall(LoadedPacket& lp, const ReplayEnv& env)
{
    CallOutcome oc;
    auto stage = std::chrono::steady_clock::now();
    g_ReplayStage = "prepare packet";
    try { PreparePacket(lp); } catch (...) { oc.note = "preparing the packet threw"; return oc; }
    g_ReplayStage = "create ghost";
    RValue ghost;
    try { ghost = g_Yytk->CallBuiltin("instance_create_depth", { RValue(env.px + 96.0), RValue(env.py), RValue(0.0), env.ghostObj }); }
    catch (...) { oc.note = "instance_create_depth threw"; return oc; }
    g_ReplayStage = "resolve ghost";
    CInstance* gi = ResolveInstance(ghost);
    if (!gi) { oc.note = "ghost instance could not be resolved"; return oc; }
    g_Perf.create += PerfMs(stage);

    // 1) restore the monster's variables
    g_ReplayStage = "restore variables";
    std::vector<RValue> protectedHandles;
    try {
        for (const auto& v : lp.vars) {
            SetInstanceVar(ghost, v.name, v.perCall ? FromJsonValue(v.value) : v.value);
            ++oc.restored;
        }
        g_Perf.restore += PerfMs(stage);
        // 2) protected values. The drop routine reads these through the
        //    anti-cheat wrapper, so a plain number is read as handle 0
        //    (MEASURED 2026-09-17: 30 replays with plain values produced
        //    nothing). Allocate a real handle per value with the game's
        //    own "new variable" wrapper and free it after the call.
        for (const auto& p : lp.prots) {
            RValue h;
            try { h = g_Yytk->CallGameScript("gml_Script_PC_InitNewVariableFastGMLWrapper", { p.value }); } catch (...) { h = RValue(); }
            if (IsNumberKind(h) && h.ToDouble() > 0) {
                protectedHandles.push_back(h);
                SetInstanceVar(ghost, p.name, h);
            } else {
                SetInstanceVar(ghost, p.name, p.value);
            }
        }
        SetInstanceVar(ghost, RValue("xPos"), RValue(env.px + 96.0));
        SetInstanceVar(ghost, RValue("yPos"), RValue(env.py));
        g_Perf.prot += PerfMs(stage);
    } catch (...) { oc.note = "restoring variables threw"; }
    oc.handles = (int)protectedHandles.size();

    // 2b) identity: the drop routine only produces loot when self is an
    //     enemy-typed instance (MEASURED 2026-09-17: 30 calls on a live
    //     Orc_Warrior dropped an item, 60 calls on a spawnpoint-typed
    //     ghost carrying the same variables dropped nothing). Change the
    //     ghost's object type to the monster's without running its Create
    //     event, and change it back before destroying it so the monster's
    //     Destroy event never runs on the ghost.
    g_ReplayStage = "instance_change";
    if (lp.haveMonsterObj) {
        RValue r;
        if (AurieSuccess(g_Yytk->CallBuiltinEx(r, "instance_change", gi, gi, { lp.monsterObj, RValue(false) }))) oc.changed = true;
        else oc.note = "instance_change failed for " + lp.selfObject;
    }

    // 3) arguments
    g_ReplayStage = "build arguments";
    std::vector<RValue> av; av.reserve(lp.argc);
    for (int i = 0; i < lp.argc; ++i) {
        if (i == 2) av.push_back(RValue(env.px + 96.0));
        else if (i == 3) av.push_back(RValue(env.py));
        else if (i < (int)lp.argv.size()) av.push_back(lp.argv[i].perCall ? FromJsonValue(lp.argv[i].value) : lp.argv[i].value);
        else av.push_back(RValue());
    }
    std::vector<RValue*> ap; for (auto& a : av) ap.push_back(&a);

    // 4) the game's own drop routine, with the ghost as self
    g_ReplayStage = "DropItem";
    const int coinBefore = CountInstances("Coin_obj");
    const int popupsBefore = CountInstances("Combat_Text_obj");
    RValue result;
    g_ReplayActive = true; g_CtxKind = "replay"; g_CtxPacket = lp.hashPrefix;
    try { g_OrigDropItem(gi, gi, result, lp.argc, ap.data()); oc.ok = true; }
    catch (...) { oc.ok = false; }
    g_ReplayActive = false; g_CtxKind = "none"; g_CtxPacket.clear();
    g_Perf.drop += PerfMs(stage);
    if (g_SpoolActive) {
        if (g_GiveExp && oc.ok) {
            // What the game does at death (Enemy_Parent_obj Destroy event,
            // STATIC 2026-09-17): EnemyGiveExperience(amount), amount being the
            // monster's protected `killExperience`, which the spawn alarm
            // computed with EnemyCalculateExperience (zone, level gap, the
            // player's experience stats). The packet carries that value
            // (expResolved) and the ghost holds it in a fresh handle, so the
            // ghost hands over exactly the number a real death would.
            // EnemyGiveExperience forwards its argument 0 to the hashed
            // ExperienceUpdate: anything else there is credited as that
            // number (an instance id was credited as ~260k, MEASURED) and no
            // argument crashes the game, so the amount is always explicit.
            // Never call ExperienceUpdate directly: a wrong hash is a cheat
            // report to the game (STATIC: GetCounterHash check, ReportClient).
            double amount = -1.0;
            try {
                RValue kh = GetVar(gi->ToRValue(), "killExperience");
                if (IsNumberKind(kh)) { std::string r = ResolveProtected(kh); if (!r.empty()) amount = std::strtod(r.c_str(), nullptr); }
            } catch (...) {}
            if (lp.overrideExp >= 0) amount = lp.overrideExp;      // Zero XP is an explicit baseline too.
            else if (!(amount > 0)) amount = lp.packetExp;
            if (amount > 0 && amount < 1e9) {
                g_ReplayStage = "give exp";
                RValue res; g_ReplayActive = true;
                AurieStatus st = g_Yytk->CallGameScriptEx(res, "gml_Script_EnemyGiveExperience", gi, env.pi, { RValue(amount) });
                g_ReplayActive = false;
                ++g_GiveExpCalls; g_GiveExpSum += amount;
                if (g_GiveExpNote.empty()) g_GiveExpNote = "killExperience=" + std::to_string((long long)amount) + " EnemyGiveExperience st=" + std::to_string((int)st);
            } else if (g_GiveExpNote.empty()) {
                g_GiveExpNote = "no usable kill experience (packet expResolved=" + std::to_string((long long)lp.packetExp) + ")";
            }
        }
        g_Perf.exp += PerfMs(stage);
        g_ReplayStage = "harvest coins"; g_PlayerX = env.px; g_PlayerY = env.py; HarvestCoins(coinBefore);
        g_Perf.coins += PerfMs(stage);
        // The experience hand-over spawns a floating text (Combat_Text_obj) per
        // call; thousands of them alive drag the frame rate from ~250 to ~40
        // calls per second (MEASURED 2026-09-18). Remove the ones this call made.
        g_ReplayStage = "trim popups";
        try {
            static RValue ctObj; static bool ctKnown = false, ctMissing = false;
            if (!ctKnown && !ctMissing) { ctMissing = !ObjectIndex("Combat_Text_obj", ctObj); ctKnown = !ctMissing; }
            if (ctKnown) {
                const int n = (int)g_Yytk->CallBuiltin("instance_number", { ctObj }).ToDouble();
                for (int i = n - 1; i >= popupsBefore && i >= 0; --i) {
                    RValue id = g_Yytk->CallBuiltin("instance_find", { ctObj, RValue((double)i) });
                    g_Yytk->CallBuiltin("instance_destroy", { id });
                }
            }
        } catch (...) {}
        g_Perf.popups += PerfMs(stage);
    }

    g_ReplayStage = "cleanup";
    if (oc.changed) { RValue r; try { g_Yytk->CallBuiltinEx(r, "instance_change", gi, gi, { env.ghostObj, RValue(false) }); } catch (...) {} }
    try { g_Yytk->CallBuiltin("instance_destroy", { ghost }); } catch (...) {}
    for (auto& h : protectedHandles) { try { g_Yytk->CallGameScript("gml_Script_PC_FreeVariableGMLWrapper", { h }); } catch (...) {} }
    FreeReplayDs();
    g_Perf.cleanup += PerfMs(stage);
    ++g_Perf.calls;
    return oc;
}

static void SpoolBegin(const std::string& spoolId)
{
    SpoolClose();
    g_SpoolActive = !spoolId.empty();
    if (!g_SpoolActive) return;
    std::error_code ec; fs::create_directories(DATA_ROOT + "\\spool", ec);
    g_SpoolId = spoolId; g_SpoolPath = DATA_ROOT + "\\spool\\" + spoolId + ".ndjson";
    g_SpoolItems = 0; g_SpoolGold = 0; g_SpoolGoldPiles = 0; g_LootGroundSkipped = 0; g_LootGroundArgsNote.clear();
    g_GiveExpCalls = 0; g_GiveExpSum = 0; g_GiveExpNote.clear(); g_ExpUpdateCalls = 0; g_ExpUpdateSum = 0; g_ExpArgsNote.clear();
    g_GoldLogCalls = 0; g_GoldLogSum = 0; g_GoldArgsNote.clear(); g_HarvestedCoins.clear();
    g_SpoolFiltered = 0; g_SpoolUnplaced = 0; g_PendingSpoolItem.clear();
    // continue the sequence if the spool already has records
    g_SpoolSeq = 0;
    { std::ifstream f(g_SpoolPath); std::string line; while (std::getline(f, line)) if (!line.empty()) ++g_SpoolSeq; }
}
static std::string SpoolSummaryText()
{
    return "spool " + g_SpoolId + ": items=" + std::to_string(g_SpoolItems) + " (filtered " + std::to_string(g_SpoolFiltered) + ", unplaced " + std::to_string(g_SpoolUnplaced) + ") gold=" + std::to_string((long long)g_SpoolGold)
        + " (" + std::to_string(g_SpoolGoldPiles) + " piles, " + (g_GoldPickup ? "given to player" : "removed") + ") ground_removed=" + std::to_string(g_LootGroundSkipped)
        + " exp_calls=" + std::to_string(g_GiveExpCalls) + " exp_given=" + std::to_string((long long)g_GiveExpSum) + " [" + g_GiveExpNote + "]"
        + " ExperienceUpdate: " + std::to_string(g_ExpUpdateCalls) + " calls sum=" + std::to_string((long long)g_ExpUpdateSum) + " {" + g_ExpArgsNote + "}"
        + " GoldLogAdd: " + std::to_string(g_GoldLogCalls) + " calls sum=" + std::to_string((long long)g_GoldLogSum) + " {" + g_GoldArgsNote + "}"
        + " -> " + g_SpoolPath;
}
static std::string SpoolSummaryLine(long long calls, const char* kind)
{
    std::ostringstream f;
    f << "{\"expedition_id\":\"" << JsonEscape(g_SpoolId) << "\",\"seq\":" << (++g_SpoolSeq) << ",\"kind\":\"" << kind << "\",\"t\":\"" << NowIso()
      << "\",\"gold\":" << (long long)g_SpoolGold << ",\"gold_piles\":" << g_SpoolGoldPiles << ",\"exp_credited\":" << (long long)g_GiveExpSum
      << ",\"calls\":" << calls << ",\"items\":" << g_SpoolItems << ",\"items_filtered\":" << g_SpoolFiltered << ",\"items_unplaced\":" << g_SpoolUnplaced
      << ",\"forgepact\":" << ForgePactSettingsJson() << ",\"conversion\":" << ConversionJson() << "}";
    return f.str();
}
static void SpoolEnd(long long calls, const char* kind = "summary")
{
    if (!g_SpoolActive) return;
    FlushPendingSpoolItem("");
    SpoolWriteLine(SpoolSummaryLine(calls, kind));
    SpoolClose();
    g_SpoolActive = false;
}

static void CmdReplayImpl(const std::string& prefix, int times, bool clean, const std::string& spoolId)
{
    if (!g_OrigDropItem) { Out("replay: DropItem hook not installed"); return; }
    SpoolBegin(spoolId);
    g_ReplayStage = "find packet";
    LoadedPacket lp; std::string err;
    if (!LoadPacket(prefix, lp, err)) { Out("replay: " + err); g_SpoolActive = false; return; }
    if (g_SessionFile.empty()) SessionOpen();
    g_ReplayStage = "locate player";
    ReplayEnv env;
    if (!PrepareReplayEnv(env, err)) { Out("replay: " + err); g_SpoolActive = false; return; }
    const int lootBefore = CountInstances("Loot_Ground_obj"), coinBefore = CountInstances("Coin_obj");
    const int allBefore = TotalInstances();
    g_ReplayStage = "census before";
    const std::map<int, int> censusBefore = Census();

    int done = 0, failed = 0;
    for (int t = 0; t < times; ++t) {
        CallOutcome oc = ReplayOneCall(lp, env);
        if (oc.ok) ++done; else ++failed;
        if (t == 0) Out("replay: ghost restored " + std::to_string(oc.restored) + " variables, " + std::to_string(oc.handles) + " protected handles, "
                        + (oc.changed ? "typed as the monster" : "type unchanged") + ", argc=" + std::to_string(lp.argc) + (oc.ok ? "" : ", DropItem threw") + (oc.note.empty() ? "" : " [" + oc.note + "]"));
    }
    g_ReplayStage = "report";
    const int lootAfter = CountInstances("Loot_Ground_obj");
    const int allAfter = TotalInstances();
    Out("replay " + lp.monsterKey + " x" + std::to_string(times)
        + ": ok=" + std::to_string(done) + " failed=" + std::to_string(failed)
        + " loot_ground " + std::to_string(lootBefore) + " -> " + std::to_string(lootAfter)
        + " instances " + std::to_string(allBefore) + " -> " + std::to_string(allAfter));
    g_ReplayStage = "census after";
    Out("replay: object counts that changed:");
    ReportCensusDiff(censusBefore, Census());
    if (g_SpoolActive) { Out(SpoolSummaryText()); SpoolEnd(times); }
    if (clean) DestroyNewPickups(lootBefore, coinBefore);
}

// ------------------------------------------------------------- expedition
// A planned run: a list of (packet, count) replayed a few calls per frame so
// the game keeps drawing, with progress written to disk after every frame so
// a controlled abort can resume. A crashed process cannot prove that the game
// save and spool agree: refuse automatic recovery of a running checkpoint. It
// continues only when the checkpoint carries resume_accepted, which the panel
// writes after the player confirmed a position the item records end at exactly.
static std::string IdentityKey(const RValue& stamp)
{
    RValue version = StructGet(stamp, "identity_version"), slot = StructGet(stamp, "slot"), cls = StructGet(stamp, "class"), name = StructGet(stamp, "name");
    if (!IsNumberKind(version) || version.ToDouble() != 2 || !IsNumberKind(slot) || slot.m_Kind == VALUE_BOOL
        || !IsNumberKind(cls) || cls.m_Kind == VALUE_BOOL || name.m_Kind != VALUE_STRING || name.ToString().empty()) return "";
    const int s = AfkExpedition::LocalSaveSlot({0, slot.ToDouble()});
    if (s < 0 || !std::isfinite(cls.ToDouble()) || cls.ToDouble() < 0 || std::floor(cls.ToDouble()) != cls.ToDouble()) return "";
    return std::to_string(s) + ":" + std::to_string((int)cls.ToDouble()) + ":" + name.ToString();
}
static std::string CurrentIdentityKey()
{
    try { return IdentityKey(g_Yytk->CallBuiltin("json_parse", { RValue(CharacterStampJson()) })); } catch (...) { return ""; }
}
static AfkExpedition::RewardTotals CurrentRewards()
{
    AfkExpedition::RewardTotals r;
    r.items = g_SpoolItems; r.filtered = g_SpoolFiltered; r.unplaced = g_SpoolUnplaced;
    r.groundRemoved = g_LootGroundSkipped; r.goldPiles = g_SpoolGoldPiles; r.expCalls = g_GiveExpCalls;
    r.gold = g_SpoolGold; r.exp = g_GiveExpSum; r.expUpdateCalls = g_ExpUpdateCalls;
    r.expUpdateSum = g_ExpUpdateSum; r.goldLogCalls = g_GoldLogCalls; r.goldLogSum = g_GoldLogSum;
    return r;
}
static void RestoreRewards(const AfkExpedition::RewardTotals& r)
{
    g_SpoolItems = r.items; g_SpoolFiltered = r.filtered; g_SpoolUnplaced = r.unplaced;
    g_LootGroundSkipped = r.groundRemoved; g_SpoolGoldPiles = r.goldPiles; g_GiveExpCalls = r.expCalls;
    g_SpoolGold = r.gold; g_GiveExpSum = r.exp; g_ExpUpdateCalls = r.expUpdateCalls;
    g_ExpUpdateSum = r.expUpdateSum; g_GoldLogCalls = r.goldLogCalls; g_GoldLogSum = r.goldLogSum;
}
struct ExpPacketPlan { std::string hash; long long count = 0; double exp = -1.0; };
struct Expedition {
    AfkExpedition::RewardPolicy rewards;
    double effectiveMagicFind=-1;
    bool running = false;
    std::string id, planPath, progressPath, startedAt, error, state = "idle";
    std::vector<ExpPacketPlan> packets;
    size_t idx = 0; long long doneInPacket = 0, callsDone = 0, callsTotal = 0, failed = 0;
    int perFrame = 60; double frameBudgetMs = 12.0; bool exp = true; bool gold = true;
    LoadedPacket cur; bool curLoaded = false; std::string curHash;
    uint32_t pausedFrames = 0; std::string pauseReason;
    int curFailStreak = 0; long long skipped = 0;
    std::string saveNote;
    std::string planHash, identity, room;
    bool anywhere = false;
    // A pause while the expedition character is still loaded (it walked out of
    // the region) saves the game and leaves a resumable "paused" checkpoint.
    bool pausedSaved = false;
    std::chrono::steady_clock::time_point lastCheckpoint{};
};
static Expedition g_Exp;
static uint64_t g_CheckpointWriteRetries = 0;
static std::string PersistRewards();
static void InstallCloseGuard();

// The Prospector's recipe table as the game holds it (global.prospectItem /
// global.prospectResult, filled by DefineProspectCombos). Only recipes that
// take Satanic and above ("unique") matter here; a single-id amount is stored
// protected and read through the game's own PilipaliDecrypt, as the game does.
// ores = false: the recipes for Satanic and above equipment (break-down);
// ores = true: the recipes that take a mining ore (a worker's Gem Sense).
static bool LoadProspectRecipes(std::vector<AfkExpedition::ProspectRecipe>& out, std::string& why, std::vector<std::string>* report = nullptr, bool ores = false)
{
    out.clear();
    try {
        RValue items = g_Yytk->CallBuiltin("variable_global_get", { RValue("prospectItem") });
        RValue results = g_Yytk->CallBuiltin("variable_global_get", { RValue("prospectResult") });
        if (items.m_Kind != VALUE_ARRAY || results.m_Kind != VALUE_ARRAY) { why = "the Prospector recipe table was not found"; return false; }
        const int n = static_cast<int>(g_Yytk->CallBuiltin("array_length", { items }).ToDouble());
        const int m = static_cast<int>(g_Yytk->CallBuiltin("array_length", { results }).ToDouble());
        auto numbers = [](const RValue& v, std::vector<int>& list, std::string& kinds) {
            if (IsNumberKind(v) && v.m_Kind != VALUE_BOOL) { list.push_back(static_cast<int>(v.ToDouble())); return; }
            if (v.m_Kind != VALUE_ARRAY) { kinds += KindName(v) + " "; return; }
            const int k = static_cast<int>(g_Yytk->CallBuiltin("array_length", { v }).ToDouble());
            for (int j = 0; j < k; ++j) {
                RValue e = g_Yytk->CallBuiltin("array_get", { v, RValue(static_cast<double>(j)) });
                if (IsNumberKind(e) && e.m_Kind != VALUE_BOOL) list.push_back(static_cast<int>(e.ToDouble()));
                else kinds += KindName(e) + " ";
            }
        };
        for (int i = 0; i < n && i < m; ++i) {
            RValue in = g_Yytk->CallBuiltin("array_get", { items, RValue(static_cast<double>(i)) });
            RValue res = g_Yytk->CallBuiltin("array_get", { results, RValue(static_cast<double>(i)) });
            if (in.m_Kind != VALUE_OBJECT) continue;
            AfkExpedition::ProspectRecipe r;
            RValue unique = StructGet(in, "isUnique");
            r.unique = (unique.m_Kind == VALUE_BOOL || IsNumberKind(unique)) && unique.ToBoolean();
            std::string skippedKinds;
            numbers(StructGet(in, "itemType"), r.types, skippedKinds);
            RValue tier = StructGet(in, "tierRequirement"); r.tier = IsNumberKind(tier) ? static_cast<int>(tier.ToDouble()) : AfkExpedition::kAnyTier;
            RValue base = StructGet(in, "itemId"); r.baseId = IsNumberKind(base) ? static_cast<int>(base.ToDouble()) : -1;
            std::vector<RValue> outs;
            if (res.m_Kind == VALUE_OBJECT) outs.push_back(res);
            else if (res.m_Kind == VALUE_ARRAY) {
                const int k = static_cast<int>(g_Yytk->CallBuiltin("array_length", { res }).ToDouble());
                for (int j = 0; j < k; ++j) outs.push_back(g_Yytk->CallBuiltin("array_get", { res, RValue(static_cast<double>(j)) }));
            }
            std::string line = "recipe " + std::to_string(i) + (r.unique ? " unique" : " other") + " types[";
            for (int t : r.types) line += std::to_string(t) + " ";
            line += "] tier=" + std::to_string(r.tier) + " base=" + std::to_string(r.baseId) + (skippedKinds.empty() ? "" : " unread-types(" + skippedKinds + ")") + " ->";
            for (const RValue& o : outs) {
                if (o.m_Kind != VALUE_OBJECT) continue;
                AfkExpedition::ProspectOutput out;
                RValue type = StructGet(o, "itemType"); out.type = IsNumberKind(type) ? static_cast<int>(type.ToDouble()) : -1;
                std::string unread; numbers(StructGet(o, "itemId"), out.ids, unread);
                RValue chance = StructGet(o, "resultChance"); out.chance = IsNumberKind(chance) ? chance.ToDouble() : 100.0;
                RValue raw = StructGet(o, "amount");
                std::string amountNote = "none";
                if (out.ids.size() == 1 && IsNumberKind(raw)) {
                    RValue plain; double value = -1;
                    try { plain = g_Yytk->CallGameScript(HeroSiege::Scripts::gml_Script_PilipaliDecrypt.data(), { raw, RValue(), RValue() }); } catch (...) {}
                    if (IsNumberKind(plain)) value = plain.ToDouble();
                    amountNote = Stringify(raw).substr(0, 24) + "=>" + (IsNumberKind(plain) ? Stringify(plain) : std::string("?"));
                    if (!(value >= 1 && value <= AfkExpedition::kNativeStackMax && std::floor(value) == value)) { out.ids.clear(); amountNote += "(refused)"; }
                    else out.amount = static_cast<long long>(value);
                }
                line += " {type " + std::to_string(out.type) + " ids";
                for (int id : out.ids) line += " " + std::to_string(id);
                line += " amount " + amountNote + " chance " + Stringify(RValue(out.chance)) + "}";
                if (out.type >= 0 && !out.ids.empty()) r.outputs.push_back(out);
            }
            if (report) report->push_back(line);
            if (!r.types.empty() && !r.outputs.empty() && (ores ? !r.unique && AfkExpedition::WorkerOre(r.baseId) : r.unique)) out.push_back(r);
        }
    } catch (...) { why = "reading the Prospector recipe table threw"; out.clear(); return false; }
    if (out.empty()) { why = ores ? "no Prospector recipe for mining ores was readable" : "no Prospector recipe for Satanic and above items was readable"; return false; }
    return true;
}
// The Jeweler's recipes as the game holds them (global.craftComboList /
// global.craftComboResult, filled by DefineCraftingCombos): result types 37-41
// make socketable jewels and gems. Amounts are read through the game's own
// PilipaliDecrypt, as the Prospector's are; a small whole number that does not
// decrypt is taken as stored plainly (the report says which).
static long long JewelAmount(const RValue& raw, std::string& note)
{
    if (!IsNumberKind(raw) || raw.m_Kind == VALUE_BOOL) { note = "none"; return -1; }
    RValue plain;
    try { plain = g_Yytk->CallGameScript(HeroSiege::Scripts::gml_Script_PilipaliDecrypt.data(), { raw, RValue(), RValue() }); } catch (...) {}
    const double stored = raw.ToDouble();
    if (IsNumberKind(plain)) {
        const double v = plain.ToDouble();
        if (v >= 1 && v <= AfkExpedition::kNativeStackMax && std::floor(v) == v) { note = "decrypted"; return static_cast<long long>(v); }
    }
    if (stored >= 1 && stored <= AfkExpedition::kNativeStackMax && std::floor(stored) == stored) { note = "plain"; return static_cast<long long>(stored); }
    note = "unreadable"; return -1;
}
static AfkExpedition::JewelPart ReadJewelPart(const RValue& s, std::string& note)
{
    AfkExpedition::JewelPart p;
    if (s.m_Kind != VALUE_OBJECT) { note = "not a struct"; return p; }
    RValue type = StructGet(s, "itemType"), id = StructGet(s, "itemId");
    p.type = IsNumberKind(type) ? static_cast<int>(type.ToDouble()) : -1;
    p.id = IsNumberKind(id) ? static_cast<int>(id.ToDouble()) : -1;
    p.amount = JewelAmount(StructGet(s, "amount"), note);
    return p;
}
static bool LoadJewelRecipes(std::vector<AfkExpedition::JewelRecipe>& out, std::string& why, std::vector<std::string>* report = nullptr)
{
    out.clear();
    try {
        RValue lists = g_Yytk->CallBuiltin("variable_global_get", { RValue("craftComboList") });
        RValue results = g_Yytk->CallBuiltin("variable_global_get", { RValue("craftComboResult") });
        if (lists.m_Kind != VALUE_ARRAY || results.m_Kind != VALUE_ARRAY) { why = "the craft recipe table was not found"; return false; }
        const int n = static_cast<int>(g_Yytk->CallBuiltin("array_length", { lists }).ToDouble());
        const int m = static_cast<int>(g_Yytk->CallBuiltin("array_length", { results }).ToDouble());
        for (int i = 0; i < n && i < m; ++i) {
            RValue res = g_Yytk->CallBuiltin("array_get", { results, RValue(static_cast<double>(i)) });
            if (res.m_Kind != VALUE_OBJECT) continue;
            RValue kind = StructGet(res, "resultType");
            const int resultType = IsNumberKind(kind) ? static_cast<int>(kind.ToDouble()) : -1;
            if (!AfkExpedition::JewelRecipeType(resultType)) continue;
            AfkExpedition::JewelRecipe r; r.index = i; r.resultType = resultType;
            std::string notes, note;
            r.output = ReadJewelPart(res, note); notes += note;
            RValue in = g_Yytk->CallBuiltin("array_get", { lists, RValue(static_cast<double>(i)) });
            if (in.m_Kind == VALUE_OBJECT) { r.inputs.push_back(ReadJewelPart(in, note)); notes += " " + note; }
            else if (in.m_Kind == VALUE_ARRAY) {
                const int k = static_cast<int>(g_Yytk->CallBuiltin("array_length", { in }).ToDouble());
                for (int j = 0; j < k; ++j) {
                    r.inputs.push_back(ReadJewelPart(g_Yytk->CallBuiltin("array_get", { in, RValue(static_cast<double>(j)) }), note));
                    notes += " " + note;
                }
            }
            const bool ok = AfkExpedition::UsableJewelRecipe(r);
            if (report) {
                std::string line = "recipe " + std::to_string(i) + " type " + std::to_string(resultType) + " -> " + std::to_string(r.output.type) + ":"
                    + std::to_string(r.output.id) + " x" + std::to_string(r.output.amount) + " from";
                for (const auto& p : r.inputs) line += " " + std::to_string(p.type) + ":" + std::to_string(p.id) + " x" + std::to_string(p.amount);
                report->push_back(line + " [" + notes + "]" + (ok ? "" : " (refused)"));
            }
            if (ok) out.push_back(r);
        }
    } catch (...) { why = "reading the craft recipe table threw"; out.clear(); return false; }
    if (out.empty()) { why = "no jewel recipe was readable"; return false; }
    return true;
}
static void WriteBackSales(const std::string& why)
{
    if (g_SpoolActive) for (const auto& line : g_Conv.soldLines) { SpoolWriteLine(SpoolRecord(line)); ++g_SpoolItems; ++g_SpoolFiltered; }
    g_Conv.soldLines.clear(); g_Conv.sellPending = 0;
    if (!why.empty()) {
        g_Conv.sell = false; ++g_Conv.creditFailures; g_Conv.note = "selling stopped: " + why;
        Out("expedition " + g_Exp.id + ": " + g_Conv.note + "; those items stay in the records");
    }
}
// Selling is only safe while no report could leave the game: offline (onl
// false) and the API exchange absent or disconnected (STATIC 2026-09-23:
// ReportClient sends whenever apiExchangeConnected is true, whatever onl says).
// Menu_Controller_obj must exist, because the gold credit reads the save slot.
static bool SalesSafe(std::string& why)
{
    try {
        RValue online = g_Yytk->CallBuiltin("variable_global_get", { RValue("onl") });
        if ((online.m_Kind == VALUE_BOOL || IsNumberKind(online)) && online.ToBoolean()) { why = "the game is online"; return false; }
        RValue api;
        if (ObjectIndex("Api_Exchange_Client_obj", api) && g_Yytk->CallBuiltin("instance_exists", { api }).ToBoolean()) {
            RValue link = GetVar(g_Yytk->CallBuiltin("instance_find", { api, RValue(0.0) }), "apiExchangeConnected");
            if ((link.m_Kind == VALUE_BOOL || IsNumberKind(link)) && link.ToBoolean()) { why = "the game is connected to the Hero Siege servers"; return false; }
        }
        RValue menu;
        if (!ObjectIndex("Menu_Controller_obj", menu) || !g_Yytk->CallBuiltin("instance_exists", { menu }).ToBoolean()) { why = "the save controller is missing"; return false; }
    } catch (...) { why = "the offline checks threw"; return false; }
    return true;
}
static RValue GoldAmount(CInstance* player)
{
    RValue gold;
    try { g_Yytk->CallGameScriptEx(gold, HeroSiege::Scripts::gml_Script_GetGoldAmount.data(), player, player, {}); } catch (...) { gold = RValue(); }
    return gold;
}
// This frame's sales credited as one amount, the way a merchant sale credits
// gold: PickUpGoldCheck with a GetCounterHash taken right before it, the local
// player as self (STATIC: undefined on success, false on a stale hash). The
// gold amount must rise by exactly the sale (it is clamped at the game's gold
// cap). Anything else, or any anti-cheat report meanwhile: the items go to the
// records as filtered items and selling stops for the rest of the delivery.
static bool CreditSales(CInstance* player)
{
    if (g_Conv.soldLines.empty()) { g_Conv.sellPending = 0; return true; }
    std::string why;
    const uint64_t reportsBefore = g_ReportClientCalls.load();
    if (!SalesSafe(why)) { WriteBackSales(why); return false; }
    try {
        const RValue before = GoldAmount(player);
        RValue hash;
        AurieStatus st = g_Yytk->CallGameScriptEx(hash, HeroSiege::Scripts::gml_Script_GetCounterHash.data(), player, player, {});
        if (!AurieSuccess(st) || hash.m_Kind != VALUE_STRING) why = "GetCounterHash returned no hash";
        else {
            RValue result;
            st = g_Yytk->CallGameScriptEx(result, HeroSiege::Scripts::gml_Script_PickUpGoldCheck.data(), player, player,
                { hash, RValue(g_Conv.sellPending), RValue(1.0), RValue(), RValue(), RValue() });
            const RValue after = GoldAmount(player);
            if (!AurieSuccess(st) || (result.m_Kind == VALUE_BOOL && !result.ToBoolean())) why = "PickUpGoldCheck refused the sale";
            else if (!IsNumberKind(before) || !IsNumberKind(after)) why = "the gold amount could not be read to confirm the sale";
            else if (std::fabs(after.ToDouble() - before.ToDouble() - g_Conv.sellPending) > 0.5) why = "the gold did not rise by the sale (gold cap reached?)";
        }
    } catch (...) { why = "the gold credit threw"; }
    if (g_ReportClientCalls.load() != reportsBefore) why = "the game raised an anti-cheat report";
    if (!why.empty()) { WriteBackSales(why); return false; }
    g_Conv.sellGold += g_Conv.sellPending; g_Conv.soldItems += g_Conv.soldLines.size();
    g_Conv.soldLines.clear(); g_Conv.sellPending = 0;
    return true;
}
// Gathered fragments leave as native stacks through the same LootGroundCreate
// call a mining node uses (type, {o, b, j, c}); the spool hook records each
// stack and removes its floor object. Full 999 stacks during delivery, the
// rest when it is done. A stack that does not come back stops the break-down.
static bool CreateOutputStacks(const ReplayEnv& env, bool includePartial)
{
    bool ok = true;
    for (auto it = g_Conv.pending.begin(); it != g_Conv.pending.end() && g_Conv.prospect;) {
        int type = -1, id = -1;
        if (!AfkExpedition::ParseOutputKey(it->first, type, id)) { it = g_Conv.pending.erase(it); continue; }
        for (long long n : AfkExpedition::StackSizes(it->second, includePartial)) {
            const uint64_t before = g_SpoolItems;
            bool called = false;
            try {
                RValue def = g_Yytk->CallBuiltin("json_parse", { RValue("{\"o\":" + std::to_string(n) + ",\"b\":" + std::to_string(id) + ",\"j\":0,\"c\":0}") });
                g_CreatingOutput = true; g_ReplayActive = true; g_CtxKind = "replay"; g_CtxPacket = "prospect";
                RValue result;
                called = AurieSuccess(g_Yytk->CallGameScriptEx(result, HeroSiege::Scripts::gml_Script_LootGroundCreate.data(), env.pi, env.pi,
                    { RValue(env.px + 96.0), RValue(env.py), RValue(static_cast<double>(type)), def, RValue(), RValue() }));
            } catch (...) { called = false; }
            if (!g_PendingSpoolItem.empty()) FlushPendingSpoolItem(",\"placed\":false,\"source\":\"prospect\"");
            g_CreatingOutput = false; g_ReplayActive = false; g_CtxKind = "none"; g_CtxPacket.clear();
            if (!called || g_SpoolItems != before + 1) {
                g_Conv.prospect = false; ok = false;
                g_Conv.note = "break-down stopped: a fragment stack could not be created; the remaining fragments stay pending";
                Out("expedition " + g_Exp.id + ": " + g_Conv.note);
                break;
            }
            it->second -= n; g_Conv.created[it->first] += n; ++g_Conv.outputStacks;
        }
        if (it->second <= 0) it = g_Conv.pending.erase(it); else ++it;
    }
    return ok;
}
static std::string ConversionJson()
{
    std::ostringstream o;
    o << std::setprecision(17) << "{\"enabled\":" << (g_Conv.enabled ? "true" : "false") << ",\"sell\":" << (g_Conv.sell ? "true" : "false")
      << ",\"prospect\":" << (g_Conv.prospect ? "true" : "false") << ",\"sold_items\":" << g_Conv.soldItems << ",\"sell_gold\":" << g_Conv.sellGold
      << ",\"prospected_items\":" << g_Conv.prospectedItems << ",\"kept_items\":" << g_Conv.keptItems << ",\"output_stacks\":" << g_Conv.outputStacks
      << ",\"credit_failures\":" << g_Conv.creditFailures << ",\"recipes\":" << g_Conv.recipes.size() << ",\"note\":\"" << JsonEscape(g_Conv.note) << "\"";
    for (const auto* map : { &g_Conv.pending, &g_Conv.created }) {
        o << ",\"" << (map == &g_Conv.pending ? "pending" : "created") << "\":{";
        bool first = true;
        for (const auto& [key, amount] : *map) { o << (first ? "" : ",") << "\"" << key << "\":" << amount; first = false; }
        o << "}";
    }
    o << "}";
    return o.str();
}
// A continued delivery keeps the earlier part's totals, its not yet created
// fragments, and any path that part had to stop.
static void RestoreConversion(const RValue& saved)
{
    if (saved.m_Kind != VALUE_OBJECT) return;
    auto number = [&](const char* key) { RValue v = StructGet(saved, key); return IsNumberKind(v) && v.m_Kind != VALUE_BOOL && std::isfinite(v.ToDouble()) && v.ToDouble() >= 0 ? v.ToDouble() : 0.0; };
    g_Conv.soldItems = static_cast<uint64_t>(number("sold_items")); g_Conv.sellGold = number("sell_gold");
    g_Conv.prospectedItems = static_cast<uint64_t>(number("prospected_items")); g_Conv.keptItems = static_cast<uint64_t>(number("kept_items"));
    g_Conv.outputStacks = static_cast<uint64_t>(number("output_stacks")); g_Conv.creditFailures = static_cast<uint64_t>(number("credit_failures"));
    for (auto* map : { &g_Conv.pending, &g_Conv.created }) {
        RValue m = StructGet(saved, map == &g_Conv.pending ? "pending" : "created");
        if (m.m_Kind != VALUE_OBJECT) continue;
        RValue names = g_Yytk->CallBuiltin("variable_struct_get_names", { m });
        const int k = names.m_Kind == VALUE_ARRAY ? static_cast<int>(g_Yytk->CallBuiltin("array_length", { names }).ToDouble()) : 0;
        for (int i = 0; i < k; ++i) {
            RValue name = g_Yytk->CallBuiltin("array_get", { names, RValue(static_cast<double>(i)) });
            int type = -1, id = -1;
            if (name.m_Kind != VALUE_STRING || !AfkExpedition::ParseOutputKey(name.ToString(), type, id)) continue;
            RValue v = StructGet(m, name.ToString().c_str());
            if (IsNumberKind(v) && v.ToDouble() > 0) (*map)[name.ToString()] = static_cast<long long>(v.ToDouble());
        }
    }
    for (const char* path : { "sell", "prospect" }) {
        RValue v = StructGet(saved, path);
        if (v.m_Kind == VALUE_BOOL && !v.ToBoolean()) (std::string(path) == "sell" ? g_Conv.sell : g_Conv.prospect) = false;
    }
    RValue note = StructGet(saved, "note");
    if (note.m_Kind == VALUE_STRING && !note.ToString().empty()) g_Conv.note = note.ToString();
}
static bool InstallReportWatch()
{
    return InstallScriptDetour("ReportClient", "afk_reportclient", &Hook_ReportClient, &g_OrigReportClient, g_ReportClientHookKind);
}
// The plan asked to convert filtered items: selling needs the game offline and
// the anti-cheat report watched; breaking down needs the recipe table.
static void SetUpConversion()
{
    g_Conv = ConversionState{};
    g_Conv.enabled = true;
    std::string unsafe;
    if (!SalesSafe(unsafe)) g_Conv.note = "selling off: " + unsafe;
    else if (!InstallReportWatch()) g_Conv.note = "selling off: anti-cheat reports cannot be watched";
    else g_Conv.sell = true;
    std::string why;
    g_Conv.prospect = LoadProspectRecipes(g_Conv.recipes, why);
    if (!g_Conv.prospect) g_Conv.note += (g_Conv.note.empty() ? "" : "; ") + std::string("break-down off: ") + why;
}
static std::string ExpeditionProgressJson(const std::string& state)
{
    std::ostringstream o;
    o << std::setprecision(17);
    o << "{\"expedition_id\":\"" << JsonEscape(g_Exp.id) << "\",\"state\":\"" << JsonEscape(state) << "\""
      << ",\"packet_index\":" << g_Exp.idx << ",\"done_in_packet\":" << g_Exp.doneInPacket
      << ",\"calls_done\":" << g_Exp.callsDone << ",\"calls_total\":" << g_Exp.callsTotal << ",\"failed\":" << g_Exp.failed << ",\"skipped\":" << g_Exp.skipped
      << ",\"checkpoint_version\":2,\"plan_hash\":\"" << g_Exp.planHash << "\""
      << ",\"checkpoint_write_retries\":" << g_CheckpointWriteRetries
      << ",\"conversion\":" << ConversionJson();
    // The spool is flushed before every checkpoint: its size here is where the
    // records of this position end. After a crash the panel sets aside what was
    // written later, so a player-accepted continue never repeats a record.
    std::error_code sizeError;
    const auto spoolBytes = g_SpoolPath.empty() ? 0 : fs::file_size(g_SpoolPath, sizeError);
    o << ",\"spool_bytes\":" << (g_SpoolPath.empty() || sizeError ? std::string("null") : std::to_string(spoolBytes))
      << ",\"effective_magic_find\":" << (g_Exp.effectiveMagicFind>=0?std::to_string(g_Exp.effectiveMagicFind):"null")
      << ",\"save_committed\":" << (g_Exp.saveNote.rfind("saved (",0)==0 ? "true" : "false");
    CurrentRewards().Fields([&](const char* key, auto& value) { o << ",\"" << key << "\":" << value; });
    o << std::fixed << std::setprecision(1)
      << ",\"perf\":{\"calls\":" << g_Perf.calls << ",\"frames\":" << g_Perf.frames
      << ",\"frame_budget_ms\":" << g_Exp.frameBudgetMs << ",\"per_frame\":" << g_Exp.perFrame
      << ",\"ms_replay_frames\":" << g_Perf.frameTotal << ",\"ms_create\":" << g_Perf.create << ",\"ms_restore\":" << g_Perf.restore
      << ",\"ms_protected\":" << g_Perf.prot << ",\"ms_drop_and_items\":" << g_Perf.drop << ",\"ms_exp\":" << g_Perf.exp
      << ",\"ms_coins\":" << g_Perf.coins << ",\"ms_popups\":" << g_Perf.popups << ",\"ms_cleanup\":" << g_Perf.cleanup
      << ",\"ms_checkpoints\":" << g_Perf.checkpoint << "}" << std::defaultfloat << std::setprecision(17);
    o
      << ",\"started\":\"" << JsonEscape(g_Exp.startedAt) << "\",\"updated\":\"" << NowIso() << "\""
      << ",\"plan\":\"" << JsonEscape(g_Exp.planPath) << "\",\"spool\":\"" << JsonEscape(g_SpoolPath) << "\""
      << ",\"error\":\"" << JsonEscape(g_Exp.error) << "\",\"pause\":\"" << JsonEscape(g_Exp.pauseReason) << "\",\"saved\":\"" << JsonEscape(g_Exp.saveNote) << "\"}";
    return o.str();
}
static void ExpeditionWriteProgress(const std::string& state)
{
    auto started = std::chrono::steady_clock::now();
    g_Exp.state = state;
    g_Exp.lastCheckpoint = started;
    SpoolFlush();
    std::error_code ec; fs::create_directories(DATA_ROOT + "\\sessions", ec);
    const std::string tmp = g_Exp.progressPath + ".tmp";
    bool written = false;
    { std::ofstream f(tmp, std::ios::binary | std::ios::trunc); f << ExpeditionProgressJson(state) << "\n"; f.flush(); written = f.good(); }
    bool replaced = false; DWORD writeError = ERROR_WRITE_FAULT;
    if (written) {
        for (int attempt = 0; attempt < 51; ++attempt) {
            if (MoveFileExW(fs::path(tmp).c_str(), fs::path(g_Exp.progressPath).c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) { replaced = true; break; }
            writeError = GetLastError();
            if (writeError != ERROR_SHARING_VIOLATION && writeError != ERROR_ACCESS_DENIED && writeError != ERROR_LOCK_VIOLATION) break;
            if (attempt < 50) { ++g_CheckpointWriteRetries; Sleep(5); }
        }
    }
    if (!replaced) {
        g_Exp.running = false; g_ExpBusy = false;
        g_Exp.error = "checkpoint write failed, Windows error " + std::to_string(writeError) + "; reconciliation required";
        g_Exp.saveNote = CurrentIdentityKey() == g_Exp.identity ? PersistRewards() : "not saved: different character";
        if (g_SpoolActive) SpoolEnd(g_Exp.callsDone, "partial");
        g_Exp.state = "error";
        // A separate failure file remains writable when a reader holds the
        // old progress path open without FILE_SHARE_DELETE.
        std::ofstream failure(DATA_ROOT + "\\sessions\\" + g_Exp.id + ".failure.json", std::ios::trunc);
        failure << ExpeditionProgressJson("error") << "\n";
        Out("expedition: " + g_Exp.error);
    }
    g_Perf.checkpoint += PerfMs(started);
}
// MEASURED 2026-09-21 on Suh: Controller Room End persisted gold but left
// the character file unchanged despite +822532 live XP. A real transition
// persisted that XP. SaveLocalFile(0), self/other = live player, explicitly
// rewrites the character file; keep the Controller event for account gold.
static std::string PersistRewards()
{
    try {
        RValue cobj; if (!ObjectIndex("Controller_obj", cobj)) return "no Controller_obj";
        RValue cid = g_Yytk->CallBuiltin("instance_find", { cobj, RValue(0.0) });
        if (!g_Yytk->CallBuiltin("instance_exists", { cid }).ToBoolean()) return "no controller instance (not in a map?)";
        CInstance* ci = ResolveInstance(cid);
        if (!ci) return "controller instance unresolved";
        RValue res;
        AurieStatus st = g_Yytk->CallBuiltinEx(res, "event_perform", ci, ci, { RValue(7.0), RValue(5.0) });
        if (!AurieSuccess(st)) return "account save failed st=" + std::to_string((int)st);
        RValue pobj; if (!ObjectIndex("Player_obj", pobj)) return "character save: no player object";
        RValue pid = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
        CInstance* pi = ResolveInstance(pid);
        if (!pi) return "character save: no live player";
        st = g_Yytk->CallGameScriptEx(res, HeroSiege::Scripts::gml_Script_SaveLocalFile.data(), pi, pi, { RValue(0.0) });
        return AurieSuccess(st) ? "saved (character and account save performed)" : "character save failed st=" + std::to_string((int)st);
    } catch (...) { return "EXCEPTION"; }
}
static void ExpeditionFinish(const std::string& state)
{
    g_Exp.running = false; g_ExpBusy = false;
    if (g_Conv.enabled && g_SpoolActive) {
        ReplayEnv env; std::string err;
        if (PrepareReplayEnv(env, err)) {
            CreditSales(env.pi);
            if (state == "done" && g_Conv.prospect) CreateOutputStacks(env, true);
        } else if (!g_Conv.soldLines.empty()) WriteBackSales("");
        Out("expedition " + g_Exp.id + ": filtered items sold " + std::to_string(g_Conv.soldItems) + " for " + std::to_string(static_cast<long long>(g_Conv.sellGold))
            + " gold, broken down " + std::to_string(g_Conv.prospectedItems) + " into " + std::to_string(g_Conv.outputStacks) + " stacks"
            + (g_Conv.pending.empty() ? "" : " (fragments still pending)") + (g_Conv.note.empty() ? "" : " | " + g_Conv.note));
    }
    if (g_SpoolActive) { Out(SpoolSummaryText()); SpoolEnd(g_Exp.callsDone, state == "done" ? "summary" : "partial"); }
    std::string saved = g_Exp.callsDone > 0 ? PersistRewards() : std::string("nothing to save");
    g_Exp.saveNote = saved;
    ExpeditionWriteProgress(state);
    Out("expedition " + g_Exp.id + ": " + state + " calls=" + std::to_string(g_Exp.callsDone) + "/" + std::to_string(g_Exp.callsTotal)
        + " failed=" + std::to_string(g_Exp.failed) + (g_Exp.error.empty() ? "" : " error=" + g_Exp.error) + " | " + saved);
}
static void CmdExpeditionStart(const std::string& planPath, bool anywhere = false)
{
    if (g_Exp.running) { Out("expedition: already running (" + g_Exp.id + "); abort it first"); return; }
    if (!InstallDropItemHook()) { Out("expedition: required native hooks not installed"); return; }
    const std::string text = ReadFileText(planPath);
    if (text.empty()) { Out("expedition: cannot read plan " + planPath); return; }
    RValue plan;
    try { plan = g_Yytk->CallBuiltin("json_parse", { RValue(text) }); } catch (...) { Out("expedition: plan json_parse threw"); return; }
    if (plan.m_Kind != VALUE_OBJECT) { Out("expedition: plan is not an object"); return; }
    Expedition e;
    try{e.rewards=IndependentRewards::Parse(plan);if(e.rewards.enabled)IndependentRewards::PrepareRates();}
    catch(const std::exception& error){Out(std::string("expedition: ")+error.what());return;}
    e.planHash = AfkExpedition::Sha256::Of(text);
    e.identity = IdentityKey(StructGet(plan, "character"));
    if (e.identity.empty() || e.identity != CurrentIdentityKey()) { Out("expedition: unknown, legacy or different character identity; recalibrate"); return; }
    RValue build = StructGet(plan, "game_build");
    if (build.m_Kind != VALUE_STRING || build.ToString() != GAME_BUILD_ID) { Out("expedition: plan build differs from the game"); return; }
    RValue expectedContext=StructGet(plan,"farm_context");
    RValue expectedHash=StructGet(expectedContext,"hash");
    if(expectedContext.m_Kind==VALUE_OBJECT) {
        const auto current=FarmContextJson();
        if(current=="null") {Out("expedition: farm context unavailable");return;}
        RValue currentObject=g_Yytk->CallBuiltin("json_parse",{RValue(current)});
        RValue currentHash=StructGet(currentObject,"hash");
        bool matches=false;
        try{matches=e.rewards.enabled?IndependentRewards::PaceContext(expectedContext)==IndependentRewards::PaceContext(currentObject)
            :(expectedHash.m_Kind==VALUE_STRING && currentHash.m_Kind==VALUE_STRING && expectedHash.ToString()==currentHash.ToString());}catch(...){}
        if(!matches) {
            Out("expedition: equipment/talents/level/difficulty/settings changed; recalibrate");return;
        }
    }
    RValue zones = StructGet(plan, "zones");
    if (zones.m_Kind != VALUE_ARRAY || g_Yytk->CallBuiltin("array_length", { zones }).ToDouble() != 1) { Out("expedition: one calibrated zone required"); return; }
    RValue zone = g_Yytk->CallBuiltin("array_get", { zones, RValue(0.0) });
    RValue room = StructGet(zone, "room");
    if (room.m_Kind != VALUE_STRING || room.ToString().empty()) { Out("expedition: zone room missing"); return; }
    e.room = room.ToString(); e.anywhere = anywhere;
    if (!anywhere && e.room != CurrentRoomName()) { Out("expedition: stand in " + e.room + " before replay"); return; }
    RValue id = StructGet(plan, "expedition_id");
    if (id.m_Kind != VALUE_STRING || id.ToString().empty()) { Out("expedition: plan lacks expedition_id"); return; }
    e.id = id.ToString(); e.planPath = planPath;
    for (char c : e.id) if (!(isalnum((unsigned char)c) || c == '_' || c == '-' || c == '.')) { Out("expedition: expedition_id may only contain letters, digits, _ - ."); return; }
    if (fs::exists(DATA_ROOT + "\\sessions\\" + e.id + ".failure.json")) { Out("expedition: checkpoint failure recorded; reconciliation required"); return; }
    RValue pk = StructGet(plan, "packets");
    if (pk.m_Kind != VALUE_ARRAY) { Out("expedition: plan lacks packets[]"); return; }
    const int n = (int)g_Yytk->CallBuiltin("array_length", { pk }).ToDouble();
    for (int i = 0; i < n; ++i) {
        RValue it = g_Yytk->CallBuiltin("array_get", { pk, RValue((double)i) });
        if (it.m_Kind != VALUE_OBJECT) continue;
        RValue h = StructGet(it, "hash"), c = StructGet(it, "count");
        if (h.m_Kind != VALUE_STRING || !IsNumberKind(c)) continue;
        ExpPacketPlan pp; pp.hash = h.ToString(); pp.count = (long long)c.ToDouble();
        { RValue ex = StructGet(it, "exp"); if (IsNumberKind(ex) && ex.ToDouble() >= 0) pp.exp = ex.ToDouble(); }
        if(e.rewards.enabled && (!std::isfinite(pp.exp) || pp.exp<0 || pp.exp>=1e9)) {Out("expedition: invalid independent XP baseline or multiplier");return;}
        if (pp.count <= 0) continue;
        // refuse unknown packets up front rather than half-way through
        std::string err; LoadedPacket probe;
        if (!LoadPacket(pp.hash, probe, err, "__afk_keep_probe")) { Out("expedition: packet " + pp.hash.substr(0, 12) + ": " + err); return; }
        e.packets.push_back(pp); e.callsTotal += pp.count;
    }
    if (e.packets.empty()) { Out("expedition: plan has no replayable packets"); return; }
    RValue ex = StructGet(plan, "exp");  if (ex.m_Kind == VALUE_BOOL || IsNumberKind(ex)) e.exp = ex.ToBoolean();
    RValue gd = StructGet(plan, "gold"); if (gd.m_Kind == VALUE_BOOL || IsNumberKind(gd)) e.gold = gd.ToBoolean(); else if (gd.m_Kind == VALUE_STRING) e.gold = gd.ToString() != "none";
    RValue pf = StructGet(plan, "per_frame"); if (IsNumberKind(pf) && pf.ToDouble() >= 1) e.perFrame = (int)pf.ToDouble();
    RValue fb = StructGet(plan, "frame_budget_ms"); if (IsNumberKind(fb) && fb.ToDouble() >= 1) e.frameBudgetMs = fb.ToDouble();
    e.progressPath = DATA_ROOT + "\\sessions\\" + e.id + ".progress.json";
    e.startedAt = NowIso();
    // resume: a progress file for this id that is not done carries what was already replayed
    bool resumed = false;
    AfkExpedition::RewardTotals restored;
    RValue restoredConversion;
    {
        const std::string pt = ReadFileText(e.progressPath);
        if (pt.empty() && fs::exists(e.progressPath)) { Out("expedition: empty/unreadable checkpoint; refusing to restart"); return; }
        if (!pt.empty()) {
            RValue pr; try { pr = g_Yytk->CallBuiltin("json_parse", { RValue(pt) }); } catch (...) {}
            if (pr.m_Kind == VALUE_OBJECT) {
                RValue st = StructGet(pr, "state");
                const std::string state = st.m_Kind == VALUE_STRING ? st.ToString() : "";
                RValue hash = StructGet(pr, "plan_hash");
                const bool samePlan = hash.m_Kind == VALUE_STRING && hash.ToString() == e.planHash;
                if (hash.m_Kind == VALUE_STRING && !samePlan) { Out("expedition: plan changed under an existing id; original plan required"); return; }
                if (state == "done") { Out("expedition " + e.id + ": already done"); return; }
                RValue saved = StructGet(pr, "saved");
                const bool safeSave = saved.m_Kind == VALUE_STRING && (saved.ToString().find("saved (") == 0 || saved.ToString() == "nothing to save");
                RValue acc = StructGet(pr, "resume_accepted");
                const bool accepted = IsNumberKind(acc) && acc.ToDouble() == 1.0;
                if (!AfkExpedition::CanResume(state, samePlan, safeSave, accepted)) { Out("expedition: checkpoint is unclean, failed or legacy; automatic resume refused, reconciliation required"); return; }
                if (state == "running") Out("expedition " + e.id + ": continuing from the recorded position the player accepted (save not confirmed)");
                if (!restored.Load([&](const char* key) { RValue v = StructGet(pr, key); return IsNumberKind(v) && v.m_Kind != VALUE_BOOL ? v.ToDouble() : std::numeric_limits<double>::quiet_NaN(); })) {
                    Out("expedition: checkpoint reward counters missing/invalid"); return;
                }
                restoredConversion = StructGet(pr, "conversion");
                RValue pi = StructGet(pr, "packet_index"), dp = StructGet(pr, "done_in_packet"), cd = StructGet(pr, "calls_done"), fl = StructGet(pr, "failed"), sa = StructGet(pr, "started");
                if (IsNumberKind(pi)) e.idx = (size_t)pi.ToDouble();
                if (IsNumberKind(dp)) e.doneInPacket = (long long)dp.ToDouble();
                if (IsNumberKind(cd)) e.callsDone = (long long)cd.ToDouble();
                if (IsNumberKind(fl)) e.failed = (long long)fl.ToDouble();
                RValue sk = StructGet(pr, "skipped"); if (IsNumberKind(sk)) e.skipped = (long long)sk.ToDouble();
                if (sa.m_Kind == VALUE_STRING) e.startedAt = sa.ToString();
                if (e.idx > e.packets.size() || e.doneInPacket < 0 || (e.idx < e.packets.size() && e.doneInPacket > e.packets[e.idx].count)) { Out("expedition: invalid checkpoint position"); return; }
                long long expected = e.doneInPacket;
                for (size_t j = 0; j < e.idx; ++j) expected += e.packets[j].count;
                if (expected != e.callsDone || e.callsDone > e.callsTotal || e.failed || e.skipped) { Out("expedition: inconsistent or failed checkpoint"); return; }
                resumed = true;
            } else { Out("expedition: unreadable checkpoint; refusing to restart"); return;
            }
        }
    }
    if (!resumed && fs::exists(DATA_ROOT + "\\spool\\" + e.id + ".ndjson")) { Out("expedition: spool exists without a usable checkpoint; refusing to duplicate rewards"); return; }
    if(e.rewards.enabled) {
        try {IndependentRewards::Batch scope(e.rewards);e.effectiveMagicFind=IndependentRewards::NativeMagicFind();}
        catch(...){Out("expedition: unable to verify effective Magic Find; no rewards generated");return;}
    }
    RValue filtered = StructGet(plan, "filtered_items");
    const bool convert = filtered.m_Kind == VALUE_STRING && filtered.ToString() == "convert";
    g_Exp = std::move(e);
    g_GiveExp = g_Exp.exp; g_GoldPickup = g_Exp.gold;
    SpoolBegin(g_Exp.id);
    if (convert) SetUpConversion(); else g_Conv = ConversionState{};
    if (resumed) {
        RestoreRewards(restored);
        if (convert) RestoreConversion(restoredConversion);
    }
    if (convert) Out("expedition " + g_Exp.id + ": filtered items -> " + (g_Conv.enabled ? std::string("sell ") + (g_Conv.sell ? "on" : "off")
        + ", break-down " + (g_Conv.prospect ? "on (" + std::to_string(g_Conv.recipes.size()) + " recipes)" : "off") : std::string("kept"))
        + (g_Conv.note.empty() ? "" : " | " + g_Conv.note));
    if (g_SessionFile.empty()) SessionOpen();
    g_Perf = ReplayPerf();
    g_Exp.running = true; g_ExpBusy = true;
    InstallCloseGuard();
    ExpeditionWriteProgress("running");
    Out("expedition " + g_Exp.id + ": " + (resumed ? "resumed at " : "started, ") + std::to_string(g_Exp.callsDone) + "/" + std::to_string(g_Exp.callsTotal)
        + " calls, " + std::to_string(g_Exp.packets.size()) + " packets, " + std::to_string(g_Exp.perFrame) + " calls/frame, exp=" + (g_Exp.exp ? "on" : "off") + " gold=" + (g_Exp.gold ? "pickup" : "off"));
}
static void ExpeditionTick()
{
    if (!g_Exp.running) return;
    if (g_Exp.idx >= g_Exp.packets.size()) { ExpeditionFinish("done"); return; }
    ReplayEnv env; std::string err;
    if (!PrepareReplayEnv(env, err)) {
        // no player on screen (menu, loading): wait, keep the progress file honest
        if (g_Exp.pauseReason != err) { g_Exp.pauseReason = err; ExpeditionWriteProgress("paused"); Out("expedition " + g_Exp.id + ": paused (" + err + ")"); }
        ++g_Exp.pausedFrames; return;
    }
    const bool sameCharacter = CurrentIdentityKey() == g_Exp.identity;
    if (!sameCharacter || (!g_Exp.anywhere && CurrentRoomName() != g_Exp.room)) {
        const std::string reason = "return to the expedition character and room";
        if (g_Exp.pauseReason != reason) {
            g_Exp.pauseReason = reason;
            if (sameCharacter && !g_Exp.pausedSaved) {
                // The expedition's hero is still loaded (it left the region):
                // save now, so this pause survives a game close or crash as a
                // resumable checkpoint. The spool gets a partial summary first,
                // so it ends consistently with the saved counters.
                if (g_Conv.enabled) CreditSales(env.pi);
                if (g_SpoolActive) { FlushPendingSpoolItem(""); SpoolWriteLine(SpoolSummaryLine(g_Exp.callsDone, "partial")); }
                g_Exp.saveNote = g_Exp.callsDone > 0 ? PersistRewards() : std::string("nothing to save");
                g_Exp.pausedSaved = true;
                Out("expedition " + g_Exp.id + ": paused outside the region | " + g_Exp.saveNote);
            }
            ExpeditionWriteProgress("paused");
        }
        return;
    }
    if (!g_Exp.pauseReason.empty()) {
        Out("expedition " + g_Exp.id + ": resumed");
        g_Exp.pauseReason.clear();
        // Replaying again: the save made when pausing no longer covers the counters.
        if (g_Exp.pausedSaved) { g_Exp.pausedSaved = false; g_Exp.saveNote.clear(); }
    }
    const auto t0 = std::chrono::steady_clock::now();
    int callsThisFrame = 0;
    try {
        IndependentRewards::Batch rewardBatch(g_Exp.rewards);
        while (g_Exp.idx < g_Exp.packets.size() && callsThisFrame < g_Exp.perFrame) {
            ExpPacketPlan& pp = g_Exp.packets[g_Exp.idx];
            if (g_Exp.doneInPacket >= pp.count) { ++g_Exp.idx; g_Exp.doneInPacket = 0; g_Exp.curLoaded = false; g_Exp.curFailStreak = 0; continue; }
            if (!g_Exp.curLoaded || g_Exp.curHash != pp.hash) {
                g_Exp.cur = LoadedPacket();
                if (!LoadPacket(pp.hash, g_Exp.cur, err, "__afk_keep_expedition")) { g_Exp.error = "packet " + pp.hash.substr(0, 12) + ": " + err; ExpeditionFinish("error"); return; }
                g_Exp.cur.overrideExp = pp.exp;
                g_Exp.curLoaded = true; g_Exp.curHash = pp.hash;
            }
            CallOutcome oc = ReplayOneCall(g_Exp.cur, env);
            if (!oc.ok) {
                ++g_Exp.failed; ++g_Exp.doneInPacket; ++g_Exp.callsDone;
                g_Exp.error = "packet " + pp.hash.substr(0, 12) + " failed; stopped without substituting or retrying rewards";
                ExpeditionFinish("error"); return;
            } else g_Exp.curFailStreak = 0;
            ++g_Exp.doneInPacket; ++g_Exp.callsDone; ++callsThisFrame;
            const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            if (ms > g_Exp.frameBudgetMs) break;
        }
    } catch (...) {
        g_Exp.error = std::string("EXCEPTION at stage '") + g_ReplayStage + "'";
        g_ReplayActive = false; FreeReplayDs();
        ExpeditionFinish("error"); return;
    }
    ++g_Perf.frames;
    g_Perf.frameTotal += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    if (g_Conv.enabled) {
        CreditSales(env.pi);
        if (g_Conv.prospect && !g_Conv.pending.empty()) CreateOutputStacks(env, false);
    }
    if (g_Exp.idx >= g_Exp.packets.size() || (g_Exp.idx + 1 == g_Exp.packets.size() && g_Exp.doneInPacket >= g_Exp.packets.back().count)) { ExpeditionFinish("done"); return; }
    // A running checkpoint is never resumable, so rewriting it every frame
    // only cost frame time (and write-through flushes). Four times a second
    // is more often than the panel polls; pauses and endings still write at once.
    SpoolFlush();
    if (g_Exp.state != "running" || std::chrono::steady_clock::now() - g_Exp.lastCheckpoint >= std::chrono::milliseconds(250))
        ExpeditionWriteProgress("running");
}
static void CmdExpeditionAbort()
{
    if (!g_Exp.running) { Out("expedition: nothing running"); return; }
    if (CurrentIdentityKey() != g_Exp.identity) { Out("expedition: load the expedition character before saving an abort"); return; }
    ExpeditionFinish("aborted");
}

// ------------------------------------------------------------------ workers
// Workers (0.7.0, tools/workers.py): a hire or a skill reset costs the loaded
// hero's gold, taken the way a merchant purchase takes it (STATIC 2026-09-23:
// PickUpGoldCheck(hash, -price) with a GetCounterHash taken right before). Only
// while no report could leave the game (SalesSafe) and with ReportClient
// watched; the gold must fall by exactly the price, the game saves at once and
// a failed save gives the gold back. One receipt per request id: a request
// never pays twice.
static void WriteSmallJson(const std::string& path, const std::string& json)
{
    std::error_code ec; fs::create_directories(fs::path(path).parent_path(), ec);
    const std::string tmp = path + ".tmp";
    { std::ofstream f(tmp, std::ios::binary | std::ios::trunc); f << json << "\n"; }
    MoveFileExA(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}
// amount < 0 spends like a purchase (two arguments, the purchase's mode);
// amount > 0 gives back like a sale (mode 1).
static bool GoldChange(CInstance* player, double amount, std::string& why)
{
    RValue hash, result;
    AurieStatus st = g_Yytk->CallGameScriptEx(hash, HeroSiege::Scripts::gml_Script_GetCounterHash.data(), player, player, {});
    if (!AurieSuccess(st) || hash.m_Kind != VALUE_STRING) { why = "GetCounterHash returned no hash"; return false; }
    std::vector<RValue> args = { hash, RValue(amount) };
    if (amount > 0) { args.push_back(RValue(1.0)); args.push_back(RValue()); args.push_back(RValue()); args.push_back(RValue()); }
    st = g_Yytk->CallGameScriptEx(result, HeroSiege::Scripts::gml_Script_PickUpGoldCheck.data(), player, player, args);
    if (!AurieSuccess(st) || (result.m_Kind == VALUE_BOOL && !result.ToBoolean())) { why = "PickUpGoldCheck refused"; return false; }
    return true;
}
static double GoldNumber(CInstance* player)
{
    const RValue gold = GoldAmount(player);
    return IsNumberKind(gold) ? gold.ToDouble() : -1;
}
// The receipt of one payment (models\worker-pay-<request>.json) or credit
// (models\worker-credit-<request>.json): the same fields for both.
static std::string GoldReceiptJson(const std::string& request, bool ok, double amount, const std::string& why, double before, double after,
                                   const std::string& saved)
{
    std::ostringstream o;
    o << std::setprecision(17) << "{\"schema\":1,\"request_id\":\"" << JsonEscape(request) << "\",\"ok\":" << (ok ? "true" : "false")
      << ",\"amount\":" << (std::isfinite(amount) ? amount : -1) << ",\"gold_before\":" << before << ",\"gold_after\":" << after
      << ",\"saved\":\"" << JsonEscape(saved) << "\",\"error\":\"" << JsonEscape(why) << "\",\"character\":" << CharacterStampJson()
      << ",\"at\":\"" << NowIso() << "\",\"plugin\":\"" AFK_EXPEDITION_VERSION "\"}";
    return o.str();
}
// A worker delivery is being made (CmdWorkerDeliver); a credit waits for it.
static bool g_WorkerDelivering = false;
// worker recipes <request>: the Jeweler's recipes as the game holds them now,
// written to models\jewel-recipes-<request>.json for the panel to plan crafts.
static void CmdWorkerRecipes(const std::string& request)
{
    if (!AfkExpedition::SafeIdentifier(request, 8, 64)) { Out("worker recipes: invalid request id"); return; }
    std::vector<AfkExpedition::JewelRecipe> recipes; std::vector<std::string> report; std::string why;
    const bool ok = LoadJewelRecipes(recipes, why, &report);
    std::ostringstream o;
    o << "{\"schema\":1,\"request_id\":\"" << JsonEscape(request) << "\",\"ok\":" << (ok ? "true" : "false") << ",\"error\":\""
      << JsonEscape(ok ? "" : why) << "\",\"recipes\":[";
    for (size_t i = 0; i < recipes.size(); ++i) {
        const auto& r = recipes[i];
        o << (i ? "," : "") << "{\"index\":" << r.index << ",\"result_type\":" << r.resultType << ",\"output\":{\"type\":" << r.output.type
          << ",\"id\":" << r.output.id << ",\"amount\":" << r.output.amount << "},\"inputs\":[";
        for (size_t j = 0; j < r.inputs.size(); ++j)
            o << (j ? "," : "") << "{\"type\":" << r.inputs[j].type << ",\"id\":" << r.inputs[j].id << ",\"amount\":" << r.inputs[j].amount << "}";
        o << "]}";
    }
    o << "],\"report\":[";
    for (size_t i = 0; i < report.size(); ++i) o << (i ? "," : "") << "\"" << JsonEscape(report[i]) << "\"";
    o << "],\"at\":\"" << NowIso() << "\",\"plugin\":\"" AFK_EXPEDITION_VERSION "\"}";
    WriteSmallJson(DATA_ROOT + "\\models\\jewel-recipes-" + request + ".json", o.str());
    Out("worker recipes " + request + ": " + (ok ? std::to_string(recipes.size()) + " jewel recipes" : "failed: " + why));
}
static void CmdWorkerPay(const std::string& request, const std::string& amountText)
{
    if (!AfkExpedition::SafeIdentifier(request, 8, 64)) { Out("worker pay: invalid request id"); return; }
    const std::string path = DATA_ROOT + "\\models\\worker-pay-" + request + ".json";
    if (fs::exists(path)) { Out("worker pay " + request + ": already processed; see the receipt"); return; }
    char* end = nullptr;
    const double amount = std::strtod(amountText.c_str(), &end);
    auto receipt = [&](bool ok, const std::string& why, double before, double after, const std::string& saved) {
        WriteSmallJson(path, GoldReceiptJson(request, ok, amount, why, before, after, saved));
        Out("worker pay " + request + ": " + (ok ? "paid " : "refused ") + std::to_string(static_cast<long long>(std::isfinite(amount) ? amount : 0))
            + " gold" + (why.empty() ? "" : " | " + why));
    };
    if (!end || *end || !AfkExpedition::ValidPayment(amount)) { receipt(false, "invalid amount", -1, -1, ""); return; }
    if (g_Exp.running) { receipt(false, "a reward delivery is running", -1, -1, ""); return; }
    std::string why;
    if (!SalesSafe(why)) { receipt(false, why, -1, -1, ""); return; }
    if (!InstallReportWatch()) { receipt(false, "anti-cheat reports cannot be watched", -1, -1, ""); return; }
    ReplayEnv env;
    if (!PrepareReplayEnv(env, why)) { receipt(false, why, -1, -1, ""); return; }
    if (CurrentIdentityKey().empty()) { receipt(false, "no offline hero is loaded", -1, -1, ""); return; }
    try {
        const double had = GoldNumber(env.pi);
        if (had < 0) { receipt(false, "the gold amount could not be read", -1, -1, ""); return; }
        if (had < amount) { receipt(false, "not enough gold", had, had, ""); return; }
        const uint64_t reports = g_ReportClientCalls.load();
        const bool called = GoldChange(env.pi, -amount, why);
        const double now = GoldNumber(env.pi);
        if (g_ReportClientCalls.load() != reports) why = "the game raised an anti-cheat report";
        const bool taken = now >= 0 && std::fabs(had - now - amount) <= 0.5;
        if (!called || !taken || !why.empty()) {
            // The purchase did not run, or did something else: give back what
            // it took, then refuse.
            if (taken) { std::string ignored; GoldChange(env.pi, amount, ignored); }
            receipt(false, why.empty() ? "the gold did not fall by the price" : why, had, GoldNumber(env.pi), "");
            return;
        }
        const std::string saved = PersistRewards();
        if (saved.rfind("saved (", 0) != 0) {
            std::string refundWhy;
            const bool refunded = GoldChange(env.pi, amount, refundWhy);
            receipt(false, "the game did not save (" + saved + "); " + (refunded ? "the gold was given back" : "giving the gold back failed: " + refundWhy),
                    had, GoldNumber(env.pi), saved);
            return;
        }
        receipt(true, "", had, now, saved);
    } catch (...) { receipt(false, "the purchase threw", -1, -1, ""); }
}
// worker credit <request> <gold> (0.9): the town's coffer taken into the game.
// The mirror of a payment: the loaded offline hero receives the gold the way a
// merchant sale credits it (PickUpGoldCheck(hash, +amount, 1) with a
// GetCounterHash taken right before), under the same guards: offline and
// unlinked (SalesSafe), ReportClient watched, no delivery running, an offline
// hero loaded. The gold may not pass the game's cap and must rise by exactly
// the amount with no anti-cheat report; the game saves at once. A credit that
// does not hold (an anti-cheat report, a failed save) is taken back through
// the purchase path and refused, and the refusal says whether the take-back
// worked. One receipt per request id: a request never credits twice.
static void CmdWorkerCredit(const std::string& request, const std::string& amountText)
{
    if (!AfkExpedition::SafeIdentifier(request, 8, 64)) { Out("worker credit: invalid request id"); return; }
    const std::string path = DATA_ROOT + "\\models\\worker-credit-" + request + ".json";
    if (fs::exists(path)) { Out("worker credit " + request + ": already processed; see the receipt"); return; }
    char* end = nullptr;
    const double amount = std::strtod(amountText.c_str(), &end);
    auto receipt = [&](bool ok, const std::string& why, double before, double after, const std::string& saved) {
        WriteSmallJson(path, GoldReceiptJson(request, ok, amount, why, before, after, saved));
        Out("worker credit " + request + ": " + (ok ? "credited " : "refused ") + std::to_string(static_cast<long long>(std::isfinite(amount) ? amount : 0))
            + " gold" + (why.empty() ? "" : " | " + why));
    };
    if (!end || *end || !AfkExpedition::ValidCredit(amount)) { receipt(false, "invalid amount", -1, -1, ""); return; }
    if (g_Exp.running) { receipt(false, "a reward delivery is running", -1, -1, ""); return; }
    if (g_WorkerDelivering) { receipt(false, "a worker delivery is running", -1, -1, ""); return; }
    std::string why;
    if (!SalesSafe(why)) { receipt(false, why, -1, -1, ""); return; }
    if (!InstallReportWatch()) { receipt(false, "anti-cheat reports cannot be watched", -1, -1, ""); return; }
    ReplayEnv env;
    if (!PrepareReplayEnv(env, why)) { receipt(false, why, -1, -1, ""); return; }
    if (CurrentIdentityKey().empty()) { receipt(false, "no offline hero is loaded", -1, -1, ""); return; }
    try {
        const double had = GoldNumber(env.pi);
        if (had < 0) { receipt(false, "the gold amount could not be read", -1, -1, ""); return; }
        if (!AfkExpedition::CreditWithinCap(had, amount)) { receipt(false, "the gold would pass the game's cap", had, had, ""); return; }
        // Takes the credit back like a purchase; it worked when the gold is
        // back at its amount before the credit.
        auto takeBack = [&]() -> std::string {
            std::string backWhy;
            if (GoldChange(env.pi, -amount, backWhy) && AfkExpedition::GoldRoseBy(had, GoldNumber(env.pi), 0)) return "the gold was taken back";
            return "taking the gold back failed: " + (backWhy.empty() ? std::string("the gold did not return to its amount before the credit") : backWhy);
        };
        const uint64_t reports = g_ReportClientCalls.load();
        const bool called = GoldChange(env.pi, amount, why);
        const double now = GoldNumber(env.pi);
        if (g_ReportClientCalls.load() != reports) why = "the game raised an anti-cheat report";
        const bool given = AfkExpedition::GoldRoseBy(had, now, amount);
        if (!called || !given || !why.empty()) {
            // The credit did not run, did something else, or the game reported
            // it: take back what it gave, then refuse.
            std::string error = why.empty() ? "the gold did not rise by the amount" : why;
            if (given) error += "; " + takeBack();
            receipt(false, error, had, GoldNumber(env.pi), "");
            return;
        }
        const std::string saved = PersistRewards();
        if (saved.rfind("saved (", 0) != 0) {
            const std::string back = takeBack();
            receipt(false, "the game did not save (" + saved + "); " + back, had, GoldNumber(env.pi), saved);
            return;
        }
        receipt(true, "", had, now, saved);
    } catch (...) { receipt(false, "the credit threw", -1, -1, ""); }
}

// A worker's haul: every stack is made by the game's own ground-drop routine
// (the call a mining node uses) and goes to its own spool, like the fragments
// of a break-down; the Gem Sense share is rolled unit by unit with the
// Prospector's ore recipe and the game's dice. One result file per delivery:
// a delivery that finished is never made again, one that stopped half way
// needs review. A town delivery (0.9, id worker_town_*) goes the same way and
// may carry any good the town trades instead of only a worker's materials.
static void CmdWorkerDeliver(const std::string& planPath)
{
    const std::string text = ReadFileText(planPath);
    if (text.empty()) { Out("worker deliver: cannot read " + planPath); return; }
    RValue plan;
    try { plan = g_Yytk->CallBuiltin("json_parse", { RValue(text) }); } catch (...) { Out("worker deliver: plan json_parse threw"); return; }
    RValue idValue = StructGet(plan, "delivery_id");
    const std::string id = idValue.m_Kind == VALUE_STRING ? idValue.ToString() : "";
    if (!AfkExpedition::SafeIdentifier(id) || id.rfind("worker_", 0) != 0) { Out("worker deliver: invalid delivery id"); return; }
    const bool town = AfkExpedition::TownDelivery(id);
    const std::string resultPath = DATA_ROOT + "\\sessions\\" + id + ".result.json";
    if (fs::exists(resultPath)) {
        const std::string previous = ReadFileText(resultPath);
        Out("worker deliver " + id + (previous.find("\"state\":\"done\"") != std::string::npos ? ": already done" : ": a previous attempt stopped; review needed"));
        return;
    }
    if (g_Exp.running || g_WorkerDelivering) { Out("worker deliver: a delivery is running; try again when it is done"); return; }
    if (!InstallDropItemHook()) { Out("worker deliver: required native hooks not installed"); return; }
    std::string why;
    if (!SalesSafe(why)) { Out("worker deliver: " + why); return; }
    ReplayEnv env;
    if (!PrepareReplayEnv(env, why)) { Out("worker deliver: " + why); return; }
    if (fs::exists(DATA_ROOT + "\\spool\\" + id + ".ndjson")) { Out("worker deliver: records exist without a result; review needed"); return; }
    std::map<std::string, long long> make, prospect, outputs, created, crafted;
    std::map<int, long long> craftCounts;
    bool routeProspect = false;
    auto amountOf = [](const RValue& v) { return IsNumberKind(v) && v.m_Kind != VALUE_BOOL && std::isfinite(v.ToDouble()) && std::floor(v.ToDouble()) == v.ToDouble()
                                              ? static_cast<long long>(v.ToDouble()) : -1LL; };
    try {
        RValue items = StructGet(plan, "items");
        const int n = items.m_Kind == VALUE_ARRAY ? static_cast<int>(g_Yytk->CallBuiltin("array_length", { items }).ToDouble()) : -1;
        if (n < 0) { Out("worker deliver: plan lacks items[]"); return; }
        for (int i = 0; i < n; ++i) {
            RValue it = g_Yytk->CallBuiltin("array_get", { items, RValue(static_cast<double>(i)) });
            const long long type = amountOf(StructGet(it, "type")), item = amountOf(StructGet(it, "id")), amount = amountOf(StructGet(it, "amount"));
            // A town delivery may carry any TownGood (keys 12, fragments and
            // cards 13, materials 14, socketables 15), made below exactly like
            // a worker's materials: {o, b, j:0, c:0} stacks of at most 999
            // through LootGroundCreate. NOT YET VERIFIED IN THE GAME: only ores,
            // jewel materials and Satanic Crystal Fragments (type 14) have been
            // made through this path live (2026-09-24). Live creation of types
            // 12, 13 and 15 through this path still has to be verified in the
            // game. Every other delivery keeps the WorkerMaterial rule.
            if (!AfkExpedition::DeliverableItem(town, static_cast<int>(type), static_cast<int>(item)) || amount < 1) { Out("worker deliver: refused item " + std::to_string(type) + ":" + std::to_string(item)); return; }
            long long& total = make[AfkExpedition::OutputKey(static_cast<int>(type), static_cast<int>(item))];
            total += amount;
            if (total > AfkExpedition::kMaxWorkerAmount) { Out("worker deliver: amount too large"); return; }
        }
        RValue units = StructGet(plan, "prospect");
        if (units.m_Kind == VALUE_OBJECT) {
            RValue names = g_Yytk->CallBuiltin("variable_struct_get_names", { units });
            const int k = names.m_Kind == VALUE_ARRAY ? static_cast<int>(g_Yytk->CallBuiltin("array_length", { names }).ToDouble()) : 0;
            for (int i = 0; i < k; ++i) {
                RValue name = g_Yytk->CallBuiltin("array_get", { names, RValue(static_cast<double>(i)) });
                int type = -1, ore = -1;
                const long long amount = amountOf(StructGet(units, name.ToString().c_str()));
                if (name.m_Kind != VALUE_STRING || !AfkExpedition::ParseOutputKey(name.ToString(), type, ore) || type != AfkExpedition::kMaterialType
                    || !AfkExpedition::WorkerOre(ore) || amount < 0 || amount > AfkExpedition::kMaxWorkerAmount) { Out("worker deliver: refused prospect entry"); return; }
                if (amount) prospect[name.ToString()] = amount;
            }
        }
        // A Jeweler's crafts: [{recipe, count}] by the game's recipe index.
        RValue crafts = StructGet(plan, "crafts");
        if (crafts.m_Kind == VALUE_ARRAY) {
            const int c = static_cast<int>(g_Yytk->CallBuiltin("array_length", { crafts }).ToDouble());
            for (int i = 0; i < c; ++i) {
                RValue row = g_Yytk->CallBuiltin("array_get", { crafts, RValue(static_cast<double>(i)) });
                const long long recipe = amountOf(StructGet(row, "recipe")), count = amountOf(StructGet(row, "count"));
                if (recipe < 0 || recipe > 100000 || count < 1 || count > AfkExpedition::kMaxWorkerAmount) { Out("worker deliver: refused craft entry"); return; }
                craftCounts[static_cast<int>(recipe)] += count;
            }
        }
        // route_prospect: the Gem Sense share is rolled with the game's recipe and
        // dice as always, but recorded for the Jeweler's stock instead of made.
        RValue route = StructGet(plan, "route_prospect");
        routeProspect = route.m_Kind == VALUE_BOOL && route.ToBoolean();
    } catch (...) { Out("worker deliver: reading the plan threw"); return; }
    if (!prospect.empty()) {
        std::vector<AfkExpedition::ProspectRecipe> recipes;
        if (!LoadProspectRecipes(recipes, why, nullptr, true)) { Out("worker deliver: " + why); return; }
        for (const auto& [key, units] : prospect) {
            int type = -1, ore = -1; AfkExpedition::ParseOutputKey(key, type, ore);
            const AfkExpedition::ProspectRecipe* recipe = AfkExpedition::FindOreRecipe(ore, recipes);
            if (!recipe) { Out("worker deliver: the Prospector has no recipe for ore " + key); return; }
            for (long long u = 0; u < units; ++u) {
                int outType = -1, outId = -1; long long amount = 0;
                if (AfkExpedition::ProspectYieldEach(*recipe, [] { return GameRandom(99); }, [](int count) { return GameRandom(count - 1); }, outType, outId, amount)
                    && AfkExpedition::WorkerMaterial(outType, outId))
                    outputs[AfkExpedition::OutputKey(outType, outId)] += amount;
            }
        }
    }
    if (!craftCounts.empty()) {
        std::vector<AfkExpedition::JewelRecipe> jewels;
        if (!LoadJewelRecipes(jewels, why)) { Out("worker deliver: " + why); return; }
        for (const auto& [index, count] : craftCounts) {
            auto it = std::find_if(jewels.begin(), jewels.end(), [&](const AfkExpedition::JewelRecipe& r) { return r.index == index; });
            if (it == jewels.end()) { Out("worker deliver: recipe " + std::to_string(index) + " is not a jewel recipe of this game"); return; }
            long long& total = crafted[AfkExpedition::OutputKey(it->output.type, it->output.id)];
            total += it->output.amount * count;
            if (total > AfkExpedition::kMaxWorkerAmount) { Out("worker deliver: too many jewels in one delivery"); return; }
        }
    }
    g_WorkerDelivering = true;
    g_Conv = ConversionState{};
    SpoolBegin(id);
    uint64_t stacks = 0; std::string error;
    std::map<std::string, long long> all = make;
    if (!routeProspect) for (const auto& [key, amount] : outputs) all[key] += amount;
    for (const auto& [key, amount] : crafted) all[key] += amount;
    g_OutputSource = "worker";
    for (const auto& [key, total] : all) {
        int type = -1, item = -1; AfkExpedition::ParseOutputKey(key, type, item);
        for (long long n : AfkExpedition::StackSizes(total, true)) {
            const uint64_t before = g_SpoolItems;
            bool called = false;
            try {
                RValue def = g_Yytk->CallBuiltin("json_parse", { RValue("{\"o\":" + std::to_string(n) + ",\"b\":" + std::to_string(item) + ",\"j\":0,\"c\":0}") });
                g_CreatingOutput = true; g_ReplayActive = true; g_CtxKind = "replay"; g_CtxPacket = "worker";
                RValue result;
                called = AurieSuccess(g_Yytk->CallGameScriptEx(result, HeroSiege::Scripts::gml_Script_LootGroundCreate.data(), env.pi, env.pi,
                    { RValue(env.px + 96.0), RValue(env.py), RValue(static_cast<double>(type)), def, RValue(), RValue() }));
            } catch (...) { called = false; }
            if (!g_PendingSpoolItem.empty()) FlushPendingSpoolItem(",\"placed\":false,\"source\":\"worker\"");
            g_CreatingOutput = false; g_ReplayActive = false; g_CtxKind = "none"; g_CtxPacket.clear();
            if (!called || g_SpoolItems != before + 1) { error = "a stack of " + key + " could not be created"; break; }
            created[key] += n; ++stacks;
        }
        if (!error.empty()) break;
    }
    g_OutputSource = "prospect";
    SpoolEnd(static_cast<long long>(stacks), error.empty() ? "summary" : "partial");
    g_WorkerDelivering = false;
    std::ostringstream o;
    auto map = [&](const std::map<std::string, long long>& m) {
        std::string s = "{"; bool first = true;
        for (const auto& [k, v] : m) { s += std::string(first ? "" : ",") + "\"" + k + "\":" + std::to_string(v); first = false; }
        return s + "}";
    };
    o << "{\"schema\":1,\"delivery_id\":\"" << JsonEscape(id) << "\",\"state\":\"" << (error.empty() ? "done" : "error") << "\",\"error\":\"" << JsonEscape(error)
      << "\",\"created\":" << map(created) << ",\"requested\":" << map(make) << ",\"prospected\":" << map(prospect) << ",\"prospect_outputs\":" << map(outputs)
      << ",\"routed\":" << (routeProspect ? "true" : "false") << ",\"crafted\":" << map(crafted)
      << ",\"stacks\":" << stacks << ",\"items\":" << g_SpoolItems << ",\"spool\":\"" << JsonEscape(DATA_ROOT + "\\spool\\" + id + ".ndjson")
      << "\",\"character\":" << CharacterStampJson() << ",\"room\":\"" << JsonEscape(CurrentRoomName()) << "\",\"at\":\"" << NowIso()
      << "\",\"plugin\":\"" AFK_EXPEDITION_VERSION "\"}";
    WriteSmallJson(resultPath, o.str());
    Out("worker deliver " + id + ": " + (error.empty() ? "done" : "error: " + error) + ", " + std::to_string(stacks) + " stacks");
}

// ------------------------------------------------ closing the game window
// Closing the window during delivery used to leave a "running" checkpoint
// that needs manual review (MEASURED 2026-09-23: a 2 h claim stopped at 78.6%
// when the game was closed). While a delivery is active the first close
// request is held for one frame: delivery stops exactly like Pause (game save
// and a resumable "aborted" checkpoint), then the same request is posted
// again and the game closes normally. A repeated request passes at once, so a
// stuck frame can never keep the window open.
static HWND g_GameWindow = nullptr;
static WNDPROC g_OriginalWndProc = nullptr;
static bool g_CloseRequested = false, g_CloseAllowed = false;
static UINT g_CloseMessage = 0; static WPARAM g_CloseWParam = 0; static LPARAM g_CloseLParam = 0;
static bool IsCloseMessage(UINT msg, WPARAM wp) { return msg == WM_CLOSE || (msg == WM_SYSCOMMAND && (wp & 0xFFF0) == SC_CLOSE); }
static LRESULT CALLBACK AfkCloseGuardProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if (IsCloseMessage(msg, wp) && g_ExpBusy && !g_CloseAllowed && !g_CloseRequested) {
        g_CloseRequested = true; g_CloseMessage = msg; g_CloseWParam = wp; g_CloseLParam = lp;
        return 0;
    }
    return CallWindowProcW(g_OriginalWndProc, hwnd, msg, wp, lp);
}
static BOOL CALLBACK FindOwnTopWindow(HWND hwnd, LPARAM out)
{
    DWORD pid = 0; GetWindowThreadProcessId(hwnd, &pid);
    if (pid == GetCurrentProcessId() && IsWindowVisible(hwnd) && !GetWindow(hwnd, GW_OWNER)) { *reinterpret_cast<HWND*>(out) = hwnd; return FALSE; }
    return TRUE;
}
static void InstallCloseGuard()
{
    if (g_OriginalWndProc) return;
    HWND hwnd = nullptr;
    try { RValue h = g_Yytk->CallBuiltin("window_handle", {}); if (h.m_Kind == VALUE_PTR) hwnd = static_cast<HWND>(h.m_Pointer); } catch (...) {}
    if (!hwnd || !IsWindow(hwnd)) { hwnd = nullptr; EnumWindows(FindOwnTopWindow, reinterpret_cast<LPARAM>(&hwnd)); }
    if (!hwnd) { Out("close guard: game window not found; closing during delivery stays unprotected"); return; }
    g_OriginalWndProc = reinterpret_cast<WNDPROC>(SetWindowLongPtrW(hwnd, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(AfkCloseGuardProc)));
    if (g_OriginalWndProc) g_GameWindow = hwnd;
    else Out("close guard: could not attach to the game window");
}
static void HandleCloseRequest()
{
    if (!g_CloseRequested) return;
    if (g_Exp.running && CurrentIdentityKey() == g_Exp.identity) {
        Out("expedition " + g_Exp.id + ": game window closing - saving and pausing delivery");
        ExpeditionFinish("aborted");
    } else if (g_Exp.running) {
        Out("expedition " + g_Exp.id + ": game window closing without the expedition character loaded; checkpoint left " + g_Exp.state);
    }
    g_CloseAllowed = true; g_CloseRequested = false;
    if (g_GameWindow) PostMessageW(g_GameWindow, g_CloseMessage, g_CloseWParam, g_CloseLParam);
}
static void CmdExpeditionStatus()
{
    if (g_Exp.id.empty()) { Out("expedition: none this session"); return; }
    Out("expedition " + g_Exp.id + ": " + (g_Exp.running ? (g_Exp.pauseReason.empty() ? "running" : "paused: " + g_Exp.pauseReason) : g_Exp.state)
        + " calls=" + std::to_string(g_Exp.callsDone) + "/" + std::to_string(g_Exp.callsTotal) + " failed=" + std::to_string(g_Exp.failed)
        + " packet " + std::to_string(g_Exp.idx + (g_Exp.idx < g_Exp.packets.size() ? 1 : 0)) + "/" + std::to_string(g_Exp.packets.size())
        + " items=" + std::to_string(g_SpoolItems) + " gold=" + std::to_string((long long)g_SpoolGold) + " exp=" + std::to_string((long long)g_GiveExpSum)
        + " progress=" + g_Exp.progressPath);
}

static void CmdPackets()
{
    std::error_code ec; int n = 0;
    for (auto& e : fs::directory_iterator(PacketsDir(), ec)) {
        if (!e.is_regular_file()) continue;
        const std::string text = ReadFileText(e.path().string());
        RValue pk;
        try { pk = g_Yytk->CallBuiltin("json_parse", { RValue(text) }); } catch (...) { continue; }
        RValue mk = StructGet(pk, "monster_key"), obj = StructGet(pk, "self_object"), rk = StructGet(pk, "rank"), pr = StructGet(pk, "protected");
        Out("  " + e.path().filename().string().substr(0, 12) + "  " + (mk.m_Kind == VALUE_STRING && !mk.ToString().empty() ? mk.ToString() : (obj.m_Kind == VALUE_STRING ? obj.ToString() : "?"))
            + "  rank " + (IsNumberKind(rk) ? std::to_string((int)rk.ToDouble()) : "?") + (pr.m_Kind == VALUE_OBJECT ? "  [deep]" : "  [old]"));
        ++n;
    }
    Out("packets: " + std::to_string(n));
}

// ------------------------------------------------------------- coexistence
static std::string LoadedNeighbours()
{
    std::string s;
    struct { const char* dll; const char* who; } known[] = {
        { "BloodPactPlugin.dll", "ForgePact" },
        { "HSOfflineTrackerProducer.dll", "HS Offline Tracker producer" },
        { "YYToolkit.dll", "YYToolkit" },
        { "AurieCore.dll", "Aurie" },
    };
    for (auto& k : known) {
        if (GetModuleHandleA(k.dll)) { if (!s.empty()) s += ", "; s += k.who; }
    }
    return s.empty() ? "(none detected)" : s;
}

// ------------------------------------------------------------------ commands
#include "TestSession.inl"
#include "DefenseLab.inl"   // research R2: the live-defense lab (afk def ...)
#include "InGameUI.inl"     // research R1: the in-game window lab (afk ui ...)

static void CmdStatus()
{
    Out("HS AFK Expedition " AFK_EXPEDITION_VERSION);
    Out("  build id     : " + GAME_BUILD_ID);
    Out("  neighbours   : " + LoadedNeighbours());
    Out("  character    : " + CharacterStampJson());
    Out("  DropItem hook: " + g_DropItemHookKind);
    Out("  CreateItem   : " + g_CreateItemHookKind);
    Out("  LootGround   : " + g_LootGroundHookKind);
    Out("  capture      : " + std::string(g_CaptureOn ? "ON" : "off") + std::string(g_CaptureAll ? " (full)" : ""));
    Out("  calls=" + std::to_string(g_DropItemCalls.load()) + " kills=" + std::to_string(g_KillsCaptured.load())
        + " snapshots=" + std::to_string(g_SnapshotsTaken.load()) + " packets_written=" + std::to_string(g_PacketsWritten.load())
        + " items_logged=" + std::to_string(g_ItemsLogged.load()) + " errors=" + std::to_string(g_CaptureErrors.load()));
    Out("  room         : " + CurrentRoomName());
    Out("  data         : " + DATA_ROOT);
    Out("  session      : " + (g_SessionFile.empty() ? std::string("-") : g_SessionFile));
}

static void CmdCaptureStats()
{
    std::lock_guard<std::mutex> lk(g_CaptureMutex);
    Out("capture: kills=" + std::to_string(g_KillsCaptured.load()) + " distinct_packets=" + std::to_string(g_IdentityToHash.size()));
    for (auto& kv : g_KillsByMonster) Out("  " + kv.first + " x" + std::to_string(kv.second));
}

static bool ResearchArguments(std::istream& input, CInstance* self, std::vector<RValue>& args)
{
    std::string token;
    while (input >> token) {
        AfkExpedition::ResearchArgument argument;
        if (!AfkExpedition::ParseResearchArgument(token,argument)) {
            Out("research command: invalid argument '" + token + "'; call not executed"); return false;
        }
        using Kind=AfkExpedition::ResearchArgumentKind;
        switch(argument.kind) {
        case Kind::Number: args.emplace_back(argument.number); break;
        case Kind::Undefined: args.emplace_back(); break;
        case Kind::Text: args.emplace_back(argument.text); break;
        case Kind::Self:
            if (!self) { Out("research command: self unavailable; call not executed"); return false; }
            args.push_back(self->ToRValue()); break;
        case Kind::Player: {
            RValue object;
            if (!ObjectIndex(HeroSiege::Objects::GetObjectName(HeroSiege::Objects::GameObject::Player_obj).data(),object) ||
                g_Yytk->CallBuiltin("instance_number",{object}).ToDouble()!=1) {
                Out("research command: me requires one live player; call not executed"); return false;
            }
            args.push_back(g_Yytk->CallBuiltin("instance_find",{object,RValue(0.0)})); break;
        }
        }
    }
    return true;
}
static void RunCommand(const std::string& raw)
{
    std::istringstream ss(raw);
    std::string w0, w1, w2;
    ss >> w0 >> w1 >> w2;
    // a failed extraction leaves the string untouched, so clear it first
    // (MEASURED 2026-09-18: "afk call X" passed X's name back as a phantom argument)
    if (w0 == "afk") { w0 = w1; w1 = w2; w2.clear(); ss >> w2; }
    if (w0.empty()) return;
    if(w0=="rewards" && w1=="probe"){
        if(g_Exp.running || g_CaptureOn){Out("rewards probe: finish replay/recording first");return;}
        IndependentRewards::Probe();return;
    }

    if (w0 == "status") { CmdStatus(); return; }
    if (w0 == "def") { DefenseLab::Command(w1, w2, ss); return; }
    if (w0 == "ui") { InGameUI::Register(); InGameUI::Command(w1, w2, ss); return; }
    if (w0 == "convert" && w1 == "probe") {
        // Research: filtered-item conversion against the live game, outside any delivery.
        //   afk convert probe          offline flag, report watcher, recipe table
        //   afk convert probe credit   also credits 1 gold through the sale path
        //   afk convert probe make     also drops 1 Satanic Crystal Fragment at the player
        if (g_Exp.running) { Out("convert probe: finish the delivery first"); return; }
        try {
            RValue online = g_Yytk->CallBuiltin("variable_global_get", { RValue("onl") });
            Out("convert probe: onl=" + Stringify(online) + " (" + KindName(online) + ")");
            const bool watch = InstallReportWatch();
            Out("convert probe: ReportClient watch " + std::string(watch ? "on" : "OFF") + " [" + g_ReportClientHookKind + "] reports so far=" + std::to_string(g_ReportClientCalls.load()));
            std::string unsafe;
            const bool safe = SalesSafe(unsafe);
            Out("convert probe: selling " + std::string(safe ? "safe (offline, no server link, save controller present)" : "NOT safe: " + unsafe));
            std::vector<AfkExpedition::ProspectRecipe> recipes; std::vector<std::string> lines; std::string why;
            const bool loaded = LoadProspectRecipes(recipes, why, &lines);
            for (const auto& line : lines) Out("convert probe: " + line);
            Out("convert probe: " + std::to_string(recipes.size()) + " recipes for Satanic and above" + (loaded ? "" : " | " + why));
            ReplayEnv env; std::string err;
            if (!PrepareReplayEnv(env, err)) { Out("convert probe: " + err); return; }
            if (w2 == "credit") {
                if (!watch || !safe) { Out("convert probe: no credit unless selling is safe and reports are watched"); return; }
                // The production path with one gold and a stand-in record.
                const RValue before = GoldAmount(env.pi);
                g_Conv = ConversionState{}; g_Conv.enabled = true; g_Conv.sell = true;
                g_Conv.soldLines.push_back("\"kind\":\"probe\""); g_Conv.sellPending = 1;
                const uint64_t reports = g_ReportClientCalls.load();
                const bool spool = g_SpoolActive; g_SpoolActive = false;   // a refused probe writes no spool record
                const bool ok = CreditSales(env.pi);
                g_SpoolActive = spool;
                const std::string note = g_Conv.note;
                g_Conv = ConversionState{};
                Out("convert probe: gold " + Stringify(before) + " -> " + Stringify(GoldAmount(env.pi)) + ", reports during the credit="
                    + std::to_string(g_ReportClientCalls.load() - reports) + (ok ? " | credit accepted" : " | credit NOT accepted: " + note));
            }
            if (w2 == "make") {
                RValue def = g_Yytk->CallBuiltin("json_parse", { RValue("{\"o\":1,\"b\":60,\"j\":0,\"c\":0}") });
                RValue result;
                const AurieStatus st = g_Yytk->CallGameScriptEx(result, HeroSiege::Scripts::gml_Script_LootGroundCreate.data(), env.pi, env.pi,
                    { RValue(env.px + 48.0), RValue(env.py), RValue(14.0), def, RValue(), RValue() });
                std::string item = "?";
                try { if (g_Yytk->CallBuiltin("instance_exists", { result }).ToBoolean()) item = Stringify(GetVar(result, "item")).substr(0, 600); } catch (...) {}
                Out("convert probe: LootGroundCreate(type 14, Satanic Crystal Fragment x1) st=" + std::to_string(static_cast<int>(st)) + " -> " + KindName(result) + " item=" + item);
            }
        } catch (...) { Out("convert probe: EXCEPTION"); }
        return;
    }
    if (w0 == "session" && w1 == "state") { std::string mode;ss>>mode;TestSession::State(w2,mode=="isolated"); return; }
    if (w0 == "model") { Out("Combat research is archived; this is the measured AFK product."); return; }
    if (w0 == "hook") { InstallDropItemHook(); return; }
    if (w0 == "capture") {
        if (w1 == "on") {
            if (!InstallDropItemHook()) { Out("capture: native hook not installed, refusing to arm"); return; }
            g_CaptureAll = (w2 == "full");
            if (!g_CaptureOn || g_SessionFile.empty()) SessionOpen();
            g_CaptureOn = true;
            Out(std::string("capture: ON") + std::string(g_CaptureAll ? " (full snapshot every kill)" : "") + " -> " + g_SessionFile);
        } else if (w1 == "off") {
            g_CaptureOn = false;
            SessionAppend("{\"kind\":\"session_stop\",\"t\":\"" + NowIso() + "\",\"kills\":" + std::to_string(g_KillsCaptured.load()) + "}");
            Out("capture: off (kills=" + std::to_string(g_KillsCaptured.load()) + ")");
        } else if (w1 == "stats") {
            CmdCaptureStats();
        } else if (w1 == "reset") {
            std::lock_guard<std::mutex> lk(g_CaptureMutex);
            g_IdentityToHash.clear(); g_KillsByMonster.clear();
            g_KillsCaptured = 0; g_SnapshotsTaken = 0; g_PacketsWritten = 0; g_CaptureErrors = 0;
            g_SessionFile.clear();
            Out("capture: counters reset (packet files kept)");
        } else {
            Out("capture: usage -> capture on [full] | off | stats | reset");
        }
        return;
    }
    if (w0 == "packets") { CmdPackets(); return; }
    if (w0 == "replaylive") {
        // replaylive <ObjName> [nth] [times]: run the drop routine on a LIVE
        // instance with reconstructed arguments (no ghost). Separates "the
        // ghost is missing something" from "the arguments/zone are wrong".
        if (!g_OrigDropItem) { Out("replaylive: hook not installed"); return; }
        if (w1.empty()) { Out("replaylive: usage -> replaylive <ObjName> [nth] [times]"); return; }
        try {
            RValue oi;
            if (!ObjectIndex(w1, oi)) { Out("replaylive: object not found: " + w1); return; }
            int nth = w2.empty() ? 0 : std::atoi(w2.c_str());
            std::string w3; ss >> w3; int times = w3.empty() ? 1 : std::atoi(w3.c_str()); if (times < 1) times = 1; if (times > 5000) times = 5000;
            RValue id = g_Yytk->CallBuiltin("instance_find", { oi, RValue((double)nth) });
            if (!g_Yytk->CallBuiltin("instance_exists", { id }).ToBoolean()) { Out("replaylive: no live instance"); return; }
            CInstance* ci = ResolveInstance(id);
            if (!ci) { Out("replaylive: cannot resolve instance"); return; }
            if (g_SessionFile.empty()) SessionOpen();
            const std::map<int, int> before = Census();
            const int lootBefore = CountInstances("Loot_Ground_obj"), coinBefore = CountInstances("Coin_obj");
            std::vector<RValue> av = SyntheticDropArgs(id);
            int ok = 0;
            for (int t = 0; t < times; ++t) {
                std::vector<RValue> a2 = av; std::vector<RValue*> ap; for (auto& a : a2) ap.push_back(&a);
                RValue result; g_ReplayActive = true; g_CtxKind = "replaylive"; g_CtxPacket = w1;
                try { g_OrigDropItem(ci, ci, result, (int)a2.size(), ap.data()); ++ok; } catch (...) {}
                g_ReplayActive = false; g_CtxKind = "none"; g_CtxPacket.clear();
            }
            Out("replaylive " + w1 + " x" + std::to_string(times) + ": ok=" + std::to_string(ok) + "; object counts that changed:");
            ReportCensusDiff(before, Census());
            DestroyNewPickups(lootBefore, coinBefore);
        } catch (...) { Out("replaylive: EXCEPTION"); g_ReplayActive = false; }
        return;
    }
    if (w0 == "census") {
        // census mark  -> remember the current per-object counts
        // census diff  -> what changed since the mark (loot that a replay created a few frames later, etc.)
        static std::map<int, int> s_Mark;
        if (w1 == "mark") { s_Mark = Census(); Out("census: marked " + std::to_string(s_Mark.size()) + " objects with instances"); }
        else if (w1 == "diff") { Out("census: changes since mark:"); ReportCensusDiff(s_Mark, Census()); }
        else Out("census: usage -> census mark | census diff");
        return;
    }
    if (w0 == "snapshot") {
        // snapshot <ObjName> [nth]: packet from a LIVE instance with reconstructed
        // arguments - lets replay mechanics be tested without a real kill.
        if (w1.empty()) { Out("snapshot: usage -> snapshot <ObjName> [nth]"); return; }
        try {
            RValue oi;
            if (!ObjectIndex(w1, oi)) { Out("snapshot: object not found: " + w1); return; }
            int nth = w2.empty() ? 0 : std::atoi(w2.c_str());
            RValue id = g_Yytk->CallBuiltin("instance_find", { oi, RValue((double)nth) });
            if (!g_Yytk->CallBuiltin("instance_exists", { id }).ToBoolean()) { Out("snapshot: no live instance " + std::to_string(nth) + " of " + w1); return; }
            const bool wasOn = g_CaptureAll; g_CaptureAll = true;    // always a fresh full snapshot
            CaptureKill(id, SyntheticDropArgs(id), true);
            g_CaptureAll = wasOn;
        } catch (...) { Out("snapshot: EXCEPTION"); }
        return;
    }
    if (w0 == "replay") {
        // replay <packet-prefix|latest> [times] [clean] [spool <expedition_id>]
        if (w1.empty()) { Out("replay: usage -> replay <packet-prefix|latest> [times] [clean] [spool <id>]"); return; }
        int times = 1; bool clean = false; std::string spoolId;
        std::vector<std::string> rest; if (!w2.empty()) rest.push_back(w2);
        for (std::string w; ss >> w;) rest.push_back(w);
        g_GiveExp = true; g_GoldPickup = true;
        for (size_t i = 0; i < rest.size(); ++i) {
            if (rest[i] == "clean") clean = true;
            else if (rest[i] == "noexp") g_GiveExp = false;
            else if (rest[i] == "nogold") g_GoldPickup = false;
            else if (rest[i] == "spool" && i + 1 < rest.size()) { spoolId = rest[++i]; }
            else times = std::atoi(rest[i].c_str());
        }
        if (times < 1) times = 1; if (times > 20000) times = 20000;
        CmdReplay(w1, times, clean, spoolId);
        return;
    }
    if (w0 == "expedition") {
        // expedition start <plan.json> | status | abort
        if (w1 == "start") {
            if (w2.empty()) { Out("expedition: usage -> expedition start <plan.json>"); return; }
            std::string rest; std::getline(ss, rest); std::string path = w2 + rest;
            const std::string flag = " --anywhere";
            const bool anywhere = path.size() > flag.size() && path.compare(path.size() - flag.size(), flag.size(), flag) == 0;
            if (anywhere) path.resize(path.size() - flag.size());
            CmdExpeditionStart(path, anywhere); return;
        }
        if (w1 == "status") { CmdExpeditionStatus(); return; }
        if (w1 == "abort") { CmdExpeditionAbort(); return; }
        Out("expedition: usage -> expedition start <plan.json> | status | abort"); return;
    }
    if (w0 == "save") { Out("save: " + PersistRewards()); return; }
    if (w0 == "worker") {
        // worker pay <request-id> <gold> | worker credit <request-id> <gold> | worker deliver <plan.json> (tools/workers.py)
        if (w1 == "pay") { std::string amount; ss >> amount; CmdWorkerPay(w2, amount); return; }
        if (w1 == "credit") { std::string amount; ss >> amount; CmdWorkerCredit(w2, amount); return; }
        if (w1 == "deliver") { std::string rest; std::getline(ss, rest); CmdWorkerDeliver(w2 + rest); return; }
        if (w1 == "recipes") { CmdWorkerRecipes(w2); return; }
        Out("worker: usage -> worker pay <request-id> <gold> | worker credit <request-id> <gold> | worker deliver <plan.json> | worker recipes <request-id>"); return;
    }
    if (w0 == "goto") {
        // goto <RoomName>: travel with the game's own RoomGoto (what a portal
        // uses), self = the controller. Research / automation.
        if (w1.empty()) { Out("goto: usage -> goto <RoomName>"); return; }
        if (!AfkExpedition::ResearchRoomTravelAllowed(w1)) { Out("goto: menu/loading transitions require the game's normal exit flow; refused"); return; }
        try {
            RValue rm = g_Yytk->CallBuiltin("asset_get_index", { RValue(w1) });
            if (rm.m_Kind == VALUE_UNDEFINED || !g_Yytk->CallBuiltin("room_exists", { rm }).ToBoolean()) { Out("goto: no room named " + w1); return; }
            RValue cobj; if (!ObjectIndex("Controller_obj", cobj)) { Out("goto: no Controller_obj"); return; }
            RValue cid = g_Yytk->CallBuiltin("instance_find", { cobj, RValue(0.0) });
            CInstance* ci = ResolveInstance(cid);
            if (!ci) { Out("goto: no controller instance"); return; }
            RValue res;
            AurieStatus st = g_Yytk->CallGameScriptEx(res, "gml_Script_RoomGoto", ci, ci, { rm });
            Out("goto " + w1 + ": st=" + std::to_string((int)st) + " -> " + Stringify(res).substr(0, 80));
        } catch (...) { Out("goto: EXCEPTION"); }
        return;
    }
    if (w0 == "builtinon") {
        // builtinon <Obj> <nth> <builtin> [args...]: run a builtin with self = a
        // live instance of Obj (e.g. event_perform 7 5 = the instance's Room End
        // event). Research only.
        std::string w3; ss >> w3;
        if (w1.empty() || w2.empty() || w3.empty()) { Out("builtinon: usage -> builtinon <Obj> <nth> <builtin> [args...]"); return; }
        int ordinal;
        if (!AfkExpedition::ParseResearchIndex(w2,ordinal)) { Out("builtinon: invalid instance ordinal; call not executed"); return; }
        try {
            RValue oi; if (!ObjectIndex(w1, oi)) { Out("builtinon: object not found"); return; }
            RValue id = g_Yytk->CallBuiltin("instance_find", { oi, RValue((double)ordinal) });
            CInstance* ci = ResolveInstance(id);
            if (!ci) { Out("builtinon: instance missing"); return; }
            std::vector<RValue> args;
            if (!ResearchArguments(ss,ci,args)) return;
            RValue res;
            AurieStatus st = g_Yytk->CallBuiltinEx(res, w3.c_str(), ci, ci, args);
            Out("builtinon " + w1 + "." + w3 + "(" + std::to_string(args.size()) + " args): st=" + std::to_string((int)st) + " -> " + Stringify(res).substr(0, 300));
        } catch (...) { Out("builtinon: EXCEPTION"); }
        return;
    }
    if (w0 == "callid") {
        // callid <instanceId> <Script> [args...]: run a game script with self =
        // the instance with that id (other = the same). Research / automation.
        if (w1.empty() || w2.empty()) { Out("callid: usage -> callid <instanceId> <Script> [args...]"); return; }
        int instanceId;
        if (!AfkExpedition::ParseResearchIndex(w1,instanceId)) { Out("callid: invalid instance id; call not executed"); return; }
        try {
            RValue id((double)instanceId);
            if (!g_Yytk->CallBuiltin("instance_exists", { id }).ToBoolean()) { Out("callid: no instance " + w1); return; }
            CInstance* ci = ResolveInstance(id);
            if (!ci) { Out("callid: instance unresolved"); return; }
            std::vector<RValue> args;
            if (!ResearchArguments(ss,ci,args)) return;
            RValue res;
            AurieStatus st = g_Yytk->CallGameScriptEx(res, ("gml_Script_" + w2).c_str(), ci, ci, args);
            Out("callid " + w1 + " (" + ObjectNameOf(id) + ")." + w2 + "(" + std::to_string(args.size()) + " args): st=" + std::to_string((int)st) + " -> " + Stringify(res).substr(0, 200));
        } catch (...) { Out("callid: EXCEPTION"); }
        return;
    }
    if (w0 == "iset") {
        // iset <instanceId> <var> <number|'text|undef>: set an instance variable
        std::string w3; ss >> w3;
        if (w1.empty() || w2.empty() || w3.empty()) { Out("iset: usage -> iset <instanceId> <var> <value>"); return; }
        try {
            RValue id((double)std::strtod(w1.c_str(), nullptr));
            if (!g_Yytk->CallBuiltin("instance_exists", { id }).ToBoolean()) { Out("iset: no instance " + w1); return; }
            RValue val = w3 == "undef" ? RValue() : (w3.size() > 1 && w3[0] == (char)0x27) ? RValue(w3.substr(1)) : RValue(std::strtod(w3.c_str(), nullptr));
            g_Yytk->CallBuiltin("variable_instance_set", { id, RValue(w2), val });
            Out("iset " + w1 + "." + w2 + " = " + Stringify(g_Yytk->CallBuiltin("variable_instance_get", { id, RValue(w2) })).substr(0, 120));
        } catch (...) { Out("iset: EXCEPTION"); }
        return;
    }
    if (w0 == "objof") {
        if (w1.empty()) { Out("objof: usage -> objof <instanceId>"); return; }
        try { RValue id((double)std::strtod(w1.c_str(), nullptr)); Out("objof " + w1 + ": " + (g_Yytk->CallBuiltin("instance_exists", { id }).ToBoolean() ? ObjectNameOf(id) : std::string("(no such instance)"))); }
        catch (...) { Out("objof: EXCEPTION"); }
        return;
    }
    if (w0 == "callon") {
        // callon <Obj> <nth> <Script> [args...]: run a game script with self = a
        // live instance of Obj and other = the player. Args: numbers, `me`
        // (player id), `undef`, or 'text. Research only.
        std::string w3; ss >> w3;
        if (w1.empty() || w2.empty() || w3.empty()) { Out("callon: usage -> callon <Obj> <nth> <Script> [args...]"); return; }
        int ordinal;
        if (!AfkExpedition::ParseResearchIndex(w2,ordinal)) { Out("callon: invalid instance ordinal; call not executed"); return; }
        try {
            RValue oi; if (!ObjectIndex(w1, oi)) { Out("callon: object not found"); return; }
            RValue id = g_Yytk->CallBuiltin("instance_find", { oi, RValue((double)ordinal) });
            CInstance* ci = ResolveInstance(id);
            RValue pobj; if (!ObjectIndex("Player_obj", pobj)) { Out("callon: no Player_obj"); return; }
            RValue pid = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
            CInstance* pi = g_Yytk->CallBuiltin("instance_exists", { pid }).ToBoolean() ? ResolveInstance(pid) : ci;   // no player (menus): other = self
            if (!ci || !pi) { Out("callon: instance missing"); return; }
            std::vector<RValue> args;
            if (!ResearchArguments(ss,ci,args)) return;
            RValue res; g_ReplayActive = true;
            AurieStatus st = g_Yytk->CallGameScriptEx(res, ("gml_Script_" + w3).c_str(), ci, pi, args);
            g_ReplayActive = false;
            Out("callon " + w1 + "." + w3 + "(" + std::to_string(args.size()) + " args): st=" + std::to_string((int)st) + " -> " + Stringify(res).substr(0, 300));
        } catch (...) { g_ReplayActive = false; Out("callon: EXCEPTION"); }
        return;
    }
    if (w0 == "lastcalls") {
        std::lock_guard<std::mutex> lk(g_CallLogMutex);
        for (auto& s : g_CallLog) Out("  " + s);
        Out("lastcalls: " + std::to_string(g_CallLog.size()) + " remembered");
        return;
    }
    if (w0 == "gold") {
        try { RValue r = g_Yytk->CallGameScript("gml_Script_GetGoldAmount", {}); Out("gold: " + Stringify(r)); } catch (...) { Out("gold: EXCEPTION"); }
        return;
    }
    if (w0 == "call") {
        // call <Script> [num|str args...]: run a game script with self = other = the player (research)
        if (w1.empty()) { Out("call: usage -> call <Script> [args...]"); return; }
        try {
            RValue pobj; if (!ObjectIndex("Player_obj", pobj)) { Out("call: Player_obj not found"); return; }
            RValue pid = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
            CInstance* pi = ResolveInstance(pid);
            if (!pi) { Out("call: no player instance"); return; }
            std::vector<RValue> args;
            std::string tok = w2;
            while (!tok.empty()) {
                if (tok == "me") args.push_back(pid);
                else if (tok == "undef") args.push_back(RValue());
                else if (tok.size() > 1 && tok[0] == (char)0x27) args.push_back(RValue(tok.substr(1)));
                else args.push_back(RValue(std::strtod(tok.c_str(), nullptr)));
                tok.clear(); ss >> tok;
            }
            RValue res;
            AurieStatus st = g_Yytk->CallGameScriptEx(res, ("gml_Script_" + w1).c_str(), pi, pi, args);
            std::string line = "call " + w1 + "(" + std::to_string(args.size()) + " args";
            for (auto& a : args) line += " k" + std::to_string((int)a.m_Kind) + ":" + Stringify(a).substr(0, 20);
            line += "): st=" + std::to_string((int)st) + " -> " + Stringify(res).substr(0, 400);
            // Never resolve a result as a protected handle here: a plain number in
            // the handle range (MEASURED 2026-09-24: GetGoldAmount's 1.48 M gold)
            // made the anti-cheat module read an invalid handle and crash the game.
            Out(line);
        } catch (...) { Out("call: EXCEPTION"); }
        return;
    }
    if (w0 == "varid") {
        // varid <hex file address> [name...]: read the variable id the compiled
        // code keeps at that static slot (file address, relocated to the live
        // module) and name it by comparing with variable_get_hash of the
        // packets' variable names (research: which variable a routine reads).
        if (w1.empty()) { Out("varid: usage -> varid <0x14xxxxxxx> [candidate names...]"); return; }
        try {
            const uint64_t fileAddr = std::strtoull(w1.c_str(), nullptr, 16);
            const uint64_t base = (uint64_t)GetModuleHandleA(nullptr);
            const uint64_t live = base + (fileAddr - 0x140000000ULL);
            uint32_t id = 0;
            MEMORY_BASIC_INFORMATION mbi{};
            if (!VirtualQuery((LPCVOID)live, &mbi, sizeof(mbi)) || mbi.State != MEM_COMMIT) { Out("varid: address not readable"); return; }
            std::memcpy(&id, (const void*)live, 4);
            std::string line = "varid @" + w1 + " (live 0x" + [&]{ char b[32]; snprintf(b, sizeof b, "%llx", (unsigned long long)live); return std::string(b); }() + ") = " + std::to_string(id);
            std::vector<std::string> names; std::string tok; while (ss >> tok) names.push_back(tok);
            // plus every variable name from the newest deep packet
            std::error_code ec; fs::path newest; fs::file_time_type nt{};
            for (auto& e : fs::directory_iterator(PacketsDir(), ec)) if (e.is_regular_file() && (newest.empty() || e.last_write_time() > nt)) { newest = e.path(); nt = e.last_write_time(); }
            if (!newest.empty()) {
                RValue pk; try { pk = g_Yytk->CallBuiltin("json_parse", { RValue(ReadFileText(newest.string())) }); } catch (...) {}
                if (pk.m_Kind == VALUE_OBJECT) for (const char* sect : { "self_snapshot", "protected" }) {
                    RValue st = StructGet(pk, sect);
                    if (st.m_Kind != VALUE_OBJECT) continue;
                    RValue nm = g_Yytk->CallBuiltin("variable_struct_get_names", { st });
                    int n = nm.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { nm }).ToDouble() : 0;
                    for (int i = 0; i < n; ++i) { RValue e = g_Yytk->CallBuiltin("array_get", { nm, RValue((double)i) }); if (e.m_Kind == VALUE_STRING) names.push_back(e.ToString()); }
                }
            }
            std::string match;
            for (auto& nmn : names) {
                RValue h = g_Yytk->CallBuiltin("variable_get_hash", { RValue(nmn) });
                if (IsNumberKind(h) && (uint32_t)h.ToDouble() == id) { match = nmn; break; }
            }
            Out(line + (match.empty() ? "  (no name matched among " + std::to_string(names.size()) + " candidates)" : "  = " + match));
        } catch (...) { Out("varid: EXCEPTION"); }
        return;
    }
    if (w0 == "gvar") {
        // gvar <name> [idx]: read a global variable, or element idx of a global
        // array; a plausible handle is resolved through the anti-cheat wrapper.
        if (w1.empty()) { Out("gvar: usage -> gvar <name> [idx]"); return; }
        try {
            RValue ex = g_Yytk->CallBuiltin("variable_global_exists", { RValue(w1) });
            if (!ex.ToBoolean()) { Out("gvar " + w1 + ": (not set)"); return; }
            RValue v = g_Yytk->CallBuiltin("variable_global_get", { RValue(w1) });
            std::string label = "gvar " + w1;
            if (!w2.empty()) {
                if (!g_Yytk->CallBuiltin("is_array", { v }).ToBoolean()) { Out(label + ": not an array (" + KindName(v.m_Kind) + ")"); return; }
                const int len = (int)g_Yytk->CallBuiltin("array_length", { v }).ToDouble();
                const int idx = std::atoi(w2.c_str());
                if (idx < 0 || idx >= len) { Out(label + ": index out of range, length=" + std::to_string(len)); return; }
                v = g_Yytk->CallBuiltin("array_get", { v, RValue((double)idx) });
                label += "[" + w2 + "]";
            } else if (g_Yytk->CallBuiltin("is_array", { v }).ToBoolean()) {
                label += " (array length " + std::to_string((int)g_Yytk->CallBuiltin("array_length", { v }).ToDouble()) + ")";
            }
            std::string line = label + " = " + Stringify(v).substr(0, 300);
            // No automatic handle resolution: an ordinary number in the handle range crashes the game (see `call`).
            Out(line);
        } catch (...) { Out("gvar: EXCEPTION"); }
        return;
    }
    if (w0 == "varname") {
        // varname <id>: name a runtime variable id by hashing every candidate
        // in afk\varnames.txt (one identifier per line). Research.
        if (w1.empty()) { Out("varname: usage -> varname <id>"); return; }
        try {
            const uint32_t want = (uint32_t)std::strtoul(w1.c_str(), nullptr, 10);
            std::ifstream f(DATA_ROOT + "\\varnames.txt");
            std::string line; size_t n = 0; std::string found;
            while (std::getline(f, line)) {
                while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
                if (line.empty()) continue;
                ++n;
                RValue h = g_Yytk->CallBuiltin("variable_get_hash", { RValue(line) });
                if (IsNumberKind(h) && (uint32_t)h.ToDouble() == want) { found = line; break; }
            }
            Out("varname " + w1 + ": " + (found.empty() ? "no match among " + std::to_string(n) + " names" : found));
        } catch (...) { Out("varname: EXCEPTION"); }
        return;
    }
    if (w0 == "gset") {
        // gset <name> <index|-> <number|'text>: set a global variable, or one
        // element of a global array. Research / automation.
        std::string w3; ss >> w3;
        if (w1.empty() || w2.empty() || w3.empty()) { Out("gset: usage -> gset <name> <index|-> <value>"); return; }
        try {
            RValue val = (w3.size() > 1 && w3[0] == (char)0x27) ? RValue(w3.substr(1)) : RValue(std::strtod(w3.c_str(), nullptr));
            if (w2 == "-") {
                g_Yytk->CallBuiltin("variable_global_set", { RValue(w1), val });
            } else {
                RValue arr = g_Yytk->CallBuiltin("variable_global_get", { RValue(w1) });
                if (!g_Yytk->CallBuiltin("is_array", { arr }).ToBoolean()) { Out("gset: " + w1 + " is not an array"); return; }
                g_Yytk->CallBuiltin("array_set", { arr, RValue((double)std::atoi(w2.c_str())), val });
            }
            RValue now = g_Yytk->CallBuiltin("variable_global_get", { RValue(w1) });
            Out("gset " + w1 + (w2 == "-" ? "" : "[" + w2 + "]") + " = " + Stringify(now).substr(0, 120));
        } catch (...) { Out("gset: EXCEPTION"); }
        return;
    }
    if (w0 == "gscan") {
        // gscan <name> <lo> <hi> [anchorIdx]: print elements lo..hi of a global
        // array raw; with an anchor, resolve elements within +-4096 of the
        // anchor's handle (never resolve far-away numbers: that crashes).
        std::string w3, w4; ss >> w3 >> w4;
        if (w1.empty() || w2.empty() || w3.empty()) { Out("gscan: usage -> gscan <name> <lo> <hi> [anchorIdx]"); return; }
        try {
            RValue v = g_Yytk->CallBuiltin("variable_global_get", { RValue(w1) });
            if (!g_Yytk->CallBuiltin("is_array", { v }).ToBoolean()) { Out("gscan: not an array"); return; }
            const int len = (int)g_Yytk->CallBuiltin("array_length", { v }).ToDouble();
            int lo = std::atoi(w2.c_str()), hi = std::atoi(w3.c_str());
            if (lo < 0) lo = 0; if (hi >= len) hi = len - 1;
            double anchor = -1;
            if (!w4.empty()) { RValue a = g_Yytk->CallBuiltin("array_get", { v, RValue((double)std::atoi(w4.c_str())) }); if (IsNumberKind(a)) anchor = a.ToDouble(); }
            int shown = 0;
            for (int i = lo; i <= hi; ++i) {
                RValue e = g_Yytk->CallBuiltin("array_get", { v, RValue((double)i) });
                std::string line = "  [" + std::to_string(i) + "] " + Stringify(e).substr(0, 80);
                if (anchor > 0 && IsNumberKind(e) && std::fabs(e.ToDouble() - anchor) <= 4096) line += "  -> " + ResolveProtected(e);
                Out(line); if (++shown >= 600) { Out("  ..."); break; }
            }
            Out("gscan " + w1 + ": length " + std::to_string(len));
        } catch (...) { Out("gscan: EXCEPTION"); }
        return;
    }
    if (w0 == "gvars") {
        // MEASURED runner API: the global pseudo-instance accepts this builtin.
        // variable_global_get_names is unavailable here and returned a false zero.
        try {
            RValue names = g_Yytk->CallBuiltin("variable_instance_get_names", { RValue(-5.0) });
            if (names.m_Kind != VALUE_ARRAY) { Out("gvars: global enumeration unavailable"); return; }
            int n = (int)g_Yytk->CallBuiltin("array_length", { names }).ToDouble();
            int shown = 0;
            for (int i = 0; i < n; ++i) {
                RValue nm = g_Yytk->CallBuiltin("array_get", { names, RValue((double)i) });
                std::string name = nm.ToString();
                if (!w1.empty()) { std::string lo = name, f = w1; for (auto& c : lo) c = (char)tolower(c); for (auto& c : f) c = (char)tolower(c); if (lo.find(f) == std::string::npos) continue; }
                RValue v = g_Yytk->CallBuiltin("variable_global_get", { nm });
                std::string line = "  " + name + " = " + Stringify(v).substr(0, 120);
                Out(line); if (++shown >= 400) { Out("  ..."); break; }
            }
            Out("gvars: " + std::to_string(n) + " globals, " + std::to_string(shown) + " shown");
        } catch (...) { Out("gvars: global enumeration failed"); }
        return;
    }
    if (w0 == "who") { Out("who: " + CharacterStampJson()); return; }
    if (w0 == "slotkeys") {
        // research: the selected slot map's keys and value kinds (no values, no deep printing)
        try {
            int slot = -1; std::string pname;
            RValue m = SelectedSlotMap(&slot, &pname);
            if (!(IsNumberKind(m) || m.m_Kind == VALUE_REF)) { Out("slotkeys: no slot map for the player in play"); return; }
            Out("slotkeys: slot " + std::to_string(slot) + " (" + pname + ")");
            RValue k = g_Yytk->CallBuiltin("ds_map_find_first", { m });
            int n = 0; std::string line;
            while (k.m_Kind == VALUE_STRING && n < 400) {
                RValue v = g_Yytk->CallBuiltin("ds_map_find_value", { m, k });
                std::string vs = v.m_Kind == VALUE_STRING ? "\"" + v.ToString().substr(0, 24) + "\"" : (IsNumberKind(v) ? std::to_string((long long)v.ToDouble()) : KindName(v.m_Kind));
                line += k.ToString() + "=" + vs + "  ";
                if (++n % 6 == 0) { Out("  " + line); line.clear(); }
                k = g_Yytk->CallBuiltin("ds_map_find_next", { m, k });
            }
            if (!line.empty()) Out("  " + line);
            Out("slotkeys: " + std::to_string(n) + " keys");
        } catch (...) { Out("slotkeys: EXCEPTION"); }
        return;
    }
    if (w0 == "level") {
        // Character level / experience / hero level: protected handles in the
        // slot map of the character in play (values as loaded from the save;
        // the bar value on Controller_obj follows the experience on screen).
        try {
            int slot = -1; std::string name;
            RValue m = SelectedSlotMap(&slot, &name);
            std::string line = "level: ";
            if (IsNumberKind(m) || m.m_Kind == VALUE_REF)
                line += SlotMapNumber(m, "level") + "  experience=" + SlotMapNumber(m, "experience") + "  herolevel=" + SlotMapNumber(m, "herolevel")
                      + "  incarnation_exp=" + SlotMapNumber(m, "incarnation_exp") + "  (slot " + std::to_string(slot) + ", " + name + ")";
            else line += "? (no player in a map, or no slot named like the player)";
            RValue cobj; if (ObjectIndex("Controller_obj", cobj)) {
                RValue cid = g_Yytk->CallBuiltin("instance_find", { cobj, RValue(0.0) });
                line += "  exp_bar=" + Stringify(GetVar(cid, "drawExperienceLerp"));
            }
            Out(line);
        } catch (...) { Out("level: EXCEPTION"); }
        return;
    }
    if (w0 == "exp") {
        // Player experience: a protected handle on Player_obj, read through the wrapper.
        try {
            RValue pobj; if (!ObjectIndex("Player_obj", pobj)) { Out("exp: Player_obj not found"); return; }
            RValue pid = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
            RValue h = GetVar(pid, "experience");
            RValue lv = GetVar(pid, "level");
            Out("exp: handle=" + Stringify(h) + " value=" + ResolveProtected(h) + " level=" + Stringify(lv) + " (" + ResolveProtected(lv) + ")");
        } catch (...) { Out("exp: EXCEPTION"); }
        return;
    }
    if (w0 == "giveexp" || w0 == "givegold") {
        // Research: apply experience / gold through the game's own routines with
        // the player as self. ExperienceUpdate(amount, arg1) and
        // GoldLogAdd(id, amount) per the Tracker producer's measured arities.
        if (w1.empty()) { Out(w0 + ": usage -> " + w0 + " <amount> [second-arg]"); return; }
        try {
            RValue pobj; if (!ObjectIndex("Player_obj", pobj)) { Out(w0 + ": Player_obj not found"); return; }
            RValue pid = g_Yytk->CallBuiltin("instance_find", { pobj, RValue(0.0) });
            CInstance* pi = ResolveInstance(pid);
            if (!pi) { Out(w0 + ": no player instance"); return; }
            const double amount = std::strtod(w1.c_str(), nullptr);
            // second argument: omitted -> undefined (what the game's own coin
            // pickup passes as GoldLogAdd's first argument, MEASURED 2026-09-17)
            RValue second = w2.empty() ? RValue() : RValue(std::strtod(w2.c_str(), nullptr));
            RValue res;
            AurieStatus st;
            if (w0 == "giveexp") st = g_Yytk->CallGameScriptEx(res, "gml_Script_ExperienceUpdate", pi, pi, { RValue(amount), second });
            else                 st = g_Yytk->CallGameScriptEx(res, "gml_Script_GoldLogAdd", pi, pi, { second, RValue(amount) });
            Out(w0 + " " + w1 + ": st=" + std::to_string((int)st) + " result=" + Stringify(res));
        } catch (...) { Out(w0 + ": EXCEPTION"); }
        return;
    }
    if (w0 == "vars") {
        // vars <ObjName> [nth] [filter]: list an instance's variables (research)
        if (w1.empty()) { Out("vars: usage -> vars <ObjName> [nth] [filter]"); return; }
        try {
            RValue oi;
            if (!ObjectIndex(w1, oi)) { Out("vars: object not found: " + w1); return; }
            int nth = w2.empty() ? 0 : std::atoi(w2.c_str());
            std::string filt; ss >> filt;
            RValue id = g_Yytk->CallBuiltin("instance_find", { oi, RValue((double)nth) });
            if (!g_Yytk->CallBuiltin("instance_exists", { id }).ToBoolean()) { Out("vars: no live instance"); return; }
            auto vars = SnapshotSelf(id);
            int shown = 0;
            for (auto& kv : vars) {
                if (!filt.empty() && kv.first.find(filt) == std::string::npos) continue;
                Out("  " + kv.first + " = " + kv.second.substr(0, 100)); if (++shown >= 400) break;
            }
            Out("vars " + w1 + ": " + std::to_string(vars.size()) + " variables");
        } catch (...) { Out("vars: EXCEPTION"); }
        return;
    }
    if (w0 == "help") {
        Out("commands: status | hook | capture on [full] | capture off | capture stats | capture reset | packets | replay <prefix|latest> [times] [clean]");
        return;
    }
    Out("unknown command: " + raw);
}

static void PollCommands()
{
    std::ifstream in(CmdPath(), std::ios::binary);
    if (!in.good()) return;
    std::stringstream ss; ss << in.rdbuf();
    std::string content = ss.str();
    in.close();
    if (content.empty()) return;
    DeleteFileA(CmdPath().c_str());
    Out("---- running command file ----");
    std::stringstream ls(content);
    std::string line;
    while (std::getline(ls, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
        if (line.empty()) continue;
        try { RunCommand(line); } catch (...) { Out("command threw: " + line); }
    }
    Out("---- done ----");
}

// --------------------------------------------------------------- frame loop
static void FrameCallback(FWFrame& FrameContext)
{
    UNREFERENCED_PARAMETER(FrameContext);
    static uint32_t fc = 0;
    ++fc;
    DefenseLab::Frame();
    InGameUI::Frame();
    HandleCloseRequest();
    if (fc % 6 == 0) PollCommands();
    static auto nextFarmSample=std::chrono::steady_clock::now();
    static auto previousFarmSample=nextFarmSample;
    if(g_CaptureOn && !g_Exp.running && std::chrono::steady_clock::now()>=nextFarmSample) {
        const auto stamp=std::chrono::steady_clock::now();
        const double elapsed=std::chrono::duration<double>(stamp-previousFarmSample).count();
        previousFarmSample=stamp;nextFarmSample=stamp+std::chrono::seconds(1);
        const auto context=FarmContextJson();
        const auto decision=AfkExpedition::CheckFarmSample(g_FarmContextLoading,context!="null" && context==g_SessionFarmContext,g_FarmClockReady,elapsed);
        if(decision==AfkExpedition::FarmSample::Invalid) {
            SessionAppend("{\"kind\":\"context_invalid\",\"reason\":\"loadout_changed_or_clock_gap\",\"context_error\":\""+JsonEscape(g_FarmContextError)+"\",\"context_available\":"+(context=="null"?"false":"true")+",\"elapsed\":"+std::to_string(elapsed)+",\"t\":\""+NowIso()+"\"}");
            g_CaptureOn=false;g_FarmClockReady=false;
        } else if(decision==AfkExpedition::FarmSample::Count) {
            SessionAppend("{\"kind\":\"farm_clock\",\"t\":\""+NowIso()+"\",\"room\":\""+JsonEscape(CurrentRoomName())+"\",\"seconds\":"+std::to_string(elapsed)+"}");
        } else if(decision==AfkExpedition::FarmSample::Pause) {
            if(g_FarmClockReady)SessionAppend("{\"kind\":\"farm_pause\",\"reason\":\"room_loading\",\"t\":\""+NowIso()+"\"}");
            g_FarmClockReady=false;
        } else {
            g_FarmClockReady=true;
            SessionAppend("{\"kind\":\"farm_resume\",\"t\":\""+NowIso()+"\"}");
        }
    }
    if(!g_CaptureOn || g_Exp.running) {previousFarmSample=std::chrono::steady_clock::now();nextFarmSample=previousFarmSample+std::chrono::seconds(1);}
    if (g_CaptureOn && !g_Exp.running) {
        // Do not label a second character's kills with the first one's stamp.
        const std::string character = CharacterStampJson();
        static auto nextSettingsCheck = std::chrono::steady_clock::now();
        std::string settings = g_SessionForgePact;
        if (std::chrono::steady_clock::now() >= nextSettingsCheck) {
            settings = ForgePactSettingsJson();
            nextSettingsCheck = std::chrono::steady_clock::now() + std::chrono::seconds(1);
        }
        if ((!CurrentIdentityKey().empty() && !g_FarmContextLoading && character != g_SessionCharacter) || settings != g_SessionForgePact) {
            // The exact settings-change instant is unknown between polls, so
            // exclude the old session from calibration rather than mix rates.
            SessionAppend("{\"kind\":\"context_invalid\",\"reason\":\"character_or_settings_changed\",\"t\":\"" + NowIso() + "\"}");
            g_CaptureOn=false;
        }
    }
    if (g_Exp.running) ExpeditionTick();
    // Auto-install the hook once the runner has its script table up; harmless
    // while capture stays off, and it lets `status` report interception kind
    // without a manual step. Retried every 5 s until it succeeds.
    static bool tried = false; static uint32_t nextTry = 600;
    if (!g_OrigDropItem && fc >= nextTry) {
        nextTry = fc + 300;
        if (!tried) Out("neighbours at startup: " + LoadedNeighbours());
        tried = true;
        InstallDropItemHook();
    }
    // Passive calibration: with "auto_capture": true in afk\config.json (the
    // command line's `capture auto on`), capture arms itself whenever the
    // hook is in and a player instance exists, so every play session grows
    // the live baseline that fidelity is checked against.
    static uint32_t nextAuto = 900;
    if (g_OrigDropItem && !g_CaptureOn.load() && fc >= nextAuto) {
        nextAuto = fc + 300;
        try {
            const std::string cfg = ReadFileText(DATA_ROOT + "\\config.json");
            if (cfg.find("\"auto_capture\"") != std::string::npos) {
                RValue c = g_Yytk->CallBuiltin("json_parse", { RValue(cfg) });
                RValue ac = c.m_Kind == VALUE_OBJECT ? StructGet(c, "auto_capture") : RValue();
                if ((ac.m_Kind == VALUE_BOOL || IsNumberKind(ac)) && ac.ToBoolean()) {
                    RValue pobj;
                    if (ObjectIndex("Player_obj", pobj) && g_Yytk->CallBuiltin("instance_number", { pobj }).ToDouble() >= 1) {
                        RunCommand("afk capture on");
                        Out("capture: armed automatically (auto_capture in config.json)");
                    }
                }
            }
        } catch (...) {}
    }
}

// ------------------------------------------------------------------- entry
EXPORTED AurieStatus ModuleInitialize(IN AurieModule* Module, IN const fs::path& ModulePath)
{
    UNREFERENCED_PARAMETER(ModulePath);
    g_Module = Module;
    g_Yytk = YYTK::GetInterface();
    if (!g_Yytk) {
        Aurie::DbgPrint("[AFK] ERROR: YYToolkit interface not available\n");
        return AURIE_MODULE_DEPENDENCY_NOT_RESOLVED;
    }
    EnsureDirs();
    Out("HS AFK Expedition " AFK_EXPEDITION_VERSION " loaded; build " + GAME_BUILD_ID);
    try { std::ofstream b(DATA_ROOT + "\\build.json", std::ios::binary | std::ios::trunc); b << "{\"game_build\":\"" << GAME_BUILD_ID << "\",\"plugin\":\"" AFK_EXPEDITION_VERSION "\",\"written\":\"" << NowIso() << "\"}\n"; } catch (...) {}
    AurieStatus st = g_Yytk->CreateCallback(Module, EVENT_FRAME, (PVOID)FrameCallback, 0);
    if (!AurieSuccess(st)) {
        Out("FAILED to register frame callback st=" + std::to_string((int)st));
        return st;
    }
    g_Yytk->Print(CM_LIGHTGREEN, "[AFK] ready - watching afk_ipc\\cmd.txt");
    return AURIE_SUCCESS;
}
