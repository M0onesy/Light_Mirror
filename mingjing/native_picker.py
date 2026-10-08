"""User-initiated Windows file/folder selection without an external runtime."""
from __future__ import annotations

import os
import threading

_PICKER_LOCK = threading.Lock()


def pick_path(kind: str) -> str | None:
    if kind not in {'file', 'dir'}:
        raise ValueError('路径选择类型必须为 file 或 dir')
    if os.name != 'nt':
        raise ValueError('原生选择器适用于 Windows；此平台请填写路径')
    if not _PICKER_LOCK.acquire(blocking=False):
        raise ValueError('已有路径选择窗口，请先完成或关闭该窗口')
    try:
        return _windows_pick(kind)
    finally:
        _PICKER_LOCK.release()


def _windows_pick(kind: str) -> str | None:
    import ctypes
    from ctypes import wintypes
    if kind == 'file':
        class OpenFileName(ctypes.Structure):
            _fields_ = [('lStructSize',wintypes.DWORD),('hwndOwner',wintypes.HWND),('hInstance',wintypes.HINSTANCE),
                        ('lpstrFilter',wintypes.LPCWSTR),('lpstrCustomFilter',wintypes.LPWSTR),('nMaxCustFilter',wintypes.DWORD),
                        ('nFilterIndex',wintypes.DWORD),('lpstrFile',wintypes.LPWSTR),('nMaxFile',wintypes.DWORD),
                        ('lpstrFileTitle',wintypes.LPWSTR),('nMaxFileTitle',wintypes.DWORD),('lpstrInitialDir',wintypes.LPCWSTR),
                        ('lpstrTitle',wintypes.LPCWSTR),('Flags',wintypes.DWORD),('nFileOffset',wintypes.WORD),
                        ('nFileExtension',wintypes.WORD),('lpstrDefExt',wintypes.LPCWSTR),('lCustData',ctypes.c_ssize_t),
                        ('lpfnHook',ctypes.c_void_p),('lpTemplateName',wintypes.LPCWSTR),('pvReserved',ctypes.c_void_p),
                        ('dwReserved',wintypes.DWORD),('FlagsEx',wintypes.DWORD)]
        buffer=ctypes.create_unicode_buffer(32768)
        request=OpenFileName()
        request.lStructSize=ctypes.sizeof(request)
        request.lpstrFilter='金融材料 (PDF/文本/JSON)\0*.pdf;*.md;*.txt;*.csv;*.json\0所有文件\0*.*\0\0'
        request.nFilterIndex=1
        request.lpstrFile=ctypes.cast(buffer,wintypes.LPWSTR)
        request.nMaxFile=len(buffer)
        request.lpstrTitle='明镜：选择金融材料'
        request.Flags=0x00080000|0x00001000|0x00000800|0x00000008 # Explorer, file/path exist, no cwd change
        dialog=ctypes.WinDLL('comdlg32',use_last_error=True)
        dialog.GetOpenFileNameW.argtypes=[ctypes.POINTER(OpenFileName)]
        dialog.GetOpenFileNameW.restype=wintypes.BOOL
        if dialog.GetOpenFileNameW(ctypes.byref(request)):
            return buffer.value
        error=dialog.CommDlgExtendedError()
        if error:
            raise OSError(f'Windows 文件选择器失败（{error}）')
        return None
    class BrowseInfo(ctypes.Structure):
        _fields_=[('hwndOwner',wintypes.HWND),('pidlRoot',ctypes.c_void_p),('pszDisplayName',wintypes.LPWSTR),
                  ('lpszTitle',wintypes.LPCWSTR),('ulFlags',wintypes.UINT),('lpfn',ctypes.c_void_p),
                  ('lParam',ctypes.c_ssize_t),('iImage',ctypes.c_int)]
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    ole=ctypes.OleDLL('ole32')
    ole.CoInitialize.argtypes=[ctypes.c_void_p]
    initialized=ole.CoInitialize(None) in {0,1}
    try:
        display=ctypes.create_unicode_buffer(32768)
        request=BrowseInfo()
        request.pszDisplayName=ctypes.cast(display,wintypes.LPWSTR)
        request.lpszTitle='明镜：选择外部财报／工作目录'
        request.ulFlags=0x0001|0x0040 # filesystem directories, resize/new-folder UI
        shell.SHBrowseForFolderW.argtypes=[ctypes.POINTER(BrowseInfo)]
        shell.SHBrowseForFolderW.restype=ctypes.c_void_p
        item=shell.SHBrowseForFolderW(ctypes.byref(request))
        if not item:
            return None
        try:
            path=ctypes.create_unicode_buffer(32768)
            shell.SHGetPathFromIDListW.argtypes=[ctypes.c_void_p,wintypes.LPWSTR]
            shell.SHGetPathFromIDListW.restype=wintypes.BOOL
            if not shell.SHGetPathFromIDListW(item,path):
                raise ValueError('请选择本地或映射的文件系统目录')
            return path.value
        finally:
            ole.CoTaskMemFree.argtypes=[ctypes.c_void_p]
            ole.CoTaskMemFree(item)
    finally:
        if initialized:
            ole.CoUninitialize()
