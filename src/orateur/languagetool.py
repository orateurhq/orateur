"""Optional LanguageTool HTTP server for orateur run.

By default the server runs in a Docker container, so nothing has to be installed on the
machine beyond a container runtime — LanguageTool itself needs a JRE and ~1 GB of jars.
Set `languagetool_runtime` to "native" to use a `languagetool` wrapper or jar already
installed instead.

Nothing in the speech pipeline uses this yet — `check()` / `correct()` are here so STT
output can be run through it later.
"""

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .paths import CACHE_DIR, DATA_DIR

log = logging.getLogger(__name__)

DEFAULT_PORT = 8081
DEFAULT_MAX_HEAP = "512m"  # 256m OOMs when a non-English dictionary loads.
DEFAULT_IMAGE = "erikvl87/languagetool:latest"

# Port the image listens on inside the container (not LanguageTool's usual 8081).
CONTAINER_PORT = 8010
CONTAINER_NAME = "orateur-languagetool"

# Generated for native runs; keeps the server lean (one check thread, no caches).
SERVER_PROPERTIES = CACHE_DIR / "languagetool-server.properties"

_WRAPPER_NAMES = ("languagetool-http-server", "languagetool-server", "languagetool")

_JAR_CANDIDATES = (
    DATA_DIR / "languagetool" / "languagetool-server.jar",
    Path("/usr/share/languagetool/languagetool-server.jar"),
    Path("/usr/share/java/languagetool/languagetool-server.jar"),
    Path("/opt/languagetool/languagetool-server.jar"),
)


@dataclass
class Handle:
    """What `stop_languagetool` needs to shut down whatever `start_languagetool` started."""

    kind: str  # "docker" | "native"
    container: Optional[str] = None
    runtime: Optional[str] = None  # docker/podman executable
    process: Optional[subprocess.Popen] = None


def base_url(config) -> str:
    """Base URL of the LanguageTool server (loopback only)."""
    port = int(config.get_setting("languagetool_port", DEFAULT_PORT) or DEFAULT_PORT)
    return f"http://127.0.0.1:{port}"


