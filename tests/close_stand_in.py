"""Stand-in for the game in test_game_session: one window, a chosen exit code.

pythonw close_stand_in.py <info.json> <exit code>

Writes {"pid", "image"} to info.json once its window exists, then exits with the
given code when the window receives WM_CLOSE (what Process.CloseMainWindow posts).
The window is an off-screen tool window (no taskbar button) that is never
activated. Without a close it exits by itself after 60 s with TIMED_OUT, so a
failing test leaves nothing running and nothing has to be killed. ExitProcess
is a normal exit: Windows Error Reporting writes no report or dump for it.
"""
import ctypes
import json
import os
import sys
import traceback
from ctypes import wintypes

TIMED_OUT=0x7E57
WM_CLOSE,WM_TIMER=0x0010,0x0113
WS_POPUP=0x80000000
WS_EX_TOOLWINDOW,WS_EX_NOACTIVATE=0x00000080,0x08000000
SW_SHOWNOACTIVATE=4

user32=ctypes.WinDLL('user32',use_last_error=True)
kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
LRESULT=ctypes.c_ssize_t
WNDPROC=ctypes.WINFUNCTYPE(LRESULT,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_=[('style',wintypes.UINT),('lpfnWndProc',WNDPROC),('cbClsExtra',ctypes.c_int),
              ('cbWndExtra',ctypes.c_int),('hInstance',wintypes.HINSTANCE),('hIcon',wintypes.HICON),
              ('hCursor',wintypes.HANDLE),('hbrBackground',wintypes.HBRUSH),
              ('lpszMenuName',wintypes.LPCWSTR),('lpszClassName',wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes=(wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
user32.DefWindowProcW.restype=LRESULT
user32.RegisterClassW.argtypes=(ctypes.POINTER(WNDCLASSW),)
user32.RegisterClassW.restype=wintypes.ATOM
user32.CreateWindowExW.argtypes=(wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
                                 ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,
                                 wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,wintypes.LPVOID)
user32.CreateWindowExW.restype=wintypes.HWND
user32.ShowWindow.argtypes=(wintypes.HWND,ctypes.c_int)
user32.SetTimer.argtypes=(wintypes.HWND,ctypes.c_size_t,wintypes.UINT,ctypes.c_void_p)
user32.SetTimer.restype=ctypes.c_size_t
user32.GetMessageW.argtypes=(ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT)
user32.GetMessageW.restype=wintypes.BOOL
user32.TranslateMessage.argtypes=(ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes=(ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.restype=LRESULT
kernel32.GetModuleHandleW.argtypes=(wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype=wintypes.HMODULE
kernel32.GetModuleFileNameW.argtypes=(wintypes.HMODULE,wintypes.LPWSTR,wintypes.DWORD)
kernel32.GetModuleFileNameW.restype=wintypes.DWORD
kernel32.ExitProcess.argtypes=(wintypes.UINT,)


def publish(info,record):
    temp=info+'.tmp'
    with open(temp,'w',encoding='utf-8') as stream:json.dump(record,stream)
    os.replace(temp,info)


def run(info,code):
    def procedure(hwnd,message,wparam,lparam):
        if message==WM_CLOSE:kernel32.ExitProcess(code)
        if message==WM_TIMER:kernel32.ExitProcess(TIMED_OUT)
        return user32.DefWindowProcW(hwnd,message,wparam,lparam)
    callback=WNDPROC(procedure)
    instance=kernel32.GetModuleHandleW(None)
    window_class=WNDCLASSW(lpfnWndProc=callback,hInstance=instance,lpszClassName='AfkCloseStandIn')
    if not user32.RegisterClassW(ctypes.byref(window_class)):raise ctypes.WinError(ctypes.get_last_error())
    hwnd=user32.CreateWindowExW(WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE,'AfkCloseStandIn','AFK close stand-in',
                                WS_POPUP,-32000,-32000,1,1,None,None,instance,None)
    if not hwnd:raise ctypes.WinError(ctypes.get_last_error())
    user32.ShowWindow(hwnd,SW_SHOWNOACTIVATE)
    user32.SetTimer(hwnd,1,60000,None)
    image=ctypes.create_unicode_buffer(32768)
    kernel32.GetModuleFileNameW(None,image,len(image))
    publish(info,dict(pid=os.getpid(),image=image.value))
    message=wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message),None,0,0)>0:
        user32.TranslateMessage(ctypes.byref(message));user32.DispatchMessageW(ctypes.byref(message))


if __name__=='__main__':
    try:run(sys.argv[1],int(sys.argv[2],0))
    except Exception:  # pythonw has no console: hand the reason to the waiting test.
        publish(sys.argv[1],dict(error=traceback.format_exc()));raise
