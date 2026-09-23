#define UNICODE
#define _UNICODE
#include <windows.h>
#include <filesystem>
#include <string>
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR args,int) {
    wchar_t buffer[32768]{};
    if(!GetModuleFileNameW(nullptr,buffer,32768))return 1;
    const auto root=std::filesystem::path(buffer).parent_path();
    const auto python=root/L"runtime"/L"python.exe";
    const auto panel=root/L"app"/L"tools"/L"panel.py";
    if(!std::filesystem::is_regular_file(python)||!std::filesystem::is_regular_file(panel)) {
        MessageBoxW(nullptr,L"Extract the entire package to a folder. Keep AFK FARM.exe alongside the other package files.",L"AFK FARM",MB_ICONERROR);return 2;
    }
    std::wstring command=L"\""+python.wstring()+L"\" -B \""+panel.wstring()+L"\"";
    if(args && std::wstring(args)==L"--no-browser")command+=L" --no-browser";
    STARTUPINFOW startup{};startup.cb=sizeof(startup);startup.dwFlags=STARTF_USESHOWWINDOW;startup.wShowWindow=SW_HIDE;
    PROCESS_INFORMATION process{};
    if(!CreateProcessW(python.c_str(),command.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,root.c_str(),&startup,&process)) {
        MessageBoxW(nullptr,L"The local panel could not start. Check the package files.",L"AFK FARM",MB_ICONERROR);return 3;
    }
    CloseHandle(process.hThread);CloseHandle(process.hProcess);return 0;
}
