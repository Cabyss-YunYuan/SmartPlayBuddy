import json
import queue
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Optional, Callable

try:
    from ..i18n import translate
    from .. import log

    logger = log.logger.getChild("Registry")

    # host.py 脚本路径
    HOST_SCRIPT = Path(__file__).parent / "host.py"

except ImportError:
    # 子进程上下文中 drivers 是顶层包，无法相对导入
    def translate(key: str, **kwargs) -> str:
        return key
    import logging
    logger = logging.getLogger("Registry")

_HEADER_FMT = "<BII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_MSG_NORMAL = 0
_MSG_BINARY = 1
_CREATION_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class DriverProcess:
    def __init__(self, cmd: list[str], name: str):
        self.name = name
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATION_NO_WINDOW,
        )
        self._ready = False
        self._recv_lock = threading.Lock()

        resp = self._recv()
        if not resp or resp.get("status") != "ready":
            self._process.kill()
            raise RuntimeError(translate("driver.failed_to_start", name=name))
        self._ready = True
        logger.info(translate("driver.started", name=name))

    def _recv(self) -> dict:
        with self._recv_lock:
            header = self._read_exact(_HEADER_SIZE)
            if not header:
                return None
            _, json_len, bin_len = struct.unpack(_HEADER_FMT, header)
            json_bytes = self._read_exact(json_len)
            if not json_bytes:
                return None
            msg = json.loads(json_bytes.decode("utf-8"))
            if bin_len > 0:
                msg["__data__"] = self._read_exact(bin_len)
            return msg

    def _send(self, msg: dict):
        json_bytes = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        header = struct.pack(_HEADER_FMT, 0, len(json_bytes), 0)
        self._process.stdin.write(header + json_bytes)
        self._process.stdin.flush()

    def _read_exact(self, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = self._process.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def operate(self, action: str, data: dict) -> dict:
        if self._process is None or self._process.poll() is not None:
            raise RuntimeError(translate("driver.process_exited", name=self.name))

        payload = json.dumps({"command": "operate", "cmd": action, "params": data}).encode("utf-8")
        header = struct.pack(_HEADER_FMT, _MSG_NORMAL, len(payload), 0)
        self._process.stdin.write(header + payload)
        self._process.stdin.flush()
        return self._recv()

    def start_stream_reader(self, callback):
        def _reader():
            while True:
                try:
                    msg = self._recv()
                    if msg and msg.get("status") == "ok" and msg.get("type") == "stream":
                        callback({
                            "Type": "stream",
                            "Action": self.name,
                            "Data": msg.get("result", {}),
                            "BinaryData": msg.get("__data__"),
                        })
                except Exception:
                    break
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        logger.info(translate("driver.stream_started", name=self.name))

    def stop(self):
        try:
            self._send({"command": "stop"})
        except Exception:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=3)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        logger.info(translate("driver.stopped", name=self.name))