def is_server_running(config, *, timeout: float = 1.0) -> bool:
    """True if something answers the LanguageTool API on the configured port."""
    try:
        with urllib.request.urlopen(f"{base_url(config)}/v2/languages", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _runtime(config) -> Optional[str]:
    """Path to the container runtime, honouring `languagetool_docker_binary`."""
    explicit = config.get_setting("languagetool_docker_binary")
    if explicit:
        return shutil.which(explicit) or (explicit if Path(explicit).is_file() else None)
    for name in ("docker", "podman"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def _run(argv: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _container_state(exe: str, name: str) -> Optional[str]:
    """Container state: "running", "exists" (created/exited/paused), or None when absent."""
    try:
        r = _run([exe, "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.State}}"], timeout=20.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    state = (r.stdout or "").strip().splitlines()
    if not state:
        return None
    return "running" if state[0].strip() == "running" else "exists"


def image_present(config) -> bool:
    exe = _runtime(config)
    if not exe:
        return False
    image = str(config.get_setting("languagetool_docker_image", DEFAULT_IMAGE) or DEFAULT_IMAGE)
    try:
        r = _run([exe, "image", "inspect", image], timeout=30.0)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def pull_image(config) -> bool:
    """Download the LanguageTool image (~1 GB on first run). Streams progress to the log."""
    exe = _runtime(config)
    if not exe:
        log.error("No container runtime found — install Docker (or Podman) to use LanguageTool")
        return False
    image = str(config.get_setting("languagetool_docker_image", DEFAULT_IMAGE) or DEFAULT_IMAGE)
    log.info("Pulling %s (this can take a few minutes the first time)…", image)
    try:
        r = subprocess.run([exe, "pull", image], timeout=1800.0)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("Failed to pull %s: %s", image, e)
        return False
    if r.returncode != 0:
        log.error("Failed to pull %s (exit %s)", image, r.returncode)
        return False
    log.info("Pulled %s", image)
    return True


def _docker_run_argv(config, exe: str) -> list[str]:
    port = int(config.get_setting("languagetool_port", DEFAULT_PORT) or DEFAULT_PORT)
    heap = str(config.get_setting("languagetool_max_heap", DEFAULT_MAX_HEAP) or DEFAULT_MAX_HEAP)
    image = str(config.get_setting("languagetool_docker_image", DEFAULT_IMAGE) or DEFAULT_IMAGE)
    return [
        exe,
        "run",
        "-d",
        "--rm",
        "--name",
        CONTAINER_NAME,
        # Loopback only: never expose a proofreading service to the network.
        "-p",
        f"127.0.0.1:{port}:{CONTAINER_PORT}",
        "-e",
        "Java_Xms=64m",
        "-e",
        f"Java_Xmx={heap}",
        # HTTPServerConfig fields, prefixed with langtool_ (keeps the footprint minimal).
        "-e",
        "langtool_maxCheckThreads=1",
        "-e",
        "langtool_cacheSize=0",
        "-e",
        "langtool_pipelineCaching=false",
        image,
    ]


def _start_docker(config) -> Optional[Handle]:
    exe = _runtime(config)
    if not exe:
        log.warning("No container runtime found — install Docker (or Podman), or set languagetool_runtime=native")
        return None

    state = _container_state(exe, CONTAINER_NAME)
    if state == "running":
        log.info("LanguageTool container %s already running", CONTAINER_NAME)
        return Handle(kind="docker", container=CONTAINER_NAME, runtime=exe)
    if state == "exists":
        # Left behind by a crash (we normally run with --rm); clear it so the name is free.
        log.info("Removing stale LanguageTool container %s", CONTAINER_NAME)
        try:
            _run([exe, "rm", "-f", CONTAINER_NAME], timeout=30.0)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("Could not remove stale container: %s", e)
            return None

    if not image_present(config):
        if not pull_image(config):
            return None

    try:
        r = _run(_docker_run_argv(config, exe), timeout=120.0)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("Failed to start LanguageTool container: %s", e)
        return None
    if r.returncode != 0:
        log.warning("Failed to start LanguageTool container: %s", (r.stderr or r.stdout or "").strip())
        return None
    log.info("Started LanguageTool container %s on %s", CONTAINER_NAME, base_url(config))
    return Handle(kind="docker", container=CONTAINER_NAME, runtime=exe)


def _find_jar(config) -> Optional[Path]:
    explicit = config.get_setting("languagetool_jar")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    home = os.environ.get("LANGUAGETOOL_HOME")
    candidates = list(_JAR_CANDIDATES)
    if home:
        candidates.insert(0, Path(home) / "languagetool-server.jar")
    for p in candidates:
        if p.is_file():
            return p
    # Unpacked LanguageTool-X.Y.zip in the data dir or home
    for parent in (DATA_DIR, Path.home()):
        try:
            for d in sorted(parent.glob("LanguageTool-*"), reverse=True):
                jar = d / "languagetool-server.jar"
                if jar.is_file():
                    return jar
        except OSError:
            continue
    return None


def _write_server_properties() -> Optional[Path]:
    """Minimal-footprint server config: single check thread, no caches."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        SERVER_PROPERTIES.write_text(
            "# Generated by orateur — minimal LanguageTool server footprint.\n"
            "maxCheckThreads=1\n"
            "cacheSize=0\n"
            "pipelineCaching=false\n",
            encoding="utf-8",
        )
        return SERVER_PROPERTIES
    except OSError as e:
        log.debug("Could not write LanguageTool server properties: %s", e)
        return None


def _classpath_for_jar(jar: Path) -> str:
    """languagetool-server.jar alone is not runnable — its dependencies sit next to it.

    Distro layouts differ (Arch: /usr/share/java/languagetool/{*.jar,libs/*.jar}; the upstream
    zip keeps everything in one directory), so put the jar, its siblings and any libs/ on the
    classpath. `dir/*` is Java's own wildcard, expanded by the JVM, not the shell.
    """
    d = jar.parent
    parts = [str(jar), str(d / "*")]
    if (d / "libs").is_dir():
        parts.append(str(d / "libs" / "*"))
    return os.pathsep.join(parts)


def _with_java_home(env: dict[str, str]) -> dict[str, str]:
    """Distro wrapper scripts call "$JAVA_HOME/bin/java"; JAVA_HOME is often unset under systemd."""
    if env.get("JAVA_HOME"):
        return env
    java = shutil.which("java")
    if java:
        home = Path(java).resolve().parent.parent
        if (home / "bin" / "java").exists():
            env["JAVA_HOME"] = str(home)
    return env


def _native_argv(config) -> Optional[tuple[list[str], dict[str, str]]]:
    """Command line + environment for a locally installed server, or None when missing."""
    port = str(int(config.get_setting("languagetool_port", DEFAULT_PORT) or DEFAULT_PORT))
    heap = str(config.get_setting("languagetool_max_heap", DEFAULT_MAX_HEAP) or DEFAULT_MAX_HEAP)
    props = _write_server_properties()
    env = dict(os.environ)

    explicit_jar = bool(config.get_setting("languagetool_jar"))
    wrapper = next((shutil.which(n) for n in _WRAPPER_NAMES if shutil.which(n)), None)
    jar = _find_jar(config)

    # Prefer the distro wrapper: it knows the classpath this install needs. An explicitly
    # configured jar wins, since that is the user pointing at a specific install.
    if wrapper and not explicit_jar:
        argv = [wrapper, "--http", "--port", port]
        # The wrapper builds its own java command line; cap the heap via the JVM env.
        env["_JAVA_OPTIONS"] = f"-Xmx{heap}"
        env = _with_java_home(env)
    elif jar:
        if not shutil.which("java"):
            log.warning("Found %s but no `java` in PATH; install a JRE to use LanguageTool", jar)
            return None
        argv = [
            "java",
            f"-Xmx{heap}",
            "-cp",
            _classpath_for_jar(jar),
            "org.languagetool.server.HTTPServer",
            "--port",
            port,
        ]
    else:
        return None
    if props:
        argv += ["--config", str(props)]
    return argv, env


def _start_native(config) -> Optional[Handle]:
    resolved = _native_argv(config)
    if not resolved:
        log.warning(
            "LanguageTool not installed — install it (e.g. the `languagetool` package), set "
            "`languagetool_jar` in config.json, or use the default languagetool_runtime=docker"
        )
        return None
    argv, env = resolved
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except OSError as e:
        log.warning("Failed to start LanguageTool: %s", e)
        return None
    time.sleep(0.15)
    if proc.poll() is not None:
        log.warning("LanguageTool exited immediately (code %s)", proc.returncode)
        return None
    log.info("Started LanguageTool (pid %s) on %s", proc.pid, base_url(config))
    return Handle(kind="native", process=proc)


def start_languagetool(config, *, wait_ready: float = 0.0) -> Optional[Handle]:
    """Start the server; None if unavailable or already up.

    The first Docker start pulls a ~1 GB image, so callers that must stay responsive should
    run this off the main thread.
    """
    if is_server_running(config, timeout=0.5):
        log.info("LanguageTool already running at %s", base_url(config))
        return None

    runtime = str(config.get_setting("languagetool_runtime", "docker") or "docker").lower()
    handle = _start_native(config) if runtime == "native" else _start_docker(config)
    if handle is None:
        return None

    if wait_ready > 0:
        deadline = time.monotonic() + wait_ready
        while time.monotonic() < deadline:
            if handle.process is not None and handle.process.poll() is not None:
                log.warning("LanguageTool exited while starting (code %s)", handle.process.returncode)
                return None
            if is_server_running(config, timeout=0.5):
                log.info("LanguageTool ready at %s", base_url(config))
                return handle
            time.sleep(0.5)
        log.warning("LanguageTool did not answer within %.0fs (still starting?)", wait_ready)
    return handle


def stop_languagetool(handle: Optional[Handle], *, timeout: float = 10.0) -> None:
    if handle is None:
        return
    if handle.kind == "docker":
        if not handle.runtime or not handle.container:
            return
        try:
            _run([handle.runtime, "stop", "-t", "5", handle.container], timeout=timeout + 5)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("Could not stop LanguageTool container: %s", e)
        return
    proc = handle.process
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def check(text: str, config, *, language: Optional[str] = None, timeout: float = 10.0) -> Optional[dict[str, Any]]:
    """POST text to /v2/check. Returns the parsed response, or None if the server is unreachable."""
    lang = language or config.get_setting("languagetool_language", "auto") or "auto"
    data = urllib.parse.urlencode({"language": lang, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url(config)}/v2/check",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("LanguageTool check failed: %s", e)
        return None


def correct(text: str, config, *, language: Optional[str] = None, timeout: float = 10.0) -> str:
    """Apply the first suggestion of every match. Returns `text` unchanged on any failure."""
    result = check(text, config, language=language, timeout=timeout)
    if not result:
        return text
    matches = result.get("matches") or []
    # Apply from the end so earlier offsets stay valid.
    out = text
    for m in sorted(matches, key=lambda m: m.get("offset", 0), reverse=True):
        replacements = m.get("replacements") or []
        if not replacements:
            continue
        value = replacements[0].get("value")
        if value is None:
            continue
        offset, length = m.get("offset", 0), m.get("length", 0)
        out = out[:offset] + value + out[offset + length :]
    return out
