"""Local acceptance metrics. No network, source paths, filenames or photo bytes."""
import ctypes,os,platform,sys

def memory():
    if os.name!='nt':return {'available':False}
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]
    class Status(ctypes.Structure):
        _fields_=[('dwLength',ctypes.c_ulong),('dwMemoryLoad',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('ullTotalPhys','ullAvailPhys','ullTotalPageFile','ullAvailPageFile','ullTotalVirtual','ullAvailVirtual','ullAvailExtendedVirtual')]
    c=Counters();c.cb=ctypes.sizeof(c);status=Status();status.dwLength=ctypes.sizeof(status)
    kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=ctypes.c_void_p
    ok=ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(kernel.GetCurrentProcess()),ctypes.byref(c),c.cb)
    status_ok=kernel.GlobalMemoryStatusEx(ctypes.byref(status))
    return {'available':bool(ok),'working_set_bytes':c.WorkingSetSize if ok else None,'process_lifetime_peak_working_set_bytes':c.PeakWorkingSetSize if ok else None,'private_bytes':c.PrivateUsage if ok else None,'available_physical_bytes':status.ullAvailPhys if status_ok else None,'total_physical_bytes':status.ullTotalPhys if status_ok else None}

def report():
    import numpy,PIL
    from PySide6.QtCore import qVersion
    import desktop_model as model,studio_store
    result={'format':'film-invert-local-acceptance','version':1,'application':model.VERSION,'release_status':'release candidate; real sample and multi-machine acceptance pending',
        'platform':{'system':platform.system(),'release':platform.release(),'machine':platform.machine(),'python':platform.python_version(),'qt':qVersion(),'numpy':numpy.__version__,'pillow':PIL.__version__},
        'engine_signature':studio_store.engine_signature(),'memory':memory(),'last_export':getattr(model.engine,'LAST_EXPORT',None),
        'scope':'Local machine metrics only; no paths, names, original hashes or photo pixels. Peak memory covers process lifetime, not only last export.'}
    loaded=model.engine.STATE.get('native_loaded')
    if loaded is not None:result['document']={'width':loaded.width,'height':loaded.height,'is_raw':loaded.is_raw,'working_space':loaded.working_space}
    return result