class DriverRegistry:
    def __init__(self):
        self._procs: Dict[str, DriverProcess] = {}
        self._info: Dict[str, dict] = {}
        self._scanned = False
        self._driver_dirs = [Path(__file__).resolve().parent.parent / "drivers"]

    def get_callable(self, action: str) -> Optional[Callable]:
        self._ensure_scanned()
        if action in self._info:
            info = self._info[action]
            driver_name = info["manifest"]["name"]
            if self._load_driver(action):
                return lambda data: self._operate_with_recovery(driver_name, action, data)
        return None

    def _operate_with_recovery(self, driver_name, action, data, retried=False):
        proc = self._procs.get(driver_name)
        if proc is not None and proc._process.poll() is not None:
            stderr_output = proc._process.stderr.read().decode("utf-8", errors="replace")
            logger.error(translate("driver.died", name=driver_name, stderr=stderr_output))
            del self._procs[driver_name]
            proc = None
        if proc is None:
            proc = self._load_driver(action)

        try:
            return proc.operate(action, data)
        except (OSError, RuntimeError) as e:
            stderr_output = proc._process.stderr.read().decode("utf-8", errors="replace") if proc._process.poll() is not None else ""
            logger.error(translate("driver.operate_failed", name=driver_name, error=e, stderr=stderr_output))
            if retried:
                return {"status": "error", "message": str(e)}
            self._procs.pop(driver_name, None)
            return self._operate_with_recovery(driver_name, action, data, retried=True)

    def _ensure_scanned(self):
        if self._scanned:
            return
        for d in self._driver_dirs:
            for sub in d.iterdir():
                manifest_file = sub / "manifest.json"
                if not sub.is_dir() or not manifest_file.exists():
                    continue

                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)

                # 支持 "action" (字符串) 和 "actions" (数组) 两种格式
                actions = manifest.get("actions") or ([manifest["action"]] if manifest.get("action") else [])
                if not actions:
                    continue

                driver_file = sub / "driver.py"
                if not driver_file.exists():
                    continue

                if manifest.get("embedded", False):
                    continue

                packages_dir = sub / "packages"
                cmd = sys.executable + " " + str(HOST_SCRIPT) + f' "{driver_file}"'
                if packages_dir.exists():
                    cmd += f' "{packages_dir}"'

                self._install_dependencies(str(sub), manifest)

                for action in actions:
                    self._info[action] = {
                        "driver_file": driver_file,
                        "packages_dir": packages_dir if packages_dir.exists() else None,
                        "cmd": cmd,
                        "manifest": manifest,
                    }
        self._scanned = True

    _REQUIRED_FIELDS = ("appid", "versionCode", "versionName")

    def _scan_drivers_dir(self):
        drivers_dir = self._resolve_drivers_dir()
        if not drivers_dir.exists():
            return

        for entry in drivers_dir.iterdir():
            if not entry.is_dir():
                continue
            manifest_file = entry / "manifest.json"
            if not manifest_file.exists():
                continue
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                missing = [k for k in self._REQUIRED_FIELDS if k not in manifest]
                if missing:
                    logger.error(translate("driver.manifest_missing_fields", name=entry.name, fields=", ".join(missing)))
                    continue
                driver_name = manifest["name"]
                actions = manifest.get("actions", [driver_name])
                info = {
                    "path": str(entry),
                    "manifest": manifest,
                    "actions": actions,
                }
                self._info[driver_name] = info
                for act in actions:
                    if act not in self._info:
                        self._info[act] = info
                logger.debug(translate("driver.discovered", name=driver_name, path=entry))
            except Exception as e:
                logger.error(translate("driver.manifest_read_failed", name=entry.name, error=e))

    def _load_driver(self, action: str) -> DriverProcess:
        self._ensure_scanned()

        info = self._info.get(action)
        if not info:
            raise KeyError(translate("driver.action_not_found", action=action))

        manifest = info["manifest"]
        name = manifest["name"]

        if name in self._procs:
            return self._procs[name]

        cmd = info["cmd"]
        driver_file = info["driver_file"]
        packages_dir = info.get("packages_dir")

        proc = DriverProcess(cmd, name)
        self._procs[name] = proc
        return proc

    def _install_dependencies(self, driver_dir: str, manifest: dict):
        req_file = manifest.get("requirements", "requirements.txt")
        req_path = Path(driver_dir) / req_file
        packages_dir = Path(driver_dir) / "packages"

        if not req_path.exists():
            return
        if packages_dir.exists() and any(packages_dir.iterdir()):
            return

        python_exe = self._resolve_runtime_python()
        if not python_exe:
            logger.warning(translate("driver.no_python_runtime"))
            return

        packages_dir.mkdir(exist_ok=True)
        cmd = [
            str(python_exe), "-m", "pip", "install",
            "-r", str(req_path),
            "--target", str(packages_dir),
            "--quiet",
        ]
        logger.info(translate("driver.installing_dependencies", name=manifest["name"]))
        try:
            subprocess.check_call(cmd, timeout=120)
        except Exception as e:
            logger.error(translate("driver.install_dependencies_failed", error=e))

    def _build_cmd(self, driver_file: str, packages_dir: Optional[str]) -> list:
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, "--driver-host", driver_file]
        else:
            cmd = [sys.executable, "-m", "smartplaybuddy.client", "--driver-host", driver_file]
        if packages_dir:
            cmd.append(packages_dir)
        return cmd

    def _resolve_runtime_python(self) -> Optional[Path]:
        if getattr(sys, 'frozen', False):
            runtime_py = Path(sys.executable).parent / "runtime" / "python.exe"
            if runtime_py.exists():
                return runtime_py
        else:
            return Path(sys.executable)
        return None

    def _resolve_drivers_dir(self) -> Path:
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).parent / "drivers"
        else:
            return Path(__file__).resolve().parent.parent / "drivers"

    def start_stream(self, action: str, callback):
        info = self._info.get(action)
        if not info:
            return
        driver_name = info["manifest"]["name"]
        proc = self._procs.get(driver_name)
        if proc:
            proc.start_stream_reader(callback)
    
    def reload(self, name: str):
        if name in self._procs:
            self._procs[name].stop()
            del self._procs[name]
        self._scanned = False

    def shutdown(self):
        for proc in self._procs.values():
            proc.stop()
        self._procs.clear()


class DriversDict:

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    def __getitem__(self, action: str) -> Callable:
        handler = self._registry.get_callable(action)
        if handler is None:
            raise KeyError(translate("driver.not_found", action=action))
        return handler

    def __contains__(self, action: str) -> bool:
        return self._registry.get_callable(action) is not None

    def keys(self):
        self._registry._ensure_scanned()
        result = set()
        seen = set()
        for info in self._registry._info.values():
            driver_name = info["manifest"]["name"]
            if driver_name not in seen:
                seen.add(driver_name)
                result.update(info.get("actions", []))
        return result


registry = DriverRegistry()
drivers = DriversDict(registry)
