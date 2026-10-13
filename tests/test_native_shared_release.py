import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform!='win32',reason='Windows x64 shared ownership ABI')
def test_shared_release_lifetime(tmp_path):
    compiler=shutil.which('gcc')
    if not compiler:
        pytest.skip('MinGW compiler required')
    source=Path(__file__).parents[1]/'native/windows/shared_release.c'
    dll=tmp_path/'release.dll'
    subprocess.run([compiler,'-std=c11','-shared','-O2','-Wall','-Wextra','-Werror','-static-libgcc',str(source),'-o',str(dll)],check=True,capture_output=True)
    code=r'''
import ctypes,sys
lib=ctypes.CDLL(sys.argv[1])
lib.novus_shared_release.argtypes=[ctypes.POINTER(ctypes.c_void_p)]
class Control(ctypes.Structure):
 _fields_=[('vtable',ctypes.c_void_p),('strong',ctypes.c_long),('weak',ctypes.c_long)]
events=[]
cb=ctypes.CFUNCTYPE(None,ctypes.c_void_p)
destroy=cb(lambda p:events.append('destroy'))
delete=cb(lambda p:events.append('delete'))
vtable=(ctypes.c_void_p*2)(ctypes.cast(destroy,ctypes.c_void_p),ctypes.cast(delete,ctypes.c_void_p))
for strong,weak,expected in [(2,1,[]),(1,2,['destroy']),(1,1,['destroy','delete'])]:
 events.clear()
 ctrl=Control(ctypes.addressof(vtable),strong,weak)
 pair=(ctypes.c_void_p*2)(123,ctypes.addressof(ctrl))
 lib.novus_shared_release(pair)
 assert pair[0] is None and pair[1] is None
 assert ctrl.strong==strong-1 and events==expected
 lib.novus_shared_release(pair)
 assert events==expected
pair=(ctypes.c_void_p*2)(None,None)
lib.novus_shared_release(pair)
'''
    subprocess.run([sys.executable,'-c',code,str(dll)],check=True,capture_output=True,timeout=15,
                   env={**os.environ,'PYTHONIOENCODING':'utf-8'})
