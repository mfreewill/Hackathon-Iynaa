"""Self-contained synthetic Studio browser smoke (not Meta).
Serves the unmodified app on random IPv4 loopback with the test-only
ControlsProvider (canonical grade), runs only the Studio journey suite
in one child, then requests bounded server shutdown and refuses
success if its thread is still alive. No key, model, or download."""
import argparse, ast, os, pathlib, re, signal, socket, subprocess
import sys, tempfile, threading, time

GRADE = "الصف الخامس"  # canonical grade (UI select value)
HOST, TIMEOUT_SECONDS = "127.0.0.1", 210
KEEP_ENV = ("PATH", "USERPROFILE", "HOME", "SystemRoot", "WINDIR",
            "COMSPEC", "PATHEXT", "APPDATA", "LOCALAPPDATA", "LANG")

class Refusal(Exception): pass

def _expected_test_ids(suite):
    """Return ({Class.method}, exact) parsed from the suite, not imported."""
    try:
        tree = ast.parse(suite.read_text(encoding="utf-8"))
    except Exception:
        return set(), False
    classes = {node.name: node for node in tree.body
               if isinstance(node, ast.ClassDef)}
    exact = not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "load_tests" for node in tree.body)

    def base_name(base):
        return base.attr if isinstance(base, ast.Attribute) else (
            base.id if isinstance(base, ast.Name) else "")

    def is_testcase(node, seen):
        for base in node.bases:
            name = base_name(base)
            if name == "TestCase":
                return True
            if name in classes and name not in seen:
                if is_testcase(classes[name], seen | {name}):
                    return True
        return False

    def methods(node, seen):
        found = set()
        for base in node.bases:
            name = base_name(base)
            if name in classes and name not in seen:
                found |= methods(classes[name], seen | {name})
        return found | {
            item.name for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name.startswith("test")}

    expected = set()
    for name, node in classes.items():
        names = methods(node, {name})
        unknown = [base_name(base) for base in node.bases
                   if base_name(base) not in classes
                   and base_name(base) not in ("TestCase", "object")]
        if names and unknown:
            exact = False
        if names and (is_testcase(node, {name}) or unknown):
            expected |= {f"{name}.{method}" for method in names}
    return expected, exact

def _ok_test_ids(err):
    """Ordinary verbose ok IDs as Class.method (either str(test) form)."""
    found, pending = [], None
    for line in err.splitlines():
        match = re.match(
            r"^(\w+) \(([\w.]+)\)(?:\s+\.\.\. (.*))?\s*$", line.strip())
        if match:
            method, inside, outcome = match.groups()
            parts = inside.split(".")
            cls = parts[-2] if parts[-1] == method and len(parts) >= 2 \
                else parts[-1]
            pending = f"{cls}.{method}"
            if outcome is not None:
                if outcome.strip() == "ok":
                    found.append(pending)
                pending = None
        elif pending is not None and line.strip().endswith("... ok"):
            found.append(pending)
            pending = None
        elif pending is not None and "..." in line:
            pending = None
    return found

def _child_env(repo, origin, browser, shots, tmp):
    keep = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    return {**keep, "BROWSER_BASE_URL": origin, "TEMP": tmp, "TMP": tmp,
            "BROWSER_EXECUTABLE": str(browser), "TMPDIR": tmp,
            "BROWSER_ARTIFACT_DIR": str(shots),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(
                str(repo / d) for d in ("src", "tests", "tests/browser"))}

def _stop_child(proc):
    # Best-effort group/child stop; no whole-tree guarantee is claimed.
    # Every wait is bounded. False means cleanup is incomplete.
    if proc.poll() is not None:
        return True
    for hard in (False, True):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL if hard else signal.SIGTERM)
            else:
                (proc.kill if hard else proc.terminate)()
            proc.wait(timeout=10)
            return True
        except Exception:
            if proc.poll() is not None:
                return True
    return proc.poll() is not None

