"""Exercise the callback DLL in a fresh process, never inside WeChat."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native callback ABI')
def test_callback_ownership_and_generations(tmp_path):
    compiler = shutil.which('gcc')
    if not compiler:
        pytest.skip('MinGW compiler required for callback ABI test')
    source = Path(__file__).parents[1] / 'native/windows/text_callback.c'
    dll = tmp_path / 'novus-text-callback.dll'
    subprocess.run([compiler, '-std=c11', '-shared', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-static-libgcc', str(source), '-o', str(dll)], check=True, capture_output=True)
    # Child exit unloads the DLL so Windows can remove the temporary directory.
    code = r'''
import ctypes, sys
lib=ctypes.CDLL(sys.argv[1])
lib.novus_callback_prepare.restype=ctypes.POINTER(ctypes.c_void_p)
lib.novus_callback_status.argtypes=[ctypes.c_long]
class Callback(ctypes.Structure):
    _fields_=[('refs',ctypes.c_long),('padding',ctypes.c_long),('invoke',ctypes.c_void_p),('destroy',ctypes.c_void_p)]
assert lib.novus_callback_version()==1
holder=lib.novus_callback_prepare()
first=lib.novus_callback_generation()
assert holder and not lib.novus_callback_prepare()
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.FreeLibrary.argtypes=[ctypes.c_void_p]
kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p]
kernel.GetModuleHandleW.restype=ctypes.c_void_p
assert kernel.FreeLibrary(lib._handle)
assert kernel.GetModuleHandleW(sys.argv[1])==lib._handle
cb=Callback.from_address(holder[0])
assert cb.refs==1
invoke=ctypes.CFUNCTYPE(None,ctypes.c_void_p,ctypes.POINTER(ctypes.c_ubyte))(cb.invoke)
destroy=ctypes.CFUNCTYPE(None,ctypes.c_void_p)(cb.destroy)
address=holder[0]
holder[0]=None # Client moves ownership out of the holder.
yes=ctypes.c_ubyte(1)
no=ctypes.c_ubyte(0)
invoke(address,ctypes.byref(yes))
assert lib.novus_callback_status(first)==21
assert not lib.novus_callback_prepare() # Completion is not final release.
invoke(address,ctypes.byref(no))
assert lib.novus_callback_status(first)==29
cb.refs-=1
assert cb.refs==0
destroy(address)
assert lib.novus_callback_status(first)==31
holder=lib.novus_callback_prepare()
second=lib.novus_callback_generation()
assert second!=first and lib.novus_callback_status(first)==-1
cb=Callback.from_address(holder[0])
invoke(holder[0],ctypes.byref(no))
assert lib.novus_callback_status(second)==17
cb.refs-=1
destroy(holder[0])
assert lib.novus_callback_status(second)==19
assert lib.novus_callback_prepare()
'''
    subprocess.run([sys.executable, '-c', code, str(dll)], check=True, capture_output=True,
                   env={**os.environ, 'PYTHONIOENCODING':'utf-8'}, timeout=15)
