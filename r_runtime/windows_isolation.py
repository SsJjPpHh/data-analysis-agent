"""Native Windows AppContainer execution, using only the Python standard library.

An analysis gets a fresh package SID, no network capabilities, temporary SID
ACLs for its inputs/runtime/results, and a kill-on-close Job Object. ACL changes
merge with the current ACL; cleanup removes only this operation's unique SID.
The trusted package installer also uses a Job Object, without AppContainer.

Windows 10/11 is required (PROC_THREAD_ATTRIBUTE_JOB_LIST). API availability is
not native execution acceptance; tests/runtime/test_windows_r_isolation.py exercises R
on an actual Windows host. No unrestricted analysis fallback is provided.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import inspect
import os
from pathlib import Path
import stat
import subprocess
import shutil
import sys
import threading
import uuid


class WindowsRCommand(list[str]):
    """R argv plus a host-owned policy; plain Popen must never consume this."""

    isolation_backend = "windows_appcontainer"

    def __init__(self, command, *, read_paths, write_paths):
        super().__init__(command)
        self.read_paths = tuple(Path(path) for path in read_paths)
        self.write_paths = tuple(Path(path) for path in write_paths)


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", wt.LPVOID), ("Capabilities", wt.LPVOID),
                ("CapabilityCount", wt.DWORD), ("Reserved", wt.DWORD)]


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
                ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
                ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", wt.LPVOID), ("hStdInput", wt.HANDLE),
                ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class _STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFO), ("lpAttributeList", wt.LPVOID)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class _JOB_BASIC_LIMITS(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOB_EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOB_BASIC_LIMITS), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _TRUSTEE(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", wt.LPVOID), ("MultipleTrusteeOperation", wt.DWORD),
                ("TrusteeForm", wt.DWORD), ("TrusteeType", wt.DWORD), ("ptstrName", wt.LPVOID)]


class _EXPLICIT_ACCESS(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", wt.DWORD), ("grfAccessMode", wt.DWORD),
                ("grfInheritance", wt.DWORD), ("Trustee", _TRUSTEE)]


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [("AceCount", wt.DWORD), ("AclBytesInUse", wt.DWORD), ("AclBytesFree", wt.DWORD)]


_ACL_LOCK = threading.RLock()
_READ_EXECUTE = 0x1200A9
_MODIFY = 0x1301BF  # read/write/execute/delete; excludes WRITE_DAC and WRITE_OWNER
_API = None


def _bind(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


class _WindowsAPI:
    def __init__(self):
        if os.name != "nt" or sys.getwindowsversion().major < 10:
            raise OSError("Windows R isolation requires Windows 10 or Windows 11")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        userenv = ctypes.WinDLL("userenv", use_last_error=True)
        ptr = wt.LPVOID
        ptrptr = ctypes.POINTER(ptr)
        size = ctypes.c_size_t
        self.CloseHandle = _bind(kernel, "CloseHandle", wt.BOOL, wt.HANDLE)
        self.LocalFree = _bind(kernel, "LocalFree", wt.HANDLE, wt.HANDLE)
        self.FreeSid = _bind(advapi, "FreeSid", ptr, ptr)
        self.CreateProfile = _bind(userenv, "CreateAppContainerProfile", ctypes.c_long,
                                   wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ptr, wt.DWORD, ptrptr)
        self.DeleteProfile = _bind(userenv, "DeleteAppContainerProfile", ctypes.c_long, wt.LPCWSTR)
        self.GetNamedSecurityInfo = _bind(advapi, "GetNamedSecurityInfoW", wt.DWORD,
                                         wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                         ptrptr, ptrptr, ptrptr, ptrptr, ptrptr)
        self.SetNamedSecurityInfo = _bind(advapi, "SetNamedSecurityInfoW", wt.DWORD,
                                         wt.LPWSTR, wt.DWORD, wt.DWORD, ptr, ptr, ptr, ptr)
        self.SetEntriesInAcl = _bind(advapi, "SetEntriesInAclW", wt.DWORD,
                                    wt.DWORD, ctypes.POINTER(_EXPLICIT_ACCESS), ptr, ptrptr)
        self.GetAclInformation = _bind(advapi, "GetAclInformation", wt.BOOL,
                                       ptr, ptr, wt.DWORD, wt.DWORD)
        self.GetAce = _bind(advapi, "GetAce", wt.BOOL, ptr, wt.DWORD, ptrptr)
        self.EqualSid = _bind(advapi, "EqualSid", wt.BOOL, ptr, ptr)
        self.ConvertSidToStringSid = _bind(advapi, "ConvertSidToStringSidW", wt.BOOL,
                                          ptr, ctypes.POINTER(wt.LPWSTR))
        self.ConvertStringSidToSid = _bind(advapi, "ConvertStringSidToSidW", wt.BOOL,
                                          wt.LPCWSTR, ptrptr)
        self.CreateJob = _bind(kernel, "CreateJobObjectW", wt.HANDLE, ptr, wt.LPCWSTR)
        self.SetJobInformation = _bind(kernel, "SetInformationJobObject", wt.BOOL,
                                       wt.HANDLE, wt.DWORD, ptr, wt.DWORD)
        self.TerminateJob = _bind(kernel, "TerminateJobObject", wt.BOOL, wt.HANDLE, wt.UINT)
        self.InitializeAttributes = _bind(kernel, "InitializeProcThreadAttributeList", wt.BOOL,
                                          ptr, wt.DWORD, wt.DWORD, ctypes.POINTER(size))
        self.UpdateAttribute = _bind(kernel, "UpdateProcThreadAttribute", wt.BOOL,
                                     ptr, wt.DWORD, size, ptr, size, ptr, ptr)
        self.DeleteAttributes = _bind(kernel, "DeleteProcThreadAttributeList", None, ptr)
        self.CreateProcess = _bind(kernel, "CreateProcessW", wt.BOOL,
                                   wt.LPCWSTR, wt.LPWSTR, ptr, ptr, wt.BOOL,
                                   wt.DWORD, ptr, wt.LPCWSTR, ptr, ctypes.POINTER(_PROCESS_INFORMATION))


def _api():
    global _API
    if _API is None:
        _API = _WindowsAPI()
    return _API


def check_windows_isolation_api() -> None:
    """Check the API surface, without creating a profile or altering ACLs."""
    _api()


def secure_windows_temp_directory(path: Path) -> None:
    """Give a newly created scratch directory owner/SYSTEM-only permissions.

    Python before 3.13 does not translate mkdir(0700) to a private Windows DACL.
    This operation is limited to the application's new, empty scratch folder.
    """
    path = Path(path).resolve(strict=True)
    if not path.is_dir() or not path.name.startswith("rsw-r-") or any(path.iterdir()):
        raise OSError("Windows private R scratch directory must be new and empty")
    api = _api()
    owner, descriptor, system, acl = (wt.LPVOID() for _ in range(4))
    _error_code(api.GetNamedSecurityInfo(str(path), 1, 1, ctypes.byref(owner), None,
                                        None, None, ctypes.byref(descriptor)), "read scratch owner")
    try:
        _check(api.ConvertStringSidToSid("S-1-5-18", ctypes.byref(system)), "resolve SYSTEM SID")
        entries = (_EXPLICIT_ACCESS * 2)(
            _EXPLICIT_ACCESS(0x1F01FF, 1, 3, _TRUSTEE(None, 0, 0, 1, owner)),
            _EXPLICIT_ACCESS(0x1F01FF, 1, 3, _TRUSTEE(None, 0, 0, 1, system)))
        _error_code(api.SetEntriesInAcl(2, entries, None, ctypes.byref(acl)), "create private scratch ACL")
        _error_code(api.SetNamedSecurityInfo(str(path), 1, 4 | 0x80000000, None, None, acl, None),
                    "protect private scratch ACL")
    finally:
        for value in (descriptor, system, acl):
            if value:
                api.LocalFree(value)


def _check(ok, operation):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), f"Windows R isolation: {operation}")


def _error_code(code, operation):
    if code:
        raise ctypes.WinError(code, f"Windows R isolation: {operation}")


def _change_acl(path: Path, sid, *, revoke: bool = False, writable: bool = False):
    api = _api()
    old_acl = wt.LPVOID()
    descriptor = wt.LPVOID()
    new_acl = wt.LPVOID()
    with _ACL_LOCK:
        _error_code(api.GetNamedSecurityInfo(str(path), 1, 4, None, None,
                                            ctypes.byref(old_acl), None, ctypes.byref(descriptor)),
                    f"read file permissions: {path}")
        try:
            # A null DACL would already grant access to everyone; do not replace
            # it with a partial ACL or advertise it as protected user data.
            if not old_acl:
                raise OSError(f"Windows R isolation requires an ACL-protected path: {path}")
            entry = _EXPLICIT_ACCESS(
                _MODIFY if writable else _READ_EXECUTE, 4 if revoke else 1,
                3 if path.is_dir() else 0, _TRUSTEE(None, 0, 0, 5, sid))
            _error_code(api.SetEntriesInAcl(1, ctypes.byref(entry), old_acl,
                                           ctypes.byref(new_acl)), f"merge file permissions: {path}")
            _error_code(api.SetNamedSecurityInfo(str(path), 1, 4, None, None, new_acl, None),
                        f"{'remove' if revoke else 'grant'} operation permissions: {path}")
        finally:
            if new_acl:
                api.LocalFree(new_acl)
            if descriptor:
                api.LocalFree(descriptor)


def _reject_reparse_tree(root: Path):
    """Never propagate writable ACLs across existing links/junctions."""
    paths = [root]
    while paths:
        path = paths.pop()
        info = path.lstat()
        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"Windows R isolation cannot grant a linked/junction path: {path}")
        if path.is_dir():
            paths.extend(path.iterdir())


def _path_has_operation_sid(path, sid):
    api = _api()
    acl, descriptor = wt.LPVOID(), wt.LPVOID()
    _error_code(api.GetNamedSecurityInfo(str(path), 1, 4, None, None,
                                        ctypes.byref(acl), None, ctypes.byref(descriptor)), "verify grant rollback")
    try:
        size = _ACL_SIZE_INFORMATION()
        _check(api.GetAclInformation(acl, ctypes.byref(size), ctypes.sizeof(size), 2), "inspect operation ACL")
        for index in range(size.AceCount):
            ace = wt.LPVOID()
            _check(api.GetAce(acl, index, ctypes.byref(ace)), "inspect operation ACE")
            # Our grant is ACCESS_ALLOWED_ACE, with the SID after header+mask.
            if ctypes.c_ubyte.from_address(ace.value).value == 0:
                if api.EqualSid(wt.LPVOID(ace.value + 8), sid):
                    return True
        return False
    finally:
        if descriptor:
            api.LocalFree(descriptor)


class _PermissionLease:
    def __init__(self, policy: WindowsRCommand):
        self.name = f"data-analysis-agent.r.{uuid.uuid4().hex}"
        self.sid = wt.LPVOID()
        self.sid_string = ""
        self.paths: list[Path] = []
        self._closed = False
        api = _api()
        result = api.CreateProfile(self.name, "Data Analysis Agent R", "Local research analysis",
                                   None, 0, ctypes.byref(self.sid))
        if result < 0:
            raise OSError(f"Windows R isolation: CreateAppContainerProfile failed (HRESULT {result & 0xffffffff:#x})")
        try:
            sid_string = wt.LPWSTR()
            _check(api.ConvertSidToStringSid(self.sid, ctypes.byref(sid_string)), "read operation SID")
            try:
                self.sid_string = sid_string.value
            finally:
                api.LocalFree(ctypes.cast(sid_string, wt.LPVOID))
            for path in (*policy.read_paths, *policy.write_paths):
                if path == Path(path.anchor):
                    raise OSError("Windows R isolation must not grant a drive root")
                _reject_reparse_tree(path)
                writable = path in policy.write_paths
                # Keep even a partially propagated failed grant in rollback.
                self.paths.append(path)
                try:
                    _change_acl(path, self.sid, writable=writable)
                except PermissionError:
                    # System-installed R and Windows binaries normally already
                    # grant ALL APPLICATION PACKAGES read/execute. Never widen
                    # their ACLs or require elevation. The kernel still checks
                    # access at launch/use; an inaccessible installation fails.
                    if writable or not _is_system_installation(path) or _path_has_operation_sid(path, self.sid):
                        raise
                    self.paths.remove(path)
                    continue
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._closed:
            return
        self._closed = True
        api = _api()
        errors = []
        for path in reversed(self.paths):
            if not path.exists():
                continue
            try:
                _change_acl(path, self.sid, revoke=True)
            except OSError as exc:
                errors.append(str(exc))
        result = api.DeleteProfile(self.name)
        if result < 0:
            errors.append(f"DeleteAppContainerProfile HRESULT {result & 0xffffffff:#x}")
        if self.sid:
            api.FreeSid(self.sid)
            self.sid = wt.LPVOID()
        if errors:
            raise OSError("Windows R permission cleanup failed: " + "; ".join(errors))


def _is_system_installation(path):
    roots = (os.environ.get("SystemRoot"), os.environ.get("ProgramFiles"),
             os.environ.get("ProgramFiles(x86)"))
    return any(path.is_relative_to(Path(root).resolve()) for root in roots if root)


class WindowsJobProcess(subprocess.Popen):
    """Popen pipe/communication semantics with atomic native containment.

    CPython still owns pipe creation and its reader threads. The private
    _execute_child hook is bound by parameter name so 3.10/3.11/3.12's optional
    process_group parameter does not shift pipe handles.
    """

    def __init__(self, args, **kwargs):
        self._job = None
        self._permissions = None
        self._job_lock = threading.RLock()
        self._policy = args if isinstance(args, WindowsRCommand) else None
        self.isolation_sid = ""
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
        try:
            super().__init__(args, **kwargs)
        except BaseException:
            self.close_execution_resources()
            raise

    def _execute_child(self, *args, **kwargs):
        bound = inspect.signature(subprocess.Popen._execute_child).bind(self, *args, **kwargs).arguments
        command = list(bound["args"])
        if bound.get("shell") or bound.get("preexec_fn") or bound.get("pass_fds"):
            raise OSError("Windows contained execution does not support shell/preexec/pass_fds")
        api = _api()
        if self._policy is not None:
            self._permissions = _PermissionLease(self._policy)
            self.isolation_sid = self._permissions.sid_string
        self._job = api.CreateJob(None, None)
        _check(self._job, "create process-tree Job Object")
        limits = _JOB_EXTENDED_LIMITS()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway
        _check(api.SetJobInformation(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
               "configure process-tree Job Object")
        startup = _STARTUPINFOEX()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100 | 1  # USESTDHANDLES | USESHOWWINDOW
        startup.StartupInfo.wShowWindow = 0
        pipe_names = ("p2cread", "c2pwrite", "errwrite")
        pipe_handles = [int(bound[name]) for name in pipe_names]
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = pipe_handles
        inherited = (wt.HANDLE * len(set(pipe_handles)))(*dict.fromkeys(pipe_handles))
        job_handles = (wt.HANDLE * 1)(self._job)
        capabilities = _SECURITY_CAPABILITIES()
        if self._permissions is not None:
            capabilities.AppContainerSid = self._permissions.sid
            # CapabilityCount remains zero: no internet/private-network grants.
        attributes = [(0x20002, inherited), (0x2000D, job_handles)]
        if self._permissions is not None:
            attributes.append((0x20009, capabilities))
        length = ctypes.c_size_t()
        api.InitializeAttributes(None, len(attributes), 0, ctypes.byref(length))
        if not length.value:
            _check(False, "size process attributes")
        storage = ctypes.create_string_buffer(length.value)
        startup.lpAttributeList = ctypes.cast(storage, wt.LPVOID)
        _check(api.InitializeAttributes(startup.lpAttributeList, len(attributes), 0, ctypes.byref(length)),
               "initialize process attributes")
        process_info = _PROCESS_INFORMATION()
        try:
            for key, value in attributes:
                _check(api.UpdateAttribute(startup.lpAttributeList, 0, key, ctypes.byref(value),
                                           ctypes.sizeof(value), None, None), "set process containment attribute")
            environment = bound.get("env")
            if environment is not None:
                entries = []
                for name, value in sorted(environment.items(), key=lambda pair: str(pair[0]).upper()):
                    name, value = str(name), str(value)
                    if not name or "=" in name or "\0" in name or "\0" in value:
                        raise ValueError("Invalid Windows process environment")
                    entries.append(f"{name}={value}")
                environment = ctypes.create_unicode_buffer("\0".join(entries) + "\0\0")
            executable = str(bound.get("executable") or command[0])
            if not Path(executable).is_absolute():
                executable = shutil.which(executable, path=(bound.get("env") or os.environ).get("PATH"))
                if not executable:
                    raise FileNotFoundError(f"Contained executable not found: {command[0]}")
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            flags = int(bound.get("creationflags", 0)) | 0x80000 | 0x400  # EXTENDED_STARTUPINFO | UNICODE_ENV
            _check(api.CreateProcess(executable, command_line, None, None, True, flags,
                                     environment, str(bound["cwd"]) if bound.get("cwd") else None,
                                     ctypes.byref(startup), ctypes.byref(process_info)), "launch contained R process")
            self._child_created = True
            self._handle = subprocess.Handle(process_info.hProcess)
            self.pid = process_info.dwProcessId
            api.CloseHandle(process_info.hThread)
        finally:
            api.DeleteAttributes(startup.lpAttributeList)
            self._close_pipe_fds(*(bound[name] for name in
                                  ("p2cread", "p2cwrite", "c2pread", "c2pwrite", "errread", "errwrite")))

    def _communicate(self, input, endtime, orig_timeout):
        # CPython's Windows communicate reader collects stdout only at EOF.
        # Keep newline-delimited progress available while R/package installs
        # are running, including in the TimeoutExpired receipt.
        def reader(stream, buffer):
            try:
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    buffer.append(line)
            finally:
                stream.close()

        for name in ("stdout", "stderr"):
            stream = getattr(self, name)
            thread_name = f"_{name}_progress_reader"
            if stream is not None and not hasattr(self, thread_name):
                buffer = []
                setattr(self, f"_{name}_progress_buffer", buffer)
                thread = threading.Thread(target=reader, args=(stream, buffer), daemon=True)
                setattr(self, thread_name, thread)
                thread.start()
        if self.stdin and not self._communication_started:
            self._stdin_write(input)

        def contents(name):
            buffer = getattr(self, f"_{name}_progress_buffer", None)
            if buffer is None:
                return None
            empty = "" if self.text_mode else b""
            return empty.join(tuple(buffer))

        def expired():
            def byte_contents(name):
                value = contents(name)
                return value.encode(self.encoding or "utf-8", errors="replace") if isinstance(value, str) else value
            return subprocess.TimeoutExpired(self.args, orig_timeout,
                                             output=byte_contents("stdout"), stderr=byte_contents("stderr"))

        for name in ("stdout", "stderr"):
            thread = getattr(self, f"_{name}_progress_reader", None)
            if thread is not None:
                thread.join(self._remaining_time(endtime))
                if thread.is_alive():
                    raise expired()
        try:
            self.wait(timeout=self._remaining_time(endtime))
        except subprocess.TimeoutExpired as exc:
            raise expired() from exc
        return contents("stdout"), contents("stderr")

    def poll(self):
        result = super().poll()
        if result is not None:
            self._end_job()
        return result

    def _wait(self, timeout):
        result = super()._wait(timeout)
        self._end_job()
        return result

    def _end_job(self):
        with self._job_lock:
            if self._job:
                api = _api()
                job, self._job = self._job, None
                api.CloseHandle(job)

    def terminate(self):
        with self._job_lock:
            if self._job:
                _check(_api().TerminateJob(self._job, 1), "terminate process tree")
                return
        super().terminate()

    kill = terminate

    def close_execution_resources(self):
        self._end_job()
        if self._permissions is not None:
            lease, self._permissions = self._permissions, None
            lease.close()