def _run(args):
    if sys.version_info[:2] != (3, 11):
        raise Refusal("Python 3.11 is required")
    repo = pathlib.Path(__file__).resolve().parents[1]
    browser = pathlib.Path(args.browser).resolve()
    if not browser.is_file():
        raise Refusal("--browser must name the installed browser file")
    suite = repo / "tests/browser/test_studio_journey.py"
    if not (suite.is_file() and (repo / "src").is_dir()
            and (repo / "tests/test_http_teacher_controls.py").is_file()):
        raise Refusal("repo layout not found from tools/ location")
    artifacts = pathlib.Path(args.artifacts).resolve() if args.artifacts else None
    if artifacts is not None:
        if artifacts.exists() and (not artifacts.is_dir()
                                   or any(artifacts.iterdir())):
            raise Refusal("--artifacts must be fresh or empty")
        artifacts.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(repo / "src"), str(repo / "tests")]
    try:
        import playwright.sync_api  # noqa: F401 (child needs it)
        import uvicorn
        from teacher_demo.http_app import create_app
        from test_http_teacher_controls import ControlsProvider
    except Exception:
        raise Refusal("install requirements-dev.txt first") from None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    port = sock.getsockname()[1]
    origin = f"http://{HOST}:{port}"
    app = create_app(allowed_hosts=(HOST, f"{HOST}:{port}"),
                     allowed_origin=origin, secure_cookie=False,
                     provider=ControlsProvider(grade=GRADE))
    server = uvicorn.Server(uvicorn.Config(
        app, host=HOST, port=port, access_log=False, log_level="warning",
        proxy_headers=False, timeout_graceful_shutdown=5))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    server_shutdown_failed = False
    child_exit, count = None, 0
    child_ran = timed_out = cleanup_failed = passed = False
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise Refusal("local server did not start")
            time.sleep(0.05)
        with tempfile.TemporaryDirectory(
                dir=artifacts, ignore_cleanup_errors=True) as tmp:
            shots = artifacts or pathlib.Path(tmp, "shots")
            shots.mkdir(parents=True, exist_ok=True)
            expected_ids, exact_ids = _expected_test_ids(suite)
            command = [sys.executable, "-B", "-m", "unittest", "discover",
                       "-s", str(suite.parent), "-p", suite.name, "-v"]
            # POSIX needs a process group for killpg. On Windows the child
            # is not detached into a new process group.
            group = {"start_new_session": True} if os.name == "posix" else {}
            proc = subprocess.Popen(
                command, cwd=repo, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                env=_child_env(repo, origin, browser, shots, tmp), **group)
            timed_out = cleanup_failed = False
            try:
                output, err = proc.communicate(timeout=TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as first:
                timed_out = True
                stopped = _stop_child(proc)
                try:
                    output, err = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired as second:
                    cleanup_failed = True
                    output = second.output or first.output or ""
                    err = second.stderr or first.stderr or ""
                cleanup_failed = (cleanup_failed or not stopped
                                  or proc.poll() is None)
            output = output.decode("utf-8", "replace") \
                if isinstance(output, bytes) else output or ""
            err = err.decode("utf-8", "replace") \
                if isinstance(err, bytes) else err or ""
            print(output, end="")
            print(err, end="", file=sys.stderr)
            (shots / "studio-smoke.log").write_text(
                output + err, encoding="utf-8")
            # Verdict from stderr only, where unittest writes its report.
            ran = re.findall(r"^Ran (\d+) tests? in [\d.]+s\s*$", err, re.M)
            count = int(ran[-1]) if ran else 0
            ok_ids = _ok_test_ids(err)
            ok_short = set(ok_ids)
            lines = [line.strip() for line in err.splitlines() if line.strip()]
            plain_ok = lines[-1:] == ["OK"]
            bad_outcome = bool(re.search(
                r"\.\.\. (?:skipped|expected failure|unexpected success)|"
                r"^OK \(|(?:skipped|expected failures?)=", err, re.M))
            ids_match = (ok_short == expected_ids if exact_ids
                         else expected_ids <= ok_short)
            passed = (not timed_out and not cleanup_failed
                      and proc.returncode == 0 and count >= 1
                      and count == len(ok_ids) == len(ok_short)
                      and plain_ok and not bad_outcome and ids_match
                      and (not exact_ids or count == len(expected_ids)))
            child_exit, child_ran = proc.returncode, True
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        if thread.is_alive():
            server_shutdown_failed = True
            print("FAIL: local server thread did not stop", file=sys.stderr)
        sock.close()
    passed = passed and not server_shutdown_failed
    if child_ran:
        print(f"synthetic Studio smoke (not Meta, no model/key): "
              f"base={origin} tests={count} exit={child_exit} "
              f"timeout={timed_out} cleanup_failed={cleanup_failed} "
              f"result={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1

def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--browser", required=True,
                        help="installed Chrome/Chromium executable")
    parser.add_argument("--artifacts", help="fresh/empty artifact dir")
    try:
        return _run(parser.parse_args(argv))
    except Refusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
