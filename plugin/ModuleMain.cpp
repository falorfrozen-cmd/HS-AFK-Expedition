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

// asset_get_index returns a typed asset reference on this runner (VALUE_REF),
// not a plain number (MEASURED 2026-09-17: a number check rejected every
// object). Validate through object_exists instead of looking at the kind.
static bool ObjectIndex(const std::string& name, RValue& out)
{
    try {
        out = g_Yytk->CallBuiltin("asset_get_index", { RValue(name) });
        if (out.m_Kind == VALUE_UNDEFINED) return false;
        if (IsNumberKind(out) && out.ToDouble() < 0) return false;
        return g_Yytk->CallBuiltin("object_exists", { out }).ToBoolean();
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

static RValue& Hook_DropItem(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    ++g_DropItemCalls;
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
static void FlushPendingSpoolItem(const std::string& extraFields)
{
    if (g_PendingSpoolItem.empty()) return;
    std::ofstream f(g_SpoolPath, std::ios::app);
    f << g_PendingSpoolItem << extraFields << "}\n";
    g_PendingSpoolItem.clear();
}
static RValue& Hook_CreateItemNew(CInstance* S, CInstance* O, RValue& R, int argc, RValue** A)
{
    RValue* r = &R;
    if (g_OrigCreateItemNew) r = &g_OrigCreateItemNew(S, O, R, argc, A);
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
                RValue def = g_Yytk->CallBuiltin("variable_struct_get", { it, RValue("itemDefinitionStruct") });
                std::string defjs = def.m_Kind == VALUE_OBJECT ? Stringify(def) : "null";
                ++g_ItemsLogged;
                SessionAppend("{\"kind\":\"item\",\"t\":\"" + NowIso() + "\",\"ctx\":\"" + g_CtxKind + "\",\"packet\":\"" + g_CtxPacket
                    + "\",\"type\":" + (IsNumberKind(type) ? Stringify(type) : std::string("null")) + ",\"tname\":\"" + JsonEscape(tname)
                    + "\",\"name\":\"" + JsonEscape(name) + "\",\"rarity\":" + rarity + ",\"def\":" + defjs + "}");
                // Spool: the complete native item struct, exactly as the game
                // built it (the shape a stash entry stores), one record per item.
                if (g_SpoolActive && g_CtxKind == "replay") {
                    // Written by the LootGroundCreate hook once the floor object
                    // exists: its Create evaluates the player's own loot filter
                    // (lootFilterVisible / lootFilterHighlight, MEASURED
                    // 2026-09-18 on a live floor item), and that verdict goes
                    // into the record so the Vault ingest can drop the junk.
                    std::ostringstream o;
                    o << "{\"expedition_id\":\"" << JsonEscape(g_SpoolId) << "\",\"seq\":" << (++g_SpoolSeq)
                      << ",\"kind\":\"item\",\"t\":\"" << NowIso() << "\",\"packet\":\"" << g_CtxPacket
                      << "\",\"type\":" << (IsNumberKind(type) ? Stringify(type) : std::string("null"))
                      << ",\"name\":\"" << JsonEscape(name) << "\",\"item\":" << Stringify(it);
                    FlushPendingSpoolItem("");           // an earlier item still pending: no floor object came back for it
                    g_PendingSpoolItem = o.str();
                    ++g_SpoolItems;
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
                try {
                    RValue vis = GetVar(*r, "lootFilterVisible"), hi = GetVar(*r, "lootFilterHighlight"), skip = GetVar(*r, "skipLootFilter");
                    const bool visible = (vis.m_Kind == VALUE_BOOL || IsNumberKind(vis)) ? vis.ToBoolean() : true;
                    const bool skipped = (skip.m_Kind == VALUE_BOOL || IsNumberKind(skip)) && skip.ToBoolean();
                    extra = std::string(",\"placed\":true,\"filter_visible\":") + ((visible || skipped) ? "true" : "false")
                          + ",\"filter_highlight\":" + (((hi.m_Kind == VALUE_BOOL || IsNumberKind(hi)) && hi.ToBoolean()) ? "true" : "false");
                    if (!(visible || skipped)) ++g_SpoolFiltered;
                } catch (...) { extra = ",\"placed\":true"; }
                FlushPendingSpoolItem(extra);
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
static bool g_ExpBusy = false;            // an expedition is replaying across frames

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
                try { RValue idx = g_Yytk->CallBuiltin("asset_get_index", { RValue(name) }); if (IsNumberKind(idx)) return idx; } catch (...) {}
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
struct LoadedPacket {
    std::string path, hashPrefix, monsterKey, selfObject;
    RValue pk, args, snap, prot, monsterObj;
    int argc = 0; double packetExp = -1.0; bool haveMonsterObj = false;
    double overrideExp = -1.0;     // from the plan: mean kill experience of the kills behind this packet
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

// One replayed kill: ghost up, the game's drop routine, experience, coins, ghost down.
static CallOutcome ReplayOneCall(const LoadedPacket& lp, const ReplayEnv& env)
{
    CallOutcome oc;
    g_ReplayStage = "create ghost";
    RValue ghost;
    try { ghost = g_Yytk->CallBuiltin("instance_create_depth", { RValue(env.px + 96.0), RValue(env.py), RValue(0.0), env.ghostObj }); }
    catch (...) { oc.note = "instance_create_depth threw"; return oc; }
    g_ReplayStage = "resolve ghost";
    CInstance* gi = ResolveInstance(ghost);
    if (!gi) { oc.note = "ghost instance could not be resolved"; return oc; }

    // 1) restore the monster's variables
    g_ReplayStage = "restore variables";
    std::vector<RValue> protectedHandles;
    try {
        RValue names = g_Yytk->CallBuiltin("variable_struct_get_names", { lp.snap });
        int n = names.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { names }).ToDouble() : 0;
        for (int i = 0; i < n; ++i) {
            RValue nm = g_Yytk->CallBuiltin("array_get", { names, RValue((double)i) });
            if (nm.m_Kind != VALUE_STRING) continue;
            const std::string name = nm.ToString();
            if (IsGhostOwnVar(name)) continue;
            RValue val = g_Yytk->CallBuiltin("variable_struct_get", { lp.snap, nm });
            g_Yytk->CallBuiltin("variable_instance_set", { ghost, nm, FromJsonValue(val) });
            ++oc.restored;
        }
        // 2) protected values. The drop routine reads these through the
        //    anti-cheat wrapper, so a plain number is read as handle 0
        //    (MEASURED 2026-09-17: 30 replays with plain values produced
        //    nothing). Allocate a real handle per value with the game's
        //    own "new variable" wrapper and free it after the call.
        if (lp.prot.m_Kind == VALUE_OBJECT) {
            RValue pn = g_Yytk->CallBuiltin("variable_struct_get_names", { lp.prot });
            int m = pn.m_Kind == VALUE_ARRAY ? (int)g_Yytk->CallBuiltin("array_length", { pn }).ToDouble() : 0;
            for (int i = 0; i < m; ++i) {
                RValue nm = g_Yytk->CallBuiltin("array_get", { pn, RValue((double)i) });
                RValue val = g_Yytk->CallBuiltin("variable_struct_get", { lp.prot, nm });
                if (!IsNumberKind(val)) continue;
                RValue h;
                try { h = g_Yytk->CallGameScript("gml_Script_PC_InitNewVariableFastGMLWrapper", { val }); } catch (...) { h = RValue(); }
                if (IsNumberKind(h) && h.ToDouble() > 0) {
                    protectedHandles.push_back(h);
                    g_Yytk->CallBuiltin("variable_instance_set", { ghost, nm, h });
                } else {
                    g_Yytk->CallBuiltin("variable_instance_set", { ghost, nm, val });
                }
            }
        }
        g_Yytk->CallBuiltin("variable_instance_set", { ghost, RValue("xPos"), RValue(env.px + 96.0) });
        g_Yytk->CallBuiltin("variable_instance_set", { ghost, RValue("yPos"), RValue(env.py) });
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
        RValue a = g_Yytk->CallBuiltin("array_get", { lp.args, RValue((double)i) });
        if (i == 2) a = RValue(env.px + 96.0);
        else if (i == 3) a = RValue(env.py);
        else a = FromJsonValue(a);
        av.push_back(a);
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
        g_ReplayStage = "harvest coins"; g_PlayerX = env.px; g_PlayerY = env.py; HarvestCoins(coinBefore);
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
    }

    g_ReplayStage = "cleanup";
    if (oc.changed) { RValue r; try { g_Yytk->CallBuiltinEx(r, "instance_change", gi, gi, { env.ghostObj, RValue(false) }); } catch (...) {} }
    try { g_Yytk->CallBuiltin("instance_destroy", { ghost }); } catch (...) {}
    for (auto& h : protectedHandles) { try { g_Yytk->CallGameScript("gml_Script_PC_FreeVariableGMLWrapper", { h }); } catch (...) {} }
    FreeReplayDs();
    return oc;
}

static void SpoolBegin(const std::string& spoolId)
{
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
static void SpoolEnd(long long calls, const char* kind = "summary")
{
    if (!g_SpoolActive) return;
    FlushPendingSpoolItem("");
    std::ofstream f(g_SpoolPath, std::ios::app);
    f << "{\"expedition_id\":\"" << JsonEscape(g_SpoolId) << "\",\"seq\":" << (++g_SpoolSeq) << ",\"kind\":\"" << kind << "\",\"t\":\"" << NowIso()
      << "\",\"gold\":" << (long long)g_SpoolGold << ",\"gold_piles\":" << g_SpoolGoldPiles << ",\"exp_credited\":" << (long long)g_GiveExpSum
      << ",\"calls\":" << calls << ",\"items\":" << g_SpoolItems << ",\"items_filtered\":" << g_SpoolFiltered << ",\"items_unplaced\":" << g_SpoolUnplaced
      << ",\"forgepact\":" << ForgePactSettingsJson() << "}\n";
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
// save and spool agree: refuse automatic recovery of a running checkpoint.
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
};
static Expedition g_Exp;
static uint64_t g_CheckpointWriteRetries = 0;
static std::string PersistRewards();

static std::string ExpeditionProgressJson(const std::string& state)
{
    std::ostringstream o;
    o << std::setprecision(17);
    o << "{\"expedition_id\":\"" << JsonEscape(g_Exp.id) << "\",\"state\":\"" << JsonEscape(state) << "\""
      << ",\"packet_index\":" << g_Exp.idx << ",\"done_in_packet\":" << g_Exp.doneInPacket
      << ",\"calls_done\":" << g_Exp.callsDone << ",\"calls_total\":" << g_Exp.callsTotal << ",\"failed\":" << g_Exp.failed << ",\"skipped\":" << g_Exp.skipped
      << ",\"checkpoint_version\":2,\"plan_hash\":\"" << g_Exp.planHash << "\""
      << ",\"checkpoint_write_retries\":" << g_CheckpointWriteRetries
      << ",\"effective_magic_find\":" << (g_Exp.effectiveMagicFind>=0?std::to_string(g_Exp.effectiveMagicFind):"null")
      << ",\"save_committed\":" << (g_Exp.saveNote.rfind("saved (",0)==0 ? "true" : "false");
    CurrentRewards().Fields([&](const char* key, auto& value) { o << ",\"" << key << "\":" << value; });
    o
      << ",\"started\":\"" << JsonEscape(g_Exp.startedAt) << "\",\"updated\":\"" << NowIso() << "\""
      << ",\"plan\":\"" << JsonEscape(g_Exp.planPath) << "\",\"spool\":\"" << JsonEscape(g_SpoolPath) << "\""
      << ",\"error\":\"" << JsonEscape(g_Exp.error) << "\",\"pause\":\"" << JsonEscape(g_Exp.pauseReason) << "\",\"saved\":\"" << JsonEscape(g_Exp.saveNote) << "\"}";
    return o.str();
}
static void ExpeditionWriteProgress(const std::string& state)
{
    g_Exp.state = state;
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
                if (!AfkExpedition::CanResume(state, samePlan, safeSave)) { Out("expedition: checkpoint is unclean, failed or legacy; automatic resume refused, reconciliation required"); return; }
                if (!restored.Load([&](const char* key) { RValue v = StructGet(pr, key); return IsNumberKind(v) && v.m_Kind != VALUE_BOOL ? v.ToDouble() : std::numeric_limits<double>::quiet_NaN(); })) {
                    Out("expedition: checkpoint reward counters missing/invalid"); return;
                }
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
    g_Exp = std::move(e);
    g_GiveExp = g_Exp.exp; g_GoldPickup = g_Exp.gold;
    SpoolBegin(g_Exp.id);
    if (resumed) {
        RestoreRewards(restored);
    }
    if (g_SessionFile.empty()) SessionOpen();
    g_Exp.running = true; g_ExpBusy = true;
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
    if (CurrentIdentityKey() != g_Exp.identity || (!g_Exp.anywhere && CurrentRoomName() != g_Exp.room)) {
        const std::string reason = "return to the expedition character and room";
        if (g_Exp.pauseReason != reason) { g_Exp.pauseReason = reason; ExpeditionWriteProgress("paused"); }
        return;
    }
    if (!g_Exp.pauseReason.empty()) { Out("expedition " + g_Exp.id + ": resumed"); g_Exp.pauseReason.clear(); }
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
    if (g_Exp.idx >= g_Exp.packets.size() || (g_Exp.idx + 1 == g_Exp.packets.size() && g_Exp.doneInPacket >= g_Exp.packets.back().count)) { ExpeditionFinish("done"); return; }
    ExpeditionWriteProgress("running");
}
static void CmdExpeditionAbort()
{
    if (!g_Exp.running) { Out("expedition: nothing running"); return; }
    if (CurrentIdentityKey() != g_Exp.identity) { Out("expedition: load the expedition character before saving an abort"); return; }
    ExpeditionFinish("aborted");
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
            if (IsNumberKind(res) && res.ToDouble() > 100000 && res.ToDouble() < 10000000) line += "  [as handle -> " + ResolveProtected(res) + "]";
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
            if (IsNumberKind(v) && v.ToDouble() > 100000 && v.ToDouble() < 10000000) line += "  [as handle -> " + ResolveProtected(v) + "]";
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
                if (IsNumberKind(v) && v.ToDouble() > 100000 && v.ToDouble() < 10000000) line += "  [handle? " + ResolveProtected(v) + "]";
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
